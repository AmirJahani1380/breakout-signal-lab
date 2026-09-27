from collections.abc import Sequence
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import bars as bars_module
from app.bars import Bar
from app.feature_export import export_bar_features
from app.features import (
    FeatureDefinition,
    FeatureSetting,
    FeatureSpec,
    FeatureTable,
    FeatureViewSpec,
    discover,
)
from app.features.configuration import configure_features, configure_stored_features
from app.features.ema import feature as ema_definition
from app.main import Settings, create_app
from tests.test_bars import valid, write_csv, write_workbook


def market_root(tmp_path: Path, rows: list[tuple[object, ...]]) -> Path:
    root = tmp_path / "market"
    root.mkdir()
    write_workbook(root / "EURUSD_H1_max_bars.xlsx", rows)
    return root


def test_browser_responses_are_not_cached(tmp_path: Path) -> None:
    with TestClient(create_app(Settings(tmp_path))) as client:
        for path in ("/", "/static/app.js", "/static/style.css", "/api/v1/features"):
            response = client.get(path)
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-store"
        assert client.get("/api/v1/catalog").headers["cache-control"] == "no-store"


def test_catalog_lists_filenames_without_loading_bars(
    tmp_path: Path,
) -> None:
    root = tmp_path / "market"
    root.mkdir()
    malformed_source = write_csv(root / "EURUSD_H1_max_bars.csv", [("not-a-time",)], ("bad",))
    with TestClient(create_app(Settings(root))) as client:
        response = client.get("/api/v1/catalog")
    assert response.json() == {"symbols": [{"symbol": "EURUSD", "timeframes": ["H1"]}]}
    with pytest.raises(bars_module.SourceValidationError):
        bars_module.load_bars(malformed_source)


def test_selected_dataset_pages_without_loading_another_dataset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = market_root(tmp_path, [valid(100 + index) for index in range(6)])
    write_workbook(root / "GBPUSD_H1.xlsx", [valid(1_000)])
    loaded_paths: list[Path] = []
    page_requests: list[tuple[int | None, int, int, int]] = []
    original_load = bars_module.load_bars
    original_page = bars_module.BarStore.page

    def track_load(
        source_path: Path, source_timezone: str = "UTC", dataset_id: str | None = None
    ) -> bars_module.BarStore:
        loaded_paths.append(source_path)
        return original_load(source_path, source_timezone, dataset_id)

    def track_page(
        store: bars_module.BarStore,
        before: int | None,
        limit: int,
        warm_up: int,
        look_ahead: int,
    ) -> bars_module.BarPage:
        page_requests.append((before, limit, warm_up, look_ahead))
        return original_page(store, before, limit, warm_up, look_ahead)

    monkeypatch.setattr("app.main.load_bars", track_load)
    monkeypatch.setattr(bars_module.BarStore, "page", track_page)
    with TestClient(create_app(Settings(root))) as client:
        first = client.get("/api/v1/bars?symbol=EURUSD&timeframe=H1&limit=2").json()
        second = client.get(
            f"/api/v1/bars?symbol=EURUSD&timeframe=H1&limit=2&before={first['next_before']}"
        ).json()
    assert [[bar["time"] for bar in page["bars"]] for page in (first, second)] == [
        [104, 105],
        [102, 103],
    ]
    assert loaded_paths == [
        root / "EURUSD_H1_max_bars.xlsx",
        root / "EURUSD_H1_max_bars.xlsx",
    ]
    assert page_requests == [(None, 2, 100, 3), (104, 2, 100, 3)]


def test_feature_gating_bounded_window_and_fresh_source_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "market"
    root.mkdir()
    source = write_csv(
        root / "EURUSD_H1.csv",
        [valid(100 + index) for index in range(1_250)],
    )
    requests: list[tuple[int, int]] = []
    original_page = bars_module.BarStore.page

    def track_page(
        store: bars_module.BarStore,
        before: int | None,
        limit: int,
        warm_up: int,
        look_ahead: int,
    ) -> bars_module.BarPage:
        page = original_page(store, before, limit, warm_up, look_ahead)
        requests.append((len(page.bars), warm_up))
        return page

    monkeypatch.setattr(bars_module.BarStore, "page", track_page)
    with TestClient(create_app(Settings(root))) as client:
        url = "/api/v1/bars?symbol=EURUSD&timeframe=H1&features=ema_20,candle_range"
        first = client.get(url).json()
        repeated = client.get(url).json()
        disabled = client.get(
            "/api/v1/bars?symbol=EURUSD&timeframe=H1&features=candle_range"
        ).json()
        assert (
            client.get("/api/v1/bars?symbol=EURUSD&timeframe=H1&features=candle_range").json()
            == disabled
        )
        cursor_page = client.get(
            "/api/v1/bars?symbol=EURUSD&timeframe=H1&features=candle_range"
            f"&limit=200&before={first['next_before']}"
        ).json()
        client.get("/api/v1/bars?symbol=EURUSD&timeframe=H1&features=candle_range&limit=500")
        source.touch()
        reloaded = client.get(url).json()

    assert first == repeated == reloaded
    assert len(first["bars"]) == 1000
    assert requests == [
        (1100, 100),
        (1100, 100),
        (1000, 0),
        (1000, 0),
        (200, 0),
        (500, 0),
        (1100, 100),
    ]
    assert len(cursor_page["bars"]) == 200
    definitions = {entry["id"]: entry for entry in first["indicators"]}
    assert len(definitions["ema_20"]["points"]) == 1000
    assert len(definitions["candle_range"]["values"]) == 1000
    assert definitions["rsi_14"]["points"] == definitions["rsi_14"]["values"] == []
    disabled_definitions = {entry["id"]: entry for entry in disabled["indicators"]}
    assert disabled_definitions["ema_20"]["points"] == []
    assert disabled_definitions["candle_range"]["points"] == []
    assert len(disabled_definitions["candle_range"]["values"]) == 1000


def test_missing_root_invalid_selection_and_malformed_selection_are_actionable(
    tmp_path: Path,
) -> None:
    with TestClient(create_app(Settings(tmp_path / "gone"))) as client:
        assert client.get("/api/v1/catalog").status_code == 503
    root = market_root(tmp_path, [])
    with TestClient(create_app(Settings(root))) as client:
        response = client.get("/api/v1/bars?symbol=EURUSD&timeframe=M5")
    assert response.status_code == 422 and "not available" in response.json()["detail"]

    write_workbook(root / "EURUSD_M5.xlsx", [valid(1)], ("time", "open"))
    with TestClient(create_app(Settings(root))) as client:
        response = client.get("/api/v1/bars?symbol=EURUSD&timeframe=M5")
    assert response.status_code == 422 and "missing" in response.json()["detail"]


@pytest.mark.parametrize(
    ("headers", "rows", "message"),
    [
        (
            ("time", "open", "high", "low", "close", "close", "volume"),
            [(1, 1, 3, 0, 2, 2, 10)],
            "duplicate column names",
        ),
        (
            ("time", "open", "high", "low", "close", "volume"),
            [valid(1), (2, 1, 3, 0, 2)],
            "CSV row 3: expected 6 fields, found 5",
        ),
    ],
)
def test_paged_api_rejects_malformed_csv_logical_records(
    tmp_path: Path,
    headers: tuple[str, ...],
    rows: list[tuple[object, ...]],
    message: str,
) -> None:
    root = tmp_path / "market"
    root.mkdir()
    write_csv(root / "EURUSD_H1.csv", rows, headers)

    with TestClient(create_app(Settings(root))) as client:
        response = client.get("/api/v1/bars?symbol=EURUSD&timeframe=H1&features=candle_range")

    assert response.status_code == 422
    assert message in response.json()["detail"]


def test_unselected_malformed_parquet_does_not_block_valid_dataset(tmp_path: Path) -> None:
    root = tmp_path / "market"
    root.mkdir()
    write_csv(root / "EURUSD_H1.csv", [valid(1), valid(2)])
    (root / "BROKEN_H1.parquet").write_bytes(b"not parquet")

    with TestClient(create_app(Settings(root))) as client:
        catalog = client.get("/api/v1/catalog")
        valid_response = client.get("/api/v1/bars?symbol=EURUSD&timeframe=H1")
        malformed_response = client.get("/api/v1/bars?symbol=BROKEN&timeframe=H1")

    assert catalog.status_code == 200
    assert valid_response.status_code == 200
    assert malformed_response.status_code == 422
    assert "cannot read Parquet file" in malformed_response.json()["detail"]


def test_parquet_source_change_read_error_returns_422(tmp_path: Path) -> None:
    root = tmp_path / "market"
    root.mkdir()
    source = root / "EURUSD_H1.parquet"
    pd.DataFrame(
        [valid(100 + index) for index in range(10)],
        columns=("time", "open", "high", "low", "close", "volume"),
    ).to_parquet(source, index=False)

    with TestClient(create_app(Settings(root))) as client:
        assert client.get("/api/v1/bars?symbol=EURUSD&timeframe=H1").status_code == 200
        source.write_bytes(b"changed and invalid parquet")
        response = client.get("/api/v1/bars?symbol=EURUSD&timeframe=H1")

    assert response.status_code == 422
    assert "cannot read Parquet file" in response.json()["detail"]


def test_bars_include_backend_calculated_indicators(tmp_path: Path) -> None:
    rows = [(100 + index, 1, 40, 0, float(index + 1), 10) for index in range(30)]
    root = market_root(tmp_path, rows)
    with TestClient(create_app(Settings(root))) as client:
        latest = client.get("/api/v1/bars?symbol=EURUSD&timeframe=H1&limit=2").json()
        earlier = client.get(
            f"/api/v1/bars?symbol=EURUSD&timeframe=H1&limit=2&before={latest['next_before']}"
        ).json()
    assert [[bar["time"] for bar in page["bars"]] for page in (latest, earlier)] == [
        [128, 129],
        [126, 127],
    ]
    for payload in (latest, earlier):
        definitions = {entry["id"]: entry for entry in payload["indicators"]}
        assert set(definitions) == {
            "atr_20",
            "atr_20_pane",
            "atr_20_to_close",
            "body_size",
            "body_size_to_atr_20",
            "body_size_to_close",
            "body_to_range_ratio",
            "candle_direction",
            "candle_range",
            "candle_range_to_atr_20",
            "candle_range_to_close",
            "confirmed_swing_3_3",
            "donchian_upper_20",
            "donchian_lower_20",
            "donchian_middle_20",
            "ema_20",
            "ema_distance_20_atr_20",
            "ema_slope_20_20_atr_20",
            "is_engulfing",
            "lower_wick_size",
            "lower_wick_to_atr_20",
            "lower_wick_to_close",
            "macd_12_26_9",
            "macd_histogram_12_26_9",
            "macd_signal_12_26_9",
            "rsi_14",
            "rolling_overlap_20",
            "volume",
            "volume_up",
            "upper_wick_size",
            "upper_wick_to_atr_20",
            "upper_wick_to_close",
        }
        assert [point["value"] for point in definitions["rsi_14"]["points"]] == [100, 100]
        assert definitions["rsi_14"]["scale_range"] == [0, 100]
        assert [line["price"] for line in definitions["rsi_14"]["reference_lines"]] == [
            70,
            50,
            30,
        ]
        assert definitions["volume"]["default_applied"] is True
        assert all(isinstance(point["value"], float) for point in definitions["ema_20"]["points"])


def test_confirmed_swing_pivot_survives_page_boundary(tmp_path: Path) -> None:
    closes = [6, 7, 8, 9, 15, 8, 7, 6, 5, 4]
    root = tmp_path / "market"
    root.mkdir()
    write_csv(
        root / "PIVOT_H1.csv",
        [
            (100 + index, close, close + 1, close - 1, close, 1)
            for index, close in enumerate(closes)
        ],
    )
    with TestClient(create_app(Settings(root))) as client:
        latest = client.get(
            "/api/v1/bars?symbol=PIVOT&timeframe=H1&limit=5&features=confirmed_swing_3_3"
        ).json()
        older = client.get(
            "/api/v1/bars",
            params={
                "symbol": "PIVOT",
                "timeframe": "H1",
                "limit": 5,
                "before": latest["next_before"],
                "features": "confirmed_swing_3_3",
            },
        ).json()
    assert [bar["time"] for bar in latest["bars"]] == [105, 106, 107, 108, 109]
    assert [bar["time"] for bar in older["bars"]] == [100, 101, 102, 103, 104]
    swing = next(view for view in older["indicators"] if view["id"] == "confirmed_swing_3_3")
    assert swing["points"] == [{"time": 104, "value": 16.0}]


def test_non_finite_feature_is_skipped_without_breaking_valid_features(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    spec = FeatureSpec("bad_score", "Float64")

    def non_finite(bars: Sequence[Bar]) -> FeatureTable:
        table = FeatureTable.from_columns(
            (spec,), [bar.time for bar in bars], {spec.name: [1.0] * len(bars)}
        )
        table.frame.iloc[-1, 0] = float("inf")
        return table

    bad_definition = FeatureDefinition(
        (spec,),
        non_finite,
        (FeatureViewSpec("bad_score", spec.name, "Bad", "Invalid output", "line"),),
    )
    monkeypatch.setattr("app.main.discover", lambda: (ema_definition, bad_definition))
    caplog.set_level("WARNING", logger="app.features")
    root = market_root(tmp_path, [valid(1), valid(2)])

    with TestClient(create_app(Settings(root))) as client:
        response = client.get("/api/v1/bars?symbol=EURUSD&timeframe=H1")

    assert response.status_code == 200
    assert [definition["id"] for definition in response.json()["indicators"]] == ["ema_20"]
    assert "bad_score values must be finite or null" in caplog.text


def test_period_settings_flow_to_catalog_chart_and_export(tmp_path: Path) -> None:
    import csv
    import json
    from io import BytesIO
    from zipfile import ZipFile

    root = tmp_path / "market"
    root.mkdir()
    rows = []
    for index in range(150):
        close = 100 + index * 0.13 + (index % 7) * 0.8
        open_price = close - (1 if index % 3 else -0.5)
        rows.append(
            (
                100 + index,
                open_price,
                max(open_price, close) + 1,
                min(open_price, close) - 1,
                close,
                index + 1,
            )
        )
    write_csv(root / "EURUSD_H1.csv", rows)
    settings = {
        "atr_period": 10,
        "rsi_period": 7,
        "ema_period": 9,
        "rolling_overlap_period": 12,
        "macd_fast_period": 5,
        "macd_slow_period": 34,
        "macd_signal_period": 6,
    }
    selected = [
        "atr_10",
        "candle_range_to_atr_10",
        "rsi_7",
        "ema_9",
        "rolling_overlap_12",
        "macd_histogram_5_34_6",
    ]
    with TestClient(create_app(Settings(root))) as client:
        catalog = client.get(
            "/api/v1/features",
            params=settings,
        ).json()
        bars_payload = client.get(
            "/api/v1/bars",
            params={
                "symbol": "EURUSD",
                "timeframe": "H1",
                **settings,
                "features": ",".join(selected),
            },
        ).json()
        export = client.get(
            "/api/v1/export",
            params={
                "symbol": "EURUSD",
                "timeframe": "H1",
                "features": ",".join(selected),
                "format": "csv",
                **settings,
            },
        )
    offered = {feature["name"]: feature for feature in catalog["export_features"]}
    assert offered["atr_10"]["parameters"] == {"period": 10}
    assert offered["candle_range_to_atr_10"]["parameters"] == {"atr_period": 10}
    assert offered["rolling_overlap_12"]["parameters"] == {"period": 12}
    assert offered["macd_5_34_6"]["parameters"] == {
        "fast_period": 5,
        "slow_period": 34,
        "signal_period": 6,
    }
    views = {view["id"]: view for view in bars_payload["indicators"]}
    assert views["atr_10"]["label"] == "ATR 10"
    assert views["ema_9"]["label"] == "EMA 9"
    assert views["rsi_7"]["label"] == "RSI 7"
    assert [setting["key"] for setting in views["macd_5_34_6"]["settings"]] == [
        "macd_fast_period",
        "macd_slow_period",
        "macd_signal_period",
    ]
    assert views["candle_range_to_close"]["settings"] == []
    assert export.status_code == 200
    with ZipFile(BytesIO(export.content)) as archive:
        table = next(name for name in archive.namelist() if name.endswith(".csv"))
        metadata_name = next(name for name in archive.namelist() if name.endswith(".json"))
        exported = list(csv.DictReader(archive.read(table).decode().splitlines()))
        metadata = json.loads(archive.read(metadata_name))
    canonical = ["asset", "timeframe", "time", "open", "high", "low", "close", "volume"]
    assert list(exported[0]) == canonical + selected
    assert len(exported) == len(rows) - metadata["export_warm_up_rows"]
    assert metadata["export_warm_up_rows"] == 100
    assert [feature["name"] for feature in metadata["features"]] == selected
    assert "atr_20" not in exported[0] and "ema_20" not in exported[0]
    api_views = {name: views[name] for name in selected}
    export_time = int(exported[0]["time"])
    for name in selected:
        points = api_views[name]["points"] or api_views[name]["values"]
        api_value = next(point["value"] for point in points if point["time"] == export_time)
        assert float(exported[0][name]) == pytest.approx(api_value)


def test_period_settings_keep_custom_registered_feature_definitions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def custom_definition(name: str, parameters: dict[str, int]) -> FeatureDefinition:
        spec = FeatureSpec(name, "Float64", parameters)

        def calculate(bars: Sequence[Bar]) -> FeatureTable:
            values: list[float | None] = [bar.close for bar in bars]
            return FeatureTable.from_columns((spec,), [bar.time for bar in bars], {name: values})

        return FeatureDefinition(
            (spec,),
            calculate,
            (FeatureViewSpec(name, name, name, "Custom registered feature", None),),
        )

    custom_macd = custom_definition(
        "macd_divergence", {"fast_period": 3, "slow_period": 7, "signal_period": 2}
    )
    custom_atr = custom_definition("custom_atr_ratio", {"atr_period": 20})
    registered = discover() + (custom_macd, custom_atr)
    monkeypatch.setattr("app.main.discover", lambda: registered)
    root = market_root(tmp_path, [valid(1), valid(2)])

    with TestClient(create_app(Settings(root))) as client:
        response = client.get(
            "/api/v1/features",
            params={
                "atr_period": 10,
                "macd_fast_period": 5,
                "macd_slow_period": 34,
                "macd_signal_period": 6,
            },
        )

    assert response.status_code == 200
    payload = response.json()
    view_ids = [view["id"] for view in payload["indicators"]]
    export_names = [feature["name"] for feature in payload["export_features"]]
    assert view_ids.count("macd_divergence") == 1
    assert "custom_atr_ratio" in view_ids
    assert "macd_5_34_6" in view_ids
    assert len(view_ids) == len(set(view_ids))
    assert export_names.count("macd_divergence") == 1
    assert "custom_atr_ratio" in export_names
    assert "macd_5_34_6" in export_names


def test_new_configurable_feature_needs_no_api_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from io import BytesIO
    from zipfile import ZipFile

    def custom_feature(period: int = 3) -> FeatureDefinition:
        name = f"custom_score_{period}"
        spec = FeatureSpec(
            name,
            "Float64",
            {"window": period},
            warm_up=period - 1,
            selection_key="custom_score",
        )

        def calculate(source: Sequence[Bar]) -> FeatureTable:
            values = [None if index < period - 1 else float(period) for index in range(len(source))]
            return FeatureTable.from_columns((spec,), [bar.time for bar in source], {name: values})

        return FeatureDefinition(
            (spec,),
            calculate,
            (
                FeatureViewSpec(
                    name,
                    name,
                    "Custom score",
                    "Configurable test indicator",
                    None,
                    selection_key="custom_score",
                ),
            ),
            calculation_warm_up=period,
            settings=(
                FeatureSetting("custom_period", "Custom period", 3, 2, 10, parameters=("window",)),
            ),
            configure=lambda values: custom_feature(values["custom_period"]),
        )

    monkeypatch.setattr("app.main.discover", lambda: discover() + (custom_feature(),))
    root = market_root(tmp_path, [valid(100 + index) for index in range(120)])
    with TestClient(create_app(Settings(root))) as client:
        catalog = client.get("/api/v1/features", params={"custom_period": 5}).json()
        chart = client.get(
            "/api/v1/bars",
            params={
                "symbol": "EURUSD",
                "timeframe": "H1",
                "custom_period": 5,
                "features": "custom_score_5",
            },
        ).json()
        export = client.get(
            "/api/v1/export",
            params={
                "symbol": "EURUSD",
                "timeframe": "H1",
                "custom_period": 5,
                "features": "custom_score_5",
                "format": "csv",
            },
        )
        invalid = client.get("/api/v1/features", params={"custom_period": 11})

    assert {setting["key"] for setting in catalog["settings"]} >= {"custom_period"}
    offered = {entry["name"]: entry for entry in catalog["export_features"]}
    assert offered["custom_score_5"]["selection_key"] == "custom_score"
    assert "custom_score_3" not in offered
    view = next(entry for entry in chart["indicators"] if entry["id"] == "custom_score_5")
    assert view["selection_key"] == "custom_score"
    assert view["values"][-1]["value"] == 5.0
    assert export.status_code == 200
    with ZipFile(BytesIO(export.content)) as archive:
        assert "custom_score_5" in archive.read("bar_features.csv").decode().splitlines()[0]
    restored = configure_stored_features(
        (custom_feature(),),
        (FeatureSpec("custom_score_5", "Float64", {"window": 5}, selection_key="custom_score"),),
    )
    assert restored[0].specs[0].name == "custom_score_5"
    assert invalid.status_code == 422 and "custom_period" in invalid.text


def test_chart_loads_enough_history_for_selected_long_period(tmp_path: Path) -> None:
    root = tmp_path / "market"
    root.mkdir()
    rows = [
        (100 + index, 100 + index, 102 + index, 99 + index, 101 + index, 1) for index in range(160)
    ]
    write_csv(root / "EURUSD_H1.csv", rows)
    with TestClient(create_app(Settings(root))) as client:
        response = client.get(
            "/api/v1/bars",
            params={
                "symbol": "EURUSD",
                "timeframe": "H1",
                "limit": 1,
                "atr_period": 120,
                "rsi_period": 120,
                "features": "atr_120,rsi_120",
            },
        )
    assert response.status_code == 200
    definitions = {feature["id"]: feature for feature in response.json()["indicators"]}
    assert len(response.json()["bars"]) == 1
    assert len(definitions["atr_120"]["values"]) == 1
    assert definitions["atr_120"]["values"][0]["value"] == pytest.approx(3.0)
    assert definitions["rsi_120"]["values"][0]["value"] == 100


def test_period_query_validation_returns_actionable_errors(tmp_path: Path) -> None:
    root = tmp_path / "market"
    root.mkdir()
    write_csv(root / "EURUSD_H1.csv", [valid(100)])
    with TestClient(create_app(Settings(root))) as client:
        invalid_atr = client.get("/api/v1/features", params={"atr_period": 0})
        invalid_macd = client.get(
            "/api/v1/features",
            params={"macd_fast_period": 26, "macd_slow_period": 12},
        )
    assert invalid_atr.status_code == 422
    assert "atr_period" in invalid_atr.text
    assert invalid_macd.status_code == 422
    assert "fast period must be below slow period" in invalid_macd.json()["detail"]


def test_stored_chart_uses_matching_period_specific_indicator_views(tmp_path: Path) -> None:
    source_root = tmp_path / "market"
    source_root.mkdir()
    source = write_csv(
        source_root / "EURUSD_H1.csv",
        [
            (100 + index, 100 + index, 102 + index, 99 + index, 101 + index, 1)
            for index in range(130)
        ],
    )
    stored_root = tmp_path / "stored"
    definitions = configure_features(
        discover(),
        {"atr_period": 10, "macd_fast_period": 5, "macd_slow_period": 8, "macd_signal_period": 3},
    )
    export_bar_features(
        source,
        stored_root,
        "EURUSD",
        "H1",
        [
            "atr_10",
            "candle_range_to_atr_10",
            "macd_5_8_3",
            "macd_signal_5_8_3",
            "macd_histogram_5_8_3",
        ],
        "csv",
        definitions=definitions,
    )
    with TestClient(create_app(Settings(source_root, stored_data_root=stored_root))) as client:
        response = client.get(
            "/api/v1/bars",
            params={"mode": "stored", "symbol": "EURUSD", "timeframe": "H1"},
        )

    assert response.status_code == 200
    payload = response.json()
    indicators = {indicator["id"]: indicator for indicator in payload["indicators"]}
    assert "atr_10" in indicators
    assert "atr_10_pane" in indicators
    assert indicators["atr_10"]["label"] == "ATR 10"
    assert indicators["atr_10"]["values"] == payload["stored_values"]["atr_10"]
    for name in (
        "candle_range_to_atr_10",
        "macd_5_8_3",
        "macd_signal_5_8_3",
        "macd_histogram_5_8_3",
    ):
        assert indicators[name]["values"] == payload["stored_values"][name]
