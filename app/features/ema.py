from __future__ import annotations

from collections.abc import Sequence

from app.bars import Bar

from . import FeatureDefinition, FeatureSetting, FeatureSpec, FeatureTable, FeatureViewSpec

DEFAULT_PERIOD = 20


def _validate_period(period: int) -> None:
    if isinstance(period, bool) or not isinstance(period, int) or period < 1:
        raise ValueError("EMA period must be a positive integer")


def ema_step(previous: float | None, current: float, period: int) -> float:
    return (
        current
        if previous is None
        else (1 - 2 / (period + 1)) * previous + 2 / (period + 1) * current
    )


def make_feature(period: int = DEFAULT_PERIOD) -> FeatureDefinition:
    _validate_period(period)
    name = f"ema_{period}"
    spec = FeatureSpec(name, "Float64", {"period": period}, version="1", selection_key="ema")

    def calculate(bars: Sequence[Bar]) -> FeatureTable:
        values: list[float] = []
        previous: float | None = None
        for bar in bars:
            previous = ema_step(previous, bar.close, period)
            values.append(previous)
        return FeatureTable.from_columns((spec,), [bar.time for bar in bars], {name: values})

    return FeatureDefinition(
        (spec,),
        calculate,
        (
            FeatureViewSpec(
                name,
                name,
                "EMA",
                f"Price overlay, period {period}",
                "line",
                series_options={
                    "color": "#ff9800",
                    "lineWidth": 2,
                    "lastValueVisible": False,
                    "priceLineVisible": False,
                },
                selection_key="ema",
            ),
        ),
        calculation_warm_up=max(100, period),
        settings=(FeatureSetting("ema_period", "EMA", DEFAULT_PERIOD),),
        configure=lambda values: make_feature(values["ema_period"]),
    )


feature = make_feature()


def calculate(bars: Sequence[Bar]) -> FeatureTable:
    return feature.calculate(bars)
