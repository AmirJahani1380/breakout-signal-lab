from math import nan

import pandas as pd
import pytest

from app.bars import Bar
from app.features.atr_20 import calculate as calculate_atr
from app.features.normalized_candles import calculate as calculate_normalized
from app.features.rolling_overlap_20 import calculate as calculate_overlap
from app.features.rolling_overlap_20 import feature as overlap_feature
from app.features.rolling_overlap_20 import overlap_score


def candle(
    time: int, low: float, high: float, open_: float | None = None, close: float | None = None
) -> Bar:
    return Bar(
        time, low if open_ is None else open_, high, low, high if close is None else close, 1
    )


def test_atr_seed_wilder_update_and_normalized_measurements() -> None:
    bars = tuple(candle(index, 9, 11, 10, 10) for index in range(20)) + (
        candle(20, 10, 14, 11, 12),
    )
    atr = calculate_atr(bars).frame["atr_20"]
    assert atr.iloc[:19].isna().all()
    assert atr.iloc[19] == 2  # 20 true ranges of 2, including the first high-low
    assert atr.iloc[20] == pytest.approx(2.1)  # (19 * 2 + 4) / 20

    normalized = calculate_normalized(bars).frame
    assert normalized.index.tolist() == list(range(21))
    assert normalized.iloc[:19]["candle_range_to_atr_20"].isna().all()
    expected = {"candle_range": 4, "body_size": 1, "upper_wick": 2, "lower_wick": 1}
    for measurement, numerator in expected.items():
        assert normalized.at[20, f"{measurement}_to_atr_20"] == pytest.approx(numerator / 2.1)
        assert normalized.at[20, f"{measurement}_to_close"] == pytest.approx(numerator / 12)
    assert normalized.at[20, "atr_20_to_close"] == pytest.approx(2.1 / 12)


def test_atr_initial_true_range_includes_gap_and_zero_denominators_are_null() -> None:
    bars = (candle(0, 9, 11, 10, 10),) + tuple(
        candle(index, 19, 21, 20, 20) for index in range(1, 20)
    )
    assert calculate_atr(bars).frame.at[19, "atr_20"] == pytest.approx((2 + 11 + 18 * 2) / 20)
    zero = tuple(candle(index, 0, 0, 0, 0) for index in range(20))
    normalized = calculate_normalized(zero).frame
    assert normalized["candle_range_to_close"].isna().all()
    assert pd.isna(normalized.at[19, "candle_range_to_atr_20"])
    assert pd.isna(normalized.at[19, "atr_20_to_close"])


def test_overlap_exact_and_sampled_coverage_cases() -> None:
    assert overlap_score([candle(index, 0, 1) for index in range(20)]) == 1
    assert overlap_score([candle(index, index * 2, index * 2 + 1) for index in range(20)]) == 0
    partial = [candle(index, 0, 1) for index in range(10)] + [
        candle(index, 0, 2) for index in range(10, 20)
    ]
    assert overlap_score(partial) == pytest.approx((10 + 10 * 9 / 19) / 20)
    coverage_count = [candle(index, 0, 20) for index in range(2)] + [
        candle(index, 0, 1) for index in range(2, 20)
    ]
    assert overlap_score(coverage_count) == pytest.approx(0.1)
    assert overlap_score([candle(index, 5, 5) for index in range(20)]) == 1


def test_overlap_null_invalid_history_alignment_and_causality() -> None:
    first_twenty = tuple(candle(index, 0, 1) for index in range(20))
    assert overlap_score(first_twenty[:-1]) is None
    assert overlap_score(first_twenty[:-1] + (candle(19, 2, 1),)) is None
    assert overlap_score(first_twenty[:-1] + (candle(19, nan, 1),)) is None
    assert overlap_score(first_twenty[:-1] + (candle(19, 0, 1, nan, 1),)) is None
    assert overlap_score(first_twenty[:-1] + (candle(18, 0, 1),)) is None
    bars = first_twenty + (candle(20, 100, 101), candle(21, 200, 201))
    changed = first_twenty + (candle(20, 0, 1), candle(21, -200, -199))
    scores = calculate_overlap(bars).frame
    changed_scores = calculate_overlap(changed).frame
    assert scores.index.tolist() == list(range(22))
    assert scores["rolling_overlap_20"].iloc[:20].isna().all()
    assert scores.at[20, "rolling_overlap_20"] == 1
    assert scores.at[20, "rolling_overlap_20"] == changed_scores.at[20, "rolling_overlap_20"]
    assert scores.at[21, "rolling_overlap_20"] != changed_scores.at[21, "rolling_overlap_20"]


def test_overlap_view_colors_both_sides_of_half_with_low_opacity() -> None:
    bars = tuple(candle(index, 0, 1) for index in range(20)) + (
        candle(20, 100, 101),
        candle(21, 100, 101),
    )
    definition = overlap_feature.views[0].definition(calculate_overlap(bars))
    assert definition["points"] == [
        {"time": 20, "value": 1.0, "color": "#00d08440"},
        {"time": 21, "value": 0.0, "color": "#ff4d6d40"},
    ]
    assert definition["values"] == [
        {"time": 20, "value": 1.0},
        {"time": 21, "value": 0.0},
    ]
