from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.bars import Bar
from app.feature_export import FeatureExportError, build_feature_frame, export_bar_features
from app.features import FeatureDefinition, FeatureSpec, FeatureTable, discover
from app.features.atr import atr_values
from app.features.configuration import configure_features
from app.features.ema import calculate as calculate_ema
from app.features.rsi import calculate as calculate_rsi
from app.main import Settings, create_app
from tests.test_bars import write_csv


def bars(count: int) -> list[Bar]:
    return [Bar(100 + index, 10, 20, 9, 10 + index / 10, 20) for index in range(count)]


def test_incremental_recursive_values_match_existing_calculations() -> None:
    source = bars(100)
    frame, _ = build_feature_frame(
        source, "EURUSD", "H1", ["ema_20", "atr_20", "rsi_14", "rolling_overlap_20"]
    )
    for name, expected in (
        ("ema_20", calculate_ema(source).frame["ema_20"]),
        ("atr_20", pd.Series(atr_values(source), dtype="Float64")),
        ("rsi_14", calculate_rsi(source).frame["rsi_14"].reset_index(drop=True)),
    ):
        pd.testing.assert_series_equal(
            frame[name].reset_index(drop=True),
            expected.reset_index(drop=True),
            check_names=False,
            rtol=1e-12,
            atol=1e-12,
        )
    assert frame.loc[0, "rolling_overlap_20"] is pd.NA
    assert frame.loc[20, "rolling_overlap_20"] is not pd.NA


def test_every_builtin_feature_matches_full_history_calculation() -> None:
    source = bars(80)
    definitions = discover()
    names = [spec.name for definition in definitions for spec in definition.specs]
    frame, _ = build_feature_frame(source, "EURUSD", "H1", names, definitions)
    for definition in definitions:
        expected = definition.calculate(source).frame.reset_index(drop=True)
        for name in expected.columns:
            pd.testing.assert_series_equal(
                frame[name].reset_index(drop=True),
                expected[name],
                check_names=False,
                check_dtype=name != "volume",
                rtol=1e-12,
                atol=1e-12,
            )


def test_export_round_trips_metadata_and_refuses_overwrite(tmp_path: Path) -> None:
    source = write_csv(
        tmp_path / "EURUSD_H1.csv",
        [
            (bar.time, bar.open, max(bar.high, bar.close), bar.low, bar.close, bar.volume)
            for bar in bars(130)
        ],
    )
    frames = []
    for format in ("csv", "parquet"):
        output = tmp_path / format
        table, sidecar = export_bar_features(
            source, output, "EURUSD", "H1", ["atr_20", "volume_up", "candle_direction"], format
        )
        assert table.name == f"bar_features.{format}"
        metadata = json.loads(sidecar.read_text())
        assert metadata["dataset_id"] == "EURUSD/H1"
        assert len(metadata["source_sha256"]) == 64
        assert metadata["dtypes"]["volume_up"] == "boolean"
        assert metadata["export_warm_up_rows"] == 100
        assert [feature["name"] for feature in metadata["features"]] == [
            "atr_20",
            "volume_up",
            "candle_direction",
        ]
        frame = (
            pd.read_csv(table, dtype=metadata["dtypes"])
            if format == "csv"
            else pd.read_parquet(table)
        )
        assert {column: str(dtype) for column, dtype in frame.dtypes.items()} == metadata["dtypes"]
        frames.append(frame)
        with pytest.raises(FileExistsError):
            export_bar_features(source, output, "EURUSD", "H1", ["atr_20"], format)
    pd.testing.assert_frame_equal(frames[0], frames[1], rtol=1e-12, atol=1e-12)
    assert len(frames[0]) == 30
    assert frames[0]["atr_20"].notna().all()
    assert frames[0]["time"].tolist() == list(range(200, 230))
    second, _ = export_bar_features(
        source,
        tmp_path / "repeat",
        "EURUSD",
        "H1",
        ["atr_20", "volume_up", "candle_direction"],
        "csv",
    )
    assert second.read_bytes() == (tmp_path / "csv" / "bar_features.csv").read_bytes()


def test_missing_failure_and_empty_dataset(tmp_path: Path) -> None:
    with pytest.raises(FeatureExportError, match="unknown feature"):
        build_feature_frame(bars(1), "EURUSD", "H1", ["missing"])
    empty, _ = build_feature_frame([], "EURUSD", "H1", ["atr_20"])
    assert empty.empty and list(empty.columns)[-1] == "atr_20"

    spec = FeatureSpec("custom", "Float64")

    def calculate_custom(source: object) -> FeatureTable:
        assert isinstance(source, Sequence)
        return FeatureTable.from_columns((spec,), [bar.time for bar in source], {"custom": [1.25]})

    definition = FeatureDefinition((spec,), calculate_custom, ())
    frame, _ = build_feature_frame(bars(1), "EURUSD", "H1", ["custom"], (definition,))
    assert frame.loc[0, "custom"] == 1.25

    def fail(_bars: object) -> FeatureTable:
        raise RuntimeError("calculation broke")

    definition = FeatureDefinition((spec,), fail, ())
    with pytest.raises(FeatureExportError, match="custom"):
        build_feature_frame(bars(1), "EURUSD", "H1", ["custom"], (definition,))


def test_api_download_contains_table_and_metadata(tmp_path: Path) -> None:
    source_root = tmp_path / "market"
    source_root.mkdir()
    write_csv(source_root / "EURUSD_H1.csv", [(100, 1, 3, 0, 2, 10)])
    with TestClient(create_app(Settings(source_root))) as client:
        response = client.get(
            "/api/v1/export?symbol=EURUSD&timeframe=H1&features=candle_range&format=csv"
        )
    assert response.status_code == 200
    from io import BytesIO
    from zipfile import ZipFile

    with ZipFile(BytesIO(response.content)) as archive:
        assert set(archive.namelist()) == {"bar_features.csv", "bar_features.csv.json"}
        assert "candle_range" in archive.read("bar_features.csv").decode()


def test_api_rejects_wrong_calculator_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = tmp_path / "market"
    source_root.mkdir()
    write_csv(source_root / "EURUSD_H1.csv", [(100, 1, 3, 0, 2, 10)])
    registered = FeatureSpec("custom_score", "Int64")
    returned = FeatureSpec("custom_score", "Float64")

    def wrong_spec(source: object) -> FeatureTable:
        assert isinstance(source, tuple)
        return FeatureTable.from_columns(
            (returned,), [bar.time for bar in source], {"custom_score": [1.5]}
        )

    monkeypatch.setattr(
        "app.main.discover", lambda: (FeatureDefinition((registered,), wrong_spec, ()),)
    )
    with TestClient(create_app(Settings(source_root))) as client:
        response = client.get(
            "/api/v1/export?symbol=EURUSD&timeframe=H1&features=custom_score&format=csv"
        )
    assert response.status_code == 422
    assert "do not match the registered definition" in response.json()["detail"]


def test_supplied_builtin_name_cannot_mislabel_calculation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = tmp_path / "market"
    source_root.mkdir()
    write_csv(source_root / "EURUSD_H1.csv", [(100, 1, 3, 0, 2, 10)])
    conflicting = FeatureSpec("ema_20", "Float64", {"period": 50}, version="2")

    def custom_calculate(source: object) -> FeatureTable:
        raise AssertionError("conflicting calculator must not run")

    definition = FeatureDefinition((conflicting,), custom_calculate, ())
    with pytest.raises(FeatureExportError, match="does not match the bundled"):
        build_feature_frame(bars(1), "EURUSD", "H1", ["ema_20"], (definition,))
    monkeypatch.setattr("app.main.discover", lambda: (definition,))
    with TestClient(create_app(Settings(source_root))) as client:
        response = client.get(
            "/api/v1/export?symbol=EURUSD&timeframe=H1&features=ema_20&format=csv"
        )
    assert response.status_code == 422
    assert "does not match the bundled" in response.json()["detail"]


def test_supplied_builtin_same_spec_cannot_replace_its_calculator() -> None:
    ema_definition = next(
        definition
        for definition in discover()
        if any(spec.name == "ema_20" for spec in definition.specs)
    )
    spec = next(spec for spec in ema_definition.specs if spec.name == "ema_20")

    def different_calculate(source: list[Bar]) -> FeatureTable:
        return FeatureTable.from_columns(
            (spec,),
            [bar.time for bar in source],
            {spec.name: [bar.close + 100 for bar in source]},
        )

    definition = FeatureDefinition(
        (spec,),
        different_calculate,
        (),
        settings=ema_definition.settings,
        configure=ema_definition.configure,
    )
    with pytest.raises(FeatureExportError, match="does not match the bundled"):
        build_feature_frame(bars(3), "EURUSD", "H1", ["ema_20"], (definition,))


def test_dynamic_export_rejects_misaligned_feature_table() -> None:
    spec = FeatureSpec("atr_10", "Float64", {"period": 10})

    def misaligned_calculate(source: list[Bar]) -> FeatureTable:
        return FeatureTable.from_columns(
            (spec,),
            [bar.time + 1 for bar in source],
            {spec.name: [1.0 for _ in source]},
        )

    definition = FeatureDefinition((spec,), misaligned_calculate, ())
    with pytest.raises(FeatureExportError, match="timestamp-aligned FeatureTable"):
        build_feature_frame(bars(3), "EURUSD", "H1", ["atr_10"], (definition,))


def test_selected_periods_drive_export_values_and_column_contract() -> None:
    source = bars(150)
    definitions = configure_features(
        discover(),
        {
            "atr_period": 10,
            "rsi_period": 7,
            "ema_period": 9,
            "rolling_overlap_period": 12,
            "macd_fast_period": 5,
            "macd_slow_period": 34,
            "macd_signal_period": 6,
        },
    )
    frame, specs = build_feature_frame(
        source,
        "EURUSD",
        "H1",
        ["atr_10", "candle_range_to_atr_10", "rsi_7", "macd_histogram_5_34_6"],
        definitions,
    )
    contracts = {spec.name: spec.parameters for spec in specs}
    assert contracts == {
        "atr_10": {"period": 10},
        "candle_range_to_atr_10": {"atr_period": 10},
        "rsi_7": {"period": 7},
        "macd_histogram_5_34_6": {"fast_period": 5, "slow_period": 34, "signal_period": 6},
    }
    assert frame["atr_10"].iloc[9] == pytest.approx(11.0)
    assert frame["atr_10"].iloc[19] == pytest.approx(11.0)
    assert frame["candle_range_to_atr_10"].iloc[19] == pytest.approx(1.0)
    assert frame["rsi_7"].iloc[:7].isna().all()
    assert frame["rsi_7"].iloc[7] == 100
