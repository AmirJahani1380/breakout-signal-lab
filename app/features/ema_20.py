from __future__ import annotations

from collections.abc import Sequence

import pandas as pd

from app.bars import Bar

from . import FeatureDefinition, FeatureSpec, FeatureTable, FeatureViewSpec

SPEC = FeatureSpec("ema_20", "Float64", {"period": 20}, version="1")


def calculate(bars: Sequence[Bar]) -> FeatureTable:
    closes = pd.Series([bar.close for bar in bars], dtype="Float64")
    values = closes.ewm(span=20, adjust=False).mean().astype("Float64")
    return FeatureTable.from_columns((SPEC,), [bar.time for bar in bars], {SPEC.name: values})


feature = FeatureDefinition(
    (SPEC,),
    calculate,
    (
        FeatureViewSpec(
            "ema_20",
            SPEC.name,
            "EMA 20",
            "Price overlay",
            "line",
            series_options={
                "color": "#ff9800",
                "lineWidth": 2,
                "lastValueVisible": False,
                "priceLineVisible": False,
            },
        ),
    ),
    calculation_warm_up=100,
)
