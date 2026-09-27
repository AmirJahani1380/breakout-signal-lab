"""Swing pivots become usable only after their right-hand confirmation bars close."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.bars import Bar

from . import FeatureDefinition, FeatureSetting, FeatureSpec, FeatureTable, FeatureViewSpec


@dataclass(frozen=True, slots=True)
class ConfirmedSwing:
    direction: str  # high or low
    pivot_time: int
    availability_time: int
    level: float


def confirmed_swings(
    bars: Sequence[Bar], left: int = 3, right: int = 3
) -> tuple[ConfirmedSwing, ...]:
    for name, count in (("left", left), ("right", right)):
        if type(count) is not int or count < 1 or count > 100:
            raise ValueError(f"swing {name} must be an integer between 1 and 100")
    swings: list[ConfirmedSwing] = []
    for pivot in range(left, len(bars) - right):
        preceding = bars[pivot - left : pivot]
        following = bars[pivot + 1 : pivot + right + 1]
        bar = bars[pivot]
        available = bars[pivot + right].time
        # Strict comparisons reject equal highs/lows anywhere in the window.
        if all(bar.high > other.high for other in (*preceding, *following)):
            swings.append(ConfirmedSwing("high", bar.time, available, bar.high))
        if all(bar.low < other.low for other in (*preceding, *following)):
            swings.append(ConfirmedSwing("low", bar.time, available, bar.low))
    return tuple(swings)


def make_feature(period: int = 3) -> FeatureDefinition:
    if type(period) is not int or not 1 <= period <= 100:
        raise ValueError("swing period must be an integer between 1 and 100")
    high = FeatureSpec(
        f"confirmed_swing_high_{period}_{period}",
        "Float64",
        {"left": period, "right": period},
        selection_key="confirmed_swing_high",
    )
    low = FeatureSpec(
        f"confirmed_swing_low_{period}_{period}",
        "Float64",
        {"left": period, "right": period},
        selection_key="confirmed_swing_low",
    )

    def calculate(bars: Sequence[Bar]) -> FeatureTable:
        positions = {bar.time: index for index, bar in enumerate(bars)}
        highs: list[float | None] = [None] * len(bars)
        lows: list[float | None] = [None] * len(bars)
        for swing in confirmed_swings(bars, period, period):
            target = highs if swing.direction == "high" else lows
            target[positions[swing.availability_time]] = swing.level
        return FeatureTable.from_columns(
            (high, low),
            [bar.time for bar in bars],
            {high.name: highs, low.name: lows},
        )

    return FeatureDefinition(
        (high, low),
        calculate,
        (
            FeatureViewSpec(
                f"confirmed_swing_{period}_{period}",
                high.name,
                f"Confirmed swings {period}/{period}",
                "Red dots show confirmed swing highs and lows on their pivot candles",
                "marker",
                show_in_crosshair=False,
                series_options={
                    "position": "atPriceMiddle",
                    "shape": "circle",
                    "color": "#ef5350",
                    "size": 1,
                },
                marker_features=(low.name,),
                marker_offset_bars=-period,
                selection_key="confirmed_swing",
            ),
        ),
        calculation_warm_up=period * 2,
        calculation_look_ahead=period,
        settings=(
            FeatureSetting(
                "swing_period", "Swing period", 3, maximum=100, parameters=("left", "right")
            ),
        ),
        configure=lambda values: make_feature(values["swing_period"]),
    )


feature = make_feature()


def calculate(bars: Sequence[Bar]) -> FeatureTable:
    return feature.calculate(bars)
