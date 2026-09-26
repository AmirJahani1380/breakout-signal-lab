"""Swing pivots become usable only after their right-hand confirmation bars close."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.bars import Bar

from . import FeatureDefinition, FeatureSpec, FeatureTable, FeatureViewSpec


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


_HIGH = FeatureSpec("confirmed_swing_high_3_3", "Float64", {"left": 3, "right": 3})
_LOW = FeatureSpec("confirmed_swing_low_3_3", "Float64", {"left": 3, "right": 3})


def calculate(bars: Sequence[Bar]) -> FeatureTable:
    positions = {bar.time: index for index, bar in enumerate(bars)}
    highs: list[float | None] = [None] * len(bars)
    lows: list[float | None] = [None] * len(bars)
    for swing in confirmed_swings(bars):
        target = highs if swing.direction == "high" else lows
        target[positions[swing.availability_time]] = swing.level
    return FeatureTable.from_columns(
        (_HIGH, _LOW),
        [bar.time for bar in bars],
        {_HIGH.name: highs, _LOW.name: lows},
    )


feature = FeatureDefinition(
    (_HIGH, _LOW),
    calculate,
    (
        FeatureViewSpec(
            _HIGH.name,
            _HIGH.name,
            "Confirmed swing high 3/3",
            "Level available on confirmation candle",
            None,
        ),
        FeatureViewSpec(
            _LOW.name,
            _LOW.name,
            "Confirmed swing low 3/3",
            "Level available on confirmation candle",
            None,
        ),
    ),
    calculation_warm_up=6,
)
