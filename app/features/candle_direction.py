from __future__ import annotations

from collections.abc import Sequence

from app.bars import Bar

from . import FeatureDefinition, FeatureSpec, FeatureTable, FeatureViewSpec

SPEC = FeatureSpec("candle_direction", "Int64")


def calculate(bars: Sequence[Bar]) -> FeatureTable:
    values = [(bar.close > bar.open) - (bar.close < bar.open) for bar in bars]
    return FeatureTable.from_columns((SPEC,), [bar.time for bar in bars], {SPEC.name: values})


feature = FeatureDefinition(
    (SPEC,),
    calculate,
    (FeatureViewSpec("candle_direction", SPEC.name, "Direction", "1 up, 0 flat, -1 down", None),),
)
