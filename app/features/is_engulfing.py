from __future__ import annotations

from collections.abc import Sequence

from app.bars import Bar

from . import FeatureDefinition, FeatureSpec, FeatureTable, FeatureViewSpec

SPEC = FeatureSpec("is_engulfing", "boolean")


def calculate(bars: Sequence[Bar]) -> FeatureTable:
    """True when an opposite-direction candle closes beyond the prior candle's range."""
    values = [False]
    values.extend(_engulfs(previous, current) for previous, current in zip(bars, bars[1:]))
    return FeatureTable.from_columns(
        (SPEC,), [bar.time for bar in bars], {SPEC.name: values[: len(bars)]}
    )


def _engulfs(previous: Bar, current: Bar) -> bool:
    previous_is_bullish = previous.close > previous.open
    previous_is_bearish = previous.close < previous.open
    current_is_bullish = current.close > current.open
    current_is_bearish = current.close < current.open
    opens_within_previous_range = previous.low < current.open < previous.high

    return (
        previous_is_bearish
        and current_is_bullish
        and opens_within_previous_range
        and current.close > previous.high
    ) or (
        previous_is_bullish
        and current_is_bearish
        and opens_within_previous_range
        and current.close < previous.low
    )


feature = FeatureDefinition(
    (SPEC,),
    calculate,
    (
        FeatureViewSpec(
            "is_engulfing",
            SPEC.name,
            "Engulfing",
            "Opposite candle opens inside and closes beyond prior range",
            None,
        ),
    ),
)
