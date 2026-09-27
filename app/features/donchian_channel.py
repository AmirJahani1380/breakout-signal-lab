"""Conventional Donchian channel of the current and preceding period minus one bars."""

from __future__ import annotations

from collections.abc import Sequence

from app.bars import Bar

from . import FeatureDefinition, FeatureSetting, FeatureSpec, FeatureTable, FeatureViewSpec


def make_feature(period: int = 20) -> FeatureDefinition:
    if type(period) is not int or not 1 <= period <= 500:
        raise ValueError("Donchian period must be an integer between 1 and 500")
    names = {edge: f"donchian_{edge}_{period}" for edge in ("upper", "lower", "middle")}
    specs = tuple(
        FeatureSpec(
            names[edge],
            "Float64",
            {"period": period},
            warm_up=period - 1,
            selection_key=f"donchian_{edge}",
        )
        for edge in names
    )

    def calculate(bars: Sequence[Bar]) -> FeatureTable:
        columns: dict[str, list[float | None]] = {name: [] for name in names.values()}
        for index in range(len(bars)):
            if index + 1 < period:
                for values in columns.values():
                    values.append(None)
                continue
            window = bars[index + 1 - period : index + 1]
            upper = max(bar.high for bar in window)
            lower = min(bar.low for bar in window)
            for edge, value in (
                ("upper", upper),
                ("lower", lower),
                ("middle", (upper + lower) / 2),
            ):
                columns[names[edge]].append(value)
        return FeatureTable.from_columns(specs, [bar.time for bar in bars], columns)

    def calculate_selected(bars: Sequence[Bar], selected: frozenset[str]) -> FeatureTable:
        table = calculate(bars)
        chosen = tuple(spec for spec in specs if spec.name in selected)
        return FeatureTable.from_columns(
            chosen,
            table.timestamps,
            {spec.name: table.frame[spec.name].tolist() for spec in chosen},
        )

    colors = {"upper": "#29b6f6", "lower": "#29b6f6", "middle": "#90a4ae"}
    views = tuple(
        FeatureViewSpec(
            names[edge],
            names[edge],
            f"Donchian {edge} {period}",
            f"{period}-bar Donchian channel {edge} (includes current candle)",
            "line",
            series_options={
                "color": colors[edge],
                "lineWidth": 2 if edge != "middle" else 1,
                "lastValueVisible": False,
                "priceLineVisible": False,
            },
            selection_key=f"donchian_{edge}",
        )
        for edge in names
    )
    return FeatureDefinition(
        specs,
        calculate,
        views,
        calculation_warm_up=period - 1,
        calculate_selected=calculate_selected,
        settings=(FeatureSetting("donchian_period", "Donchian window", 20),),
        configure=lambda values: make_feature(values["donchian_period"]),
    )


feature = make_feature()
