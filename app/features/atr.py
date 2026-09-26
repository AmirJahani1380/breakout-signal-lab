from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.bars import Bar

from . import FeatureDefinition, FeatureSetting, FeatureSpec, FeatureTable, FeatureViewSpec

DEFAULT_PERIOD = 20


def _validate_period(period: int) -> None:
    if isinstance(period, bool) or not isinstance(period, int) or period < 1:
        raise ValueError("ATR period must be a positive integer")


@dataclass(slots=True)
class AtrState:
    period: int = DEFAULT_PERIOD
    previous_close: float | None = None
    count: int = 0
    true_range_total: float = 0.0
    average: float | None = None

    def __post_init__(self) -> None:
        _validate_period(self.period)

    def add(self, bar: Bar) -> float | None:
        previous_close = self.previous_close if self.previous_close is not None else bar.close
        true_range = max(
            bar.high - bar.low,
            abs(bar.high - previous_close),
            abs(bar.low - previous_close),
        )
        if self.count < self.period:
            self.true_range_total += true_range
            if self.count == self.period - 1:
                self.average = self.true_range_total / self.period
        else:
            assert self.average is not None
            self.average = (self.average * (self.period - 1) + true_range) / self.period
        self.previous_close = bar.close
        self.count += 1
        return self.average


def atr_values(bars: Sequence[Bar], period: int = DEFAULT_PERIOD) -> list[float | None]:
    """Seed with the mean of period true ranges, taking the first as high-low."""
    state = AtrState(period=period)
    return [state.add(bar) for bar in bars]


def make_feature(period: int = DEFAULT_PERIOD) -> FeatureDefinition:
    _validate_period(period)
    name = f"atr_{period}"
    spec = FeatureSpec(name, "Float64", {"period": period}, warm_up=period - 1, selection_key="atr")

    def calculate(bars: Sequence[Bar]) -> FeatureTable:
        return FeatureTable.from_columns(
            (spec,), [bar.time for bar in bars], {name: atr_values(bars, period)}
        )

    views = (
        FeatureViewSpec(
            name,
            name,
            f"ATR {period}",
            f"Wilder average true range, period {period}",
            None,
            selection_key="atr",
        ),
        FeatureViewSpec(
            f"{name}_pane",
            name,
            f"ATR {period} pane",
            f"Wilder average true range, period {period}",
            "line",
            pane="separate",
            pane_height=140,
            series_options={"color": "#ff9800"},
            selection_key="atr_pane",
        ),
    )
    return FeatureDefinition(
        (spec,),
        calculate,
        views,
        calculation_warm_up=max(100, period),
        settings=(FeatureSetting("atr_period", "ATR", DEFAULT_PERIOD),),
        configure=lambda values: make_feature(values["atr_period"]),
    )


feature = make_feature()


def calculate(bars: Sequence[Bar]) -> FeatureTable:
    return feature.calculate(bars)
