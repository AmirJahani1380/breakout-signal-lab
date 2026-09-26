from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.bars import Bar

from . import FeatureDefinition, FeatureSetting, FeatureSpec, FeatureTable, FeatureViewSpec

DEFAULT_PERIOD = 14


def _validate_period(period: int) -> None:
    if isinstance(period, bool) or not isinstance(period, int) or period < 1:
        raise ValueError("RSI period must be a positive integer")


@dataclass(slots=True)
class RsiState:
    period: int = DEFAULT_PERIOD
    previous_close: float | None = None
    count: int = 0
    gain_sum: float = 0.0
    loss_sum: float = 0.0
    average_gain: float | None = None
    average_loss: float | None = None

    def __post_init__(self) -> None:
        _validate_period(self.period)

    def add(self, close: float) -> float | None:
        if self.previous_close is None:
            self.previous_close = close
            return None
        change = close - self.previous_close
        self.previous_close = close
        self.count += 1
        gain, loss = max(change, 0), max(-change, 0)
        if self.count <= self.period:
            self.gain_sum += gain
            self.loss_sum += loss
            if self.count == self.period:
                self.average_gain = self.gain_sum / self.period
                self.average_loss = self.loss_sum / self.period
        else:
            assert self.average_gain is not None and self.average_loss is not None
            self.average_gain = (self.average_gain * (self.period - 1) + gain) / self.period
            self.average_loss = (self.average_loss * (self.period - 1) + loss) / self.period
        if self.average_gain is None or self.average_loss is None:
            return None
        if self.average_gain == self.average_loss == 0:
            return 50.0
        if self.average_loss == 0:
            return 100.0
        return 100 - 100 / (1 + self.average_gain / self.average_loss)


def make_feature(period: int = DEFAULT_PERIOD) -> FeatureDefinition:
    _validate_period(period)
    name = f"rsi_{period}"
    spec = FeatureSpec(name, "Float64", {"period": period}, warm_up=period, selection_key="rsi")

    def calculate(bars: Sequence[Bar]) -> FeatureTable:
        state = RsiState(period=period)
        values = [state.add(bar.close) for bar in bars]
        return FeatureTable.from_columns((spec,), [bar.time for bar in bars], {name: values})

    return FeatureDefinition(
        (spec,),
        calculate,
        (
            FeatureViewSpec(
                name,
                name,
                "RSI",
                f"Separate 0–100 pane, period {period}",
                "line",
                pane="separate",
                series_options={
                    "color": "#ab47bc",
                    "lineWidth": 2,
                    "lastValueVisible": True,
                    "priceLineVisible": False,
                },
                pane_height=160,
                scale_range=(0, 100),
                reference_lines=tuple(
                    {
                        "price": price,
                        "color": "#555864" if price == 50 else "#787b86",
                        "lineStyle": 2,
                        "lineWidth": 1,
                        "axisLabelVisible": True,
                        "title": str(price),
                    }
                    for price in (70, 50, 30)
                ),
                selection_key="rsi",
            ),
        ),
        calculation_warm_up=max(100, period),
        settings=(FeatureSetting("rsi_period", "RSI", DEFAULT_PERIOD),),
        configure=lambda values: make_feature(values["rsi_period"]),
    )


feature = make_feature()


def calculate(bars: Sequence[Bar]) -> FeatureTable:
    return feature.calculate(bars)
