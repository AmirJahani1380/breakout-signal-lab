from __future__ import annotations

from collections.abc import Sequence

from app.bars import Bar

from . import FeatureDefinition, FeatureSetting, FeatureSpec, FeatureTable, FeatureViewSpec
from .atr import DEFAULT_PERIOD as DEFAULT_ATR_PERIOD
from .atr import _validate_period as _validate_atr_period
from .atr import atr_values
from .ema import DEFAULT_PERIOD, _validate_period, ema_step

DEFAULT_SLOPE_BARS = 20


def make_feature(
    period: int = DEFAULT_PERIOD,
    slope_bars: int = DEFAULT_SLOPE_BARS,
    atr_period: int = DEFAULT_ATR_PERIOD,
    absolute: bool = False,
) -> FeatureDefinition:
    _validate_period(period)
    _validate_atr_period(atr_period)
    if type(slope_bars) is not int or slope_bars < 1:
        raise ValueError("EMA slope bars must be a positive integer")
    if type(absolute) is not bool:
        raise ValueError("EMA slope absolute must be a boolean")
    name = f"ema_slope_{period}_{slope_bars}_atr_{atr_period}{'_absolute' if absolute else ''}"
    spec = FeatureSpec(
        name,
        "Float64",
        {
            "period": period,
            "slope_bars": slope_bars,
            "atr_period": atr_period,
            "absolute": int(absolute),
        },
        warm_up=max(slope_bars, atr_period - 1),
        selection_key="ema_slope",
    )

    def calculate(bars: Sequence[Bar]) -> FeatureTable:
        previous: float | None = None
        ema_values: list[float] = []
        for bar in bars:
            previous = ema_step(previous, bar.close, period)
            ema_values.append(previous)
        slopes: list[float | None] = []
        for index, atr in enumerate(atr_values(bars, atr_period)):
            if index < slope_bars or not atr:
                slopes.append(None)
                continue
            change = (ema_values[index] - ema_values[index - slope_bars]) / slope_bars
            slopes.append((abs(change) if absolute else change) / atr)
        return FeatureTable.from_columns((spec,), [bar.time for bar in bars], {name: slopes})

    view = FeatureViewSpec(
        name,
        name,
        f"{'Absolute ' if absolute else ''}EMA {period} slope / ATR {atr_period}, "
        f"{slope_bars} bars",
        f"EMA change over {slope_bars} bars divided by {slope_bars} and aligned ATR; "
        "null when ATR is unavailable or zero",
        "line",
        pane="separate",
        series_options={"color": "#ab47bc", "priceLineVisible": False},
        selection_key="ema_slope",
    )
    return FeatureDefinition(
        (spec,),
        calculate,
        (view,),
        calculation_warm_up=max(100, period + slope_bars, atr_period),
        settings=(
            FeatureSetting("ema_period", "EMA", DEFAULT_PERIOD),
            FeatureSetting("atr_period", "ATR", DEFAULT_ATR_PERIOD, parameters=("atr_period",)),
            FeatureSetting(
                "ema_slope_bars",
                "EMA slope bars",
                DEFAULT_SLOPE_BARS,
                parameters=("slope_bars",),
            ),
            FeatureSetting(
                "ema_slope_absolute", "Absolute slope", 0, 0, 1, parameters=("absolute",)
            ),
        ),
        configure=lambda values: make_feature(
            values["ema_period"],
            values["ema_slope_bars"],
            values["atr_period"],
            bool(values["ema_slope_absolute"]),
        ),
    )


feature = make_feature()


def calculate(bars: Sequence[Bar]) -> FeatureTable:
    return feature.calculate(bars)
