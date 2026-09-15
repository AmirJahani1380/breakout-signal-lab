from __future__ import annotations

from collections.abc import Sequence

from app.bars import Bar

from . import FeatureDefinition, FeatureSpec, FeatureTable, FeatureViewSpec
from .candle_measurements import measure_candles

SPEC = FeatureSpec("upper_wick_size", "Float64")


def calculate(bars: Sequence[Bar]) -> FeatureTable:
    values = measure_candles(bars).upper_wick
    return FeatureTable.from_columns((SPEC,), [bar.time for bar in bars], {SPEC.name: values})


feature = FeatureDefinition(
    (SPEC,),
    calculate,
    (FeatureViewSpec("upper_wick_size", SPEC.name, "Upper wick", "High to body top", None),),
)
