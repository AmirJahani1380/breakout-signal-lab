from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.bars import Bar

from . import FeatureDefinition, FeatureSpec, FeatureTable, FeatureViewSpec

PERIOD = 14
SPEC = FeatureSpec("rsi_14", "Float64", {"period": PERIOD}, warm_up=PERIOD, version="1")


@dataclass(slots=True)
class Rsi14State:
    previous_close: float | None = None
    count: int = 0
    gain_sum: float = 0.0
    loss_sum: float = 0.0
    average_gain: float | None = None
    average_loss: float | None = None

    def add(self, close: float) -> float | None:
        if self.previous_close is None:
            self.previous_close = close
            return None
        change = close - self.previous_close
        self.previous_close = close
        self.count += 1
        gain, loss = max(change, 0), max(-change, 0)
        if self.count <= PERIOD:
            self.gain_sum += gain
            self.loss_sum += loss
            if self.count == PERIOD:
                self.average_gain = self.gain_sum / PERIOD
                self.average_loss = self.loss_sum / PERIOD
        else:
            assert self.average_gain is not None and self.average_loss is not None
            self.average_gain = (self.average_gain * (PERIOD - 1) + gain) / PERIOD
            self.average_loss = (self.average_loss * (PERIOD - 1) + loss) / PERIOD
        if self.average_gain is None or self.average_loss is None:
            return None
        if self.average_gain == self.average_loss == 0:
            return 50.0
        if self.average_loss == 0:
            return 100.0
        return 100 - 100 / (1 + self.average_gain / self.average_loss)


def calculate(bars: Sequence[Bar]) -> FeatureTable:
    state = Rsi14State()
    values = [state.add(bar.close) for bar in bars]
    return FeatureTable.from_columns((SPEC,), [bar.time for bar in bars], {SPEC.name: values})


feature = FeatureDefinition(
    (SPEC,),
    calculate,
    (
        FeatureViewSpec(
            "rsi_14",
            SPEC.name,
            "RSI 14",
            "Separate 0–100 pane",
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
        ),
    ),
    calculation_warm_up=100,
)
