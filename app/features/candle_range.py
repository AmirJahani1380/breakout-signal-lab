from __future__ import annotations

from collections.abc import Sequence

from app.bars import Bar

from . import FeatureDefinition, FeatureSpec, FeatureTable, FeatureViewSpec
from .candle_measurements import measure_candles

SPEC = FeatureSpec("candle_range", "Float64")


def calculate(bars: Sequence[Bar]) -> FeatureTable:
    values = measure_candles(bars).candle_range
    return FeatureTable.from_columns((SPEC,), [bar.time for bar in bars], {SPEC.name: values})


feature = FeatureDefinition(
    (SPEC,),
    calculate,
    (FeatureViewSpec("candle_range", SPEC.name, "Range", "High minus low", None),),
)
