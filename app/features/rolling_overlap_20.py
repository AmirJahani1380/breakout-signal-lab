from __future__ import annotations

from collections.abc import Sequence
from math import isfinite

from app.bars import Bar

from . import FeatureDefinition, FeatureSpec, FeatureTable, FeatureViewSpec

PERIOD = 20
SCORE = FeatureSpec("rolling_overlap_20", "Float64", {"period": PERIOD}, warm_up=PERIOD)
ABOVE_HALF = FeatureSpec("rolling_overlap_20_above_half", "boolean", warm_up=PERIOD)


def overlap_score(window: Sequence[Bar]) -> float | None:
    """Approximate shared price coverage at 20 midpoints of the window range."""
    if len(window) != PERIOD:
        return None
    for index, bar in enumerate(window):
        if (
            not isinstance(bar, Bar)
            or not isinstance(bar.time, int)
            or (index > 0 and bar.time <= window[index - 1].time)
            or not all(
                isinstance(price, (int, float)) and isfinite(price)
                for price in (bar.open, bar.high, bar.low, bar.close)
            )
            or bar.high < max(bar.open, bar.close)
            or bar.low > min(bar.open, bar.close)
        ):
            return None
    minimum = min(bar.low for bar in window)
    width = max(bar.high for bar in window) - minimum
    if width == 0:
        return 1.0
    return (
        sum(
            max(
                sum(
                    bar.low <= minimum + (bin_index + 0.5) * width / PERIOD <= bar.high
                    for bar in window
                )
                - 1,
                0,
            )
            / (PERIOD - 1)
            for bin_index in range(PERIOD)
        )
        / PERIOD
    )


def calculate(bars: Sequence[Bar]) -> FeatureTable:
    return calculate_selected(bars, frozenset({SCORE.name, ABOVE_HALF.name}))


def calculate_selected(bars: Sequence[Bar], names: frozenset[str]) -> FeatureTable:
    scores = [overlap_score(bars[index - PERIOD : index]) for index in range(len(bars))]
    columns: dict[str, list[float | bool | None]] = {}
    if SCORE.name in names:
        columns[SCORE.name] = scores
    if ABOVE_HALF.name in names:
        columns[ABOVE_HALF.name] = [
            None if score is None or score == 0.5 else score > 0.5 for score in scores
        ]
    selected = tuple(spec for spec in (SCORE, ABOVE_HALF) if spec.name in names)
    return FeatureTable.from_columns(selected, [bar.time for bar in bars], columns)


feature = FeatureDefinition(
    (SCORE, ABOVE_HALF),
    calculate,
    (
        FeatureViewSpec(
            SCORE.name,
            SCORE.name,
            "Rolling overlap 20",
            "Shared range coverage of the previous 20 completed candles (0 to 1)",
            "histogram",
            pane="separate",
            pane_height=140,
            scale_range=(0, 1),
            series_options={"priceLineVisible": False, "lastValueVisible": True},
            color_feature=ABOVE_HALF.name,
            color_palette=("#00d08440", "#ff4d6d40"),
        ),
    ),
    calculation_warm_up=PERIOD,
    calculate_selected=calculate_selected,
)
