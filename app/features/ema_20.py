from __future__ import annotations

from collections.abc import Sequence

from app.bars import Bar

from . import FeatureDefinition, FeatureSpec, FeatureTable, FeatureViewSpec

SPEC = FeatureSpec("ema_20", "Float64", {"period": 20}, version="1")


def ema_step(previous: float | None, current: float, period: int) -> float:
    return (
        current
        if previous is None
        else (1 - 2 / (period + 1)) * previous + 2 / (period + 1) * current
    )


def calculate(bars: Sequence[Bar]) -> FeatureTable:
    values: list[float] = []
    previous: float | None = None
    for bar in bars:
        previous = ema_step(previous, bar.close, 20)
        values.append(previous)
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
