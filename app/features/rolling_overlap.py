from __future__ import annotations

from collections.abc import Sequence
from math import isfinite

from app.bars import Bar

from . import FeatureDefinition, FeatureSetting, FeatureSpec, FeatureTable, FeatureViewSpec

DEFAULT_PERIOD = 20


def _validate_period(period: int) -> None:
    if isinstance(period, bool) or not isinstance(period, int) or period < 2:
        raise ValueError("rolling overlap period must be an integer of at least 2")


def overlap_score(window: Sequence[Bar], period: int = DEFAULT_PERIOD) -> float | None:
    _validate_period(period)
    if len(window) != period:
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
                sum(bar.low <= minimum + (i + 0.5) * width / period <= bar.high for bar in window)
                - 1,
                0,
            )
            / (period - 1)
            for i in range(period)
        )
        / period
    )


def make_feature(period: int = DEFAULT_PERIOD) -> FeatureDefinition:
    _validate_period(period)
    name = f"rolling_overlap_{period}"
    score = FeatureSpec(
        name, "Float64", {"period": period}, warm_up=period, selection_key="rolling_overlap"
    )
    above = FeatureSpec(
        f"{name}_above_half",
        "boolean",
        {"period": period},
        warm_up=period,
        selection_key="rolling_overlap_above_half",
    )

    def calculate(bars: Sequence[Bar]) -> FeatureTable:
        scores = [overlap_score(bars[i - period : i], period) for i in range(len(bars))]
        return FeatureTable.from_columns(
            (score, above),
            [bar.time for bar in bars],
            {
                name: scores,
                above.name: [None if x is None or x == 0.5 else x > 0.5 for x in scores],
            },
        )

    view = FeatureViewSpec(
        name,
        name,
        f"Rolling overlap {period}",
        f"Shared range coverage of the previous {period} completed candles (0 to 1)",
        "histogram",
        pane="separate",
        pane_height=140,
        scale_range=(0, 1),
        series_options={"priceLineVisible": False, "lastValueVisible": True},
        color_feature=above.name,
        color_palette=("#00d08440", "#ff4d6d40"),
        selection_key="rolling_overlap",
    )
    return FeatureDefinition(
        (score, above),
        calculate,
        (view,),
        calculation_warm_up=period,
        settings=(FeatureSetting("rolling_overlap_period", "Rolling overlap", DEFAULT_PERIOD, 2),),
        configure=lambda values: make_feature(values["rolling_overlap_period"]),
    )


feature = make_feature()


def calculate(bars: Sequence[Bar]) -> FeatureTable:
    return feature.calculate(bars)
