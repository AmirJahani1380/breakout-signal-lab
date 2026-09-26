from __future__ import annotations

from collections.abc import Sequence

from app.bars import Bar

from . import FeatureDefinition, FeatureSetting, FeatureSpec, FeatureTable, FeatureViewSpec
from .atr import atr_values
from .candle_measurements import measure_candles

MEASUREMENTS = ("candle_range", "body_size", "upper_wick", "lower_wick")
DEFAULT_ATR_PERIOD = 20


def _validate_atr_period(period: int) -> None:
    if isinstance(period, bool) or not isinstance(period, int) or period < 1:
        raise ValueError("ATR period for normalized candles must be a positive integer")


def make_feature(atr_period: int = DEFAULT_ATR_PERIOD) -> FeatureDefinition:
    _validate_atr_period(atr_period)
    suffix = f"atr_{atr_period}"
    specs = tuple(
        FeatureSpec(
            f"{measurement}_to_{denominator}",
            "Float64",
            {"atr_period": atr_period} if denominator == suffix else {},
            warm_up=atr_period - 1 if denominator == suffix else 0,
            selection_key=(
                f"{measurement}_to_atr" if denominator == suffix else f"{measurement}_to_close"
            ),
        )
        for measurement in MEASUREMENTS
        for denominator in (suffix, "close")
    ) + (
        FeatureSpec(
            f"{suffix}_to_close",
            "Float64",
            {"period": atr_period},
            warm_up=atr_period - 1,
            selection_key="atr_to_close",
        ),
    )

    def calculate(bars: Sequence[Bar]) -> FeatureTable:
        measurements = measure_candles(bars)
        names = [spec.name for spec in specs]
        atr = atr_values(bars, atr_period) if any(suffix in name for name in names) else []
        columns: dict[str, list[float | None]] = {}
        for spec in specs:
            measurement, _, denominator_name = spec.name.rpartition("_to_")
            numerators = atr if measurement == suffix else getattr(measurements, measurement)
            denominators = atr if denominator_name == suffix else [bar.close for bar in bars]
            columns[spec.name] = [
                n / d if n is not None and d is not None and d != 0 else None
                for n, d in zip(numerators, denominators)
            ]
        return FeatureTable.from_columns(specs, [bar.time for bar in bars], columns)

    def calculate_selected(bars: Sequence[Bar], names: frozenset[str]) -> FeatureTable:
        table = calculate(bars)
        selected = tuple(spec for spec in specs if spec.name in names)
        return FeatureTable(selected, table.frame[[spec.name for spec in selected]])

    views = tuple(
        FeatureViewSpec(
            spec.name,
            spec.name,
            (spec.selection_key or spec.name).replace("_", " ").title()
            + (f" {atr_period}" if spec.parameters else ""),
            spec.name.replace("_to_", " divided by ").replace("_", " "),
            None,
            selection_key=spec.selection_key,
        )
        for spec in specs
    )
    return FeatureDefinition(
        specs,
        calculate,
        views,
        calculation_warm_up=max(100, atr_period),
        calculate_selected=calculate_selected,
        settings=(
            FeatureSetting(
                "atr_period",
                "ATR",
                DEFAULT_ATR_PERIOD,
                parameters=("atr_period", "period"),
            ),
        ),
        configure=lambda values: make_feature(values["atr_period"]),
    )


feature = make_feature()
SPECS = feature.specs


def calculate(bars: Sequence[Bar]) -> FeatureTable:
    return feature.calculate(bars)
