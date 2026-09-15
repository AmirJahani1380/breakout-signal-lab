from __future__ import annotations

from collections.abc import Sequence

from app.bars import Bar

from . import FeatureDefinition, FeatureSpec, FeatureTable, FeatureViewSpec
from .candle_measurements import measure_candles

SPEC = FeatureSpec("body_to_range_ratio", "Float64")


def calculate(bars: Sequence[Bar]) -> FeatureTable:
    measured = measure_candles(bars)
    values = [
        body / candle_range if candle_range else 0.0
        for body, candle_range in zip(measured.body_size, measured.candle_range)
    ]
    return FeatureTable.from_columns((SPEC,), [bar.time for bar in bars], {SPEC.name: values})


feature = FeatureDefinition(
    (SPEC,),
    calculate,
    (
        FeatureViewSpec(
            "body_to_range_ratio", SPEC.name, "Body/range", "Body divided by range", None
        ),
    ),
)
