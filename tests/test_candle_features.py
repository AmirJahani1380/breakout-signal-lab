import csv
from pathlib import Path

import pandas as pd
import pytest

from app.bars import Bar, BarStore, SourceValidationError, load_bars
from app.features import calculate
from app.features.body_size import feature as body_size_feature
from app.features.body_to_range_ratio import feature as ratio_feature
from app.features.candle_direction import feature as direction_feature
from app.features.candle_range import feature as range_feature
from app.features.comparison import (
    FeatureColumn,
    compare_feature_columns,
    computed_feature_column,
    imported_feature_column,
)
from app.features.is_engulfing import feature as engulfing_feature
from app.features.lower_wick_size import feature as lower_wick_feature
from app.features.upper_wick_size import feature as upper_wick_feature


def test_csv_and_parquet_share_canonical_validation_and_imported_features(
    tmp_path: Path,
) -> None:
    source = pd.DataFrame(
        {
            "time": ["2025-01-01T00:00:00Z", "2025-01-01T00:01:00Z"],
            "open": [2, 4],
            "high": [5, 6],
            "low": [1, 2],
            "close": [4, 3],
            "volume": [10, 11],
            "candle_range": [4.0, 4.1],
            "vendor_signal": [True, False],
        }
    )
    csv_path = tmp_path / "EURUSD_H1.csv"
    parquet_path = tmp_path / "EURUSD_H1.parquet"
    source.to_csv(csv_path, index=False)
    source.to_parquet(parquet_path, index=False)

    csv_store = load_bars(csv_path)
    parquet_store = load_bars(parquet_path)
    pd.testing.assert_frame_equal(csv_store.canonical_frame, parquet_store.canonical_frame)
    pd.testing.assert_frame_equal(csv_store.imported_features, parquet_store.imported_features)
    assert csv_store.imported_features.attrs["feature_sources"] == {
        "candle_range": "imported",
        "vendor_signal": "imported",
    }
    assert "candle_range" not in csv_store.canonical_frame


@pytest.mark.parametrize(
    ("row", "found"),
    [
        ((1, 1, 2, 0, 1, 10, True, "extra"), 8),
        ((1, 1, 2, 0, 1, 10), 6),
    ],
)
def test_csv_rejects_rows_misaligned_with_imported_feature(
    tmp_path: Path, row: tuple[object, ...], found: int
) -> None:
    path = tmp_path / "EURUSD_H1.csv"
    with path.open("w", newline="", encoding="utf-8") as source_file:
        writer = csv.writer(source_file)
        writer.writerow(("time", "open", "high", "low", "close", "volume", "vendor_signal"))
        writer.writerow(row)

    with pytest.raises(
        SourceValidationError,
        match=rf"EURUSD_H1.csv, CSV row 2: expected 7 fields, found {found}",
    ):
        load_bars(path)


@pytest.mark.parametrize("suffix", ["csv", "parquet"])
def test_tabular_sources_reject_invalid_order(tmp_path: Path, suffix: str) -> None:
    frame = pd.DataFrame(
        {
            "time": [2, 1],
            "open": [1, 1],
            "high": [2, 2],
            "low": [0, 0],
            "close": [1, 1],
            "volume": [1, 1],
        }
    )
    path = tmp_path / f"bad.{suffix}"
    getattr(frame, f"to_{suffix}")(path, index=False)
    with pytest.raises(SourceValidationError, match="earlier"):
        load_bars(path)


def test_imported_feature_alignment_is_enforced() -> None:
    bars = (Bar(1, 1, 2, 0, 1, 1),)
    misaligned = pd.DataFrame({"signal": [True]}, index=pd.Index([2], name="time"))
    with pytest.raises(SourceValidationError, match="align exactly"):
        BarStore(bars, misaligned, "EURUSD/H1")


def test_basic_candle_calculations_and_engulfing_definition() -> None:
    bars = (
        Bar(1, 4, 5, 1, 2, 10),
        Bar(2, 3, 7, 2, 6, 11),
        Bar(3, 5, 7, 0, 1, 12),
        Bar(4, 3, 3, 3, 3, 13),
    )
    assert calculate(range_feature, bars).frame.iloc[:, 0].tolist() == [4.0, 5.0, 7.0, 0.0]  # type: ignore[union-attr]
    assert calculate(body_size_feature, bars).frame.iloc[:, 0].tolist() == [2.0, 3.0, 4.0, 0.0]  # type: ignore[union-attr]
    assert calculate(ratio_feature, bars).frame.iloc[:, 0].tolist() == [0.5, 0.6, 4 / 7, 0.0]  # type: ignore[union-attr]
    upper_wick = calculate(upper_wick_feature, bars)
    lower_wick = calculate(lower_wick_feature, bars)
    assert upper_wick is not None and lower_wick is not None
    assert upper_wick.frame["upper_wick_size"].tolist() == [1.0, 1.0, 2.0, 0.0]
    assert lower_wick.frame["lower_wick_size"].tolist() == [1.0, 1.0, 1.0, 0.0]
    assert calculate(direction_feature, bars).frame.iloc[:, 0].tolist() == [-1, 1, -1, 0]  # type: ignore[union-attr]
    assert calculate(engulfing_feature, bars).frame.iloc[:, 0].tolist() == [
        False,
        True,
        True,
        False,
    ]  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "definition",
    [
        range_feature,
        body_size_feature,
        ratio_feature,
        upper_wick_feature,
        lower_wick_feature,
        direction_feature,
        engulfing_feature,
    ],
)
def test_candle_calculations_are_causal(definition: object) -> None:
    initial = (Bar(1, 2, 5, 1, 4, 10), Bar(2, 5, 6, 0, 1, 11))
    appended = (*initial, Bar(3, 1, 10, 0, 9, 12))
    before = calculate(definition, initial)  # type: ignore[arg-type]
    after = calculate(definition, appended)  # type: ignore[arg-type]
    assert before is not None and after is not None
    pd.testing.assert_frame_equal(before.frame, after.frame.iloc[: len(initial)])


def test_comparison_reports_timestamp_and_value_mismatches_without_mutation() -> None:
    imported_values = pd.Series([1.0, 2.0, True], index=pd.Index([1, 2, 4], name="time"))
    computed_values = pd.Series([1.0 + 1e-10, 3.0, True], index=pd.Index([1, 2, 3], name="time"))
    imported = FeatureColumn("EURUSD/H1", "vendor", "imported", imported_values)
    computed = FeatureColumn("EURUSD/H1", "vendor", "computed", computed_values)
    result = compare_feature_columns(imported, computed)
    assert result.missing_imported_timestamps == (3,)
    assert result.missing_computed_timestamps == (4,)
    assert [
        (entry.timestamp, entry.imported_value, entry.computed_value) for entry in result.mismatches
    ] == [(2, 2.0, 3.0)]
    assert imported.values.equals(imported_values) and computed.values.equals(computed_values)
    with pytest.raises(ValueError, match="finite and non-negative"):
        compare_feature_columns(imported, computed, relative_tolerance=float("nan"))


def test_collision_keeps_imported_and_computed_columns_separate(tmp_path: Path) -> None:
    frame = pd.DataFrame(
        {
            "time": [1],
            "open": [1],
            "high": [2],
            "low": [0],
            "close": [2],
            "volume": [1],
            "candle_range": [99.0],
        }
    )
    path = tmp_path / "EURUSD_H1.parquet"
    frame.to_parquet(path, index=False)
    store = load_bars(path)
    computed_table = calculate(range_feature, store.bars)
    assert computed_table is not None
    imported = imported_feature_column(store, "candle_range")
    computed = computed_feature_column(store.dataset_id, computed_table, "candle_range")
    assert imported.source == "imported" and computed.source == "computed"
    assert imported.values.iloc[0] == 99 and computed.values.iloc[0] == 2
