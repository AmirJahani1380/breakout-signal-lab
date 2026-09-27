from __future__ import annotations

from collections.abc import Sequence

from app.bars import Bar

from . import FeatureDefinition, FeatureSetting, FeatureSpec, FeatureTable, FeatureViewSpec
from .atr import DEFAULT_PERIOD as DEFAULT_ATR_PERIOD
from .atr import _validate_period as _validate_atr_period
from .atr import atr_values
from .ema import DEFAULT_PERIOD, _validate_period, ema_step


def make_feature(
    period: int = DEFAULT_PERIOD,
    atr_period: int = DEFAULT_ATR_PERIOD,
    absolute: bool = False,
) -> FeatureDefinition:
    _validate_period(period)
    _validate_atr_period(atr_period)
    if type(absolute) is not bool:
        raise ValueError("EMA distance absolute must be a boolean")
    name = f"ema_distance_{period}_atr_{atr_period}{'_absolute' if absolute else ''}"
    spec = FeatureSpec(
        name,
        "Float64",
        {"period": period, "atr_period": atr_period, "absolute": int(absolute)},
        warm_up=atr_period - 1,
        selection_key="ema_distance",
    )

    def calculate(bars: Sequence[Bar]) -> FeatureTable:
        previous: float | None = None
        distances: list[float | None] = []
        for bar, atr in zip(bars, atr_values(bars, atr_period)):
            previous = ema_step(previous, bar.close, period)
            distance = bar.close - previous
            distances.append((abs(distance) if absolute else distance) / atr if atr else None)
        return FeatureTable.from_columns((spec,), [bar.time for bar in bars], {name: distances})

    view = FeatureViewSpec(
        name,
        name,
        f"{'Absolute ' if absolute else ''}EMA {period} distance / ATR {atr_period}",
        "Close minus EMA divided by aligned ATR; null when ATR is unavailable or zero",
        "line",
        pane="separate",
        series_options={"color": "#29b6f6", "priceLineVisible": False},
        selection_key="ema_distance",
    )
    return FeatureDefinition(
        (spec,),
        calculate,
        (view,),
        calculation_warm_up=max(100, period, atr_period),
        settings=(
            FeatureSetting("ema_period", "EMA", DEFAULT_PERIOD),
            FeatureSetting("atr_period", "ATR", DEFAULT_ATR_PERIOD, parameters=("atr_period",)),
            FeatureSetting(
                "ema_distance_absolute", "Absolute distance", 0, 0, 1, parameters=("absolute",)
            ),
        ),
        configure=lambda values: make_feature(
            values["ema_period"], values["atr_period"], bool(values["ema_distance_absolute"])
        ),
    )


feature = make_feature()


def calculate(bars: Sequence[Bar]) -> FeatureTable:
    return feature.calculate(bars)
