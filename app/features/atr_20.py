from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.bars import Bar

from . import FeatureDefinition, FeatureSpec, FeatureTable, FeatureViewSpec

PERIOD = 20
SPEC = FeatureSpec("atr_20", "Float64", {"period": PERIOD}, warm_up=PERIOD - 1)


@dataclass(slots=True)
class Atr20State:
    previous_close: float | None = None
    count: int = 0
    true_range_total: float = 0.0
    average: float | None = None

    def add(self, bar: Bar) -> float | None:
        previous_close = self.previous_close if self.previous_close is not None else bar.close
        true_range = max(
            bar.high - bar.low,
            abs(bar.high - previous_close),
            abs(bar.low - previous_close),
        )
        if self.count < PERIOD:
            self.true_range_total += true_range
            if self.count == PERIOD - 1:
                self.average = self.true_range_total / PERIOD
        else:
            assert self.average is not None
            self.average = (self.average * (PERIOD - 1) + true_range) / PERIOD
        self.previous_close = bar.close
        self.count += 1
        return self.average


def atr_20_values(bars: Sequence[Bar]) -> list[float | None]:
    """Seed with the mean of 20 true ranges, taking the first range as high-low."""
    state = Atr20State()
    return [state.add(bar) for bar in bars]


def calculate(bars: Sequence[Bar]) -> FeatureTable:
    return FeatureTable.from_columns(
        (SPEC,), [bar.time for bar in bars], {SPEC.name: atr_20_values(bars)}
    )


feature = FeatureDefinition(
    (SPEC,),
    calculate,
    (
        FeatureViewSpec("atr_20", SPEC.name, "ATR 20", "Wilder average true range", None),
        FeatureViewSpec(
            "atr_20_pane",
            SPEC.name,
            "ATR 20 pane",
            "Wilder average true range",
            "line",
            pane="separate",
            pane_height=140,
            series_options={"color": "#ff9800"},
        ),
    ),
    calculation_warm_up=100,
)
