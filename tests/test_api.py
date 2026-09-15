from collections.abc import Sequence
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import bars as bars_module
from app.bars import Bar
from app.features import FeatureDefinition, FeatureSpec, FeatureTable, FeatureViewSpec
from app.features.ema_20 import feature as ema_definition
from app.main import Settings, create_app
from tests.test_bars import valid, write_csv, write_workbook


def market_root(tmp_path: Path, rows: list[tuple[object, ...]]) -> Path:
    root = tmp_path / "market"
    root.mkdir()
    write_workbook(root / "EURUSD_H1_max_bars.xlsx", rows)
    return root


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

    def track_load(source_path: Path, source_timezone: str) -> object:
        loaded_paths.append(source_path)
        return bars_module.load_bars(source_path, source_timezone)

    monkeypatch.setattr("app.main.load_bars", track_load)
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
            "ema_20",
            "macd",
            "macd_histogram",
            "macd_signal",
            "rsi_14",
            "volume",
            "volume_up",
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
