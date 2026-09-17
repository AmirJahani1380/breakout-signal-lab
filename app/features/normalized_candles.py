from __future__ import annotations

from collections.abc import Sequence

from app.bars import Bar

from . import FeatureDefinition, FeatureSpec, FeatureTable, FeatureViewSpec
from .atr_20 import atr_20_values
from .candle_measurements import measure_candles

MEASUREMENTS = ("candle_range", "body_size", "upper_wick", "lower_wick")
SPECS = tuple(
    FeatureSpec(
        f"{measurement}_to_{denominator}", "Float64", warm_up=19 if denominator == "atr_20" else 0
    )
    for measurement in MEASUREMENTS
    for denominator in ("atr_20", "close")
) + (FeatureSpec("atr_20_to_close", "Float64", warm_up=19),)


def calculate(bars: Sequence[Bar]) -> FeatureTable:
    return calculate_selected(bars, frozenset(spec.name for spec in SPECS))


def calculate_selected(bars: Sequence[Bar], names: frozenset[str]) -> FeatureTable:
    measurements = measure_candles(bars)
    atr = atr_20_values(bars) if any("atr_20" in name for name in names) else []
    columns: dict[str, list[float | None]] = {}
    for spec in SPECS:
        if spec.name not in names:
            continue
        measurement, _, denominator_name = spec.name.rpartition("_to_")
        numerators = atr if measurement == "atr_20" else getattr(measurements, measurement)
        denominators = atr if denominator_name == "atr_20" else [bar.close for bar in bars]
        columns[spec.name] = [
            numerator / denominator
            if numerator is not None and denominator is not None and denominator != 0
            else None
            for numerator, denominator in zip(numerators, denominators)
        ]
    selected = tuple(spec for spec in SPECS if spec.name in names)
    return FeatureTable.from_columns(selected, [bar.time for bar in bars], columns)


feature = FeatureDefinition(
    SPECS,
    calculate,
    tuple(
        FeatureViewSpec(
            spec.name,
            spec.name,
            spec.name.replace("_", " ").title(),
            spec.name.replace("_to_", " divided by ").replace("_", " "),
            None,
        )
        for spec in SPECS
    ),
    calculation_warm_up=100,
    calculate_selected=calculate_selected,
)
