from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.bars import Bar

from . import FeatureDefinition, FeatureSpec, FeatureTable, FeatureViewSpec
from .ema_20 import ema_step

MACD = FeatureSpec("macd", "Float64", {"fast_period": 12, "slow_period": 26}, version="1")
SIGNAL = FeatureSpec("macd_signal", "Float64", {"period": 9}, version="1")
HISTOGRAM = FeatureSpec("macd_histogram", "Float64", version="1")


@dataclass(slots=True)
class MacdState:
    fast: float | None = None
    slow: float | None = None
    signal: float | None = None

    def add(self, close: float, needs_signal: bool) -> tuple[float, float | None]:
        self.fast = ema_step(self.fast, close, 12)
        self.slow = ema_step(self.slow, close, 26)
        macd = self.fast - self.slow
        if needs_signal:
            self.signal = ema_step(self.signal, macd, 9)
        return macd, self.signal


def calculate(bars: Sequence[Bar]) -> FeatureTable:
    return calculate_selected(bars, frozenset({MACD.name, SIGNAL.name, HISTOGRAM.name}))


def calculate_selected(bars: Sequence[Bar], names: frozenset[str]) -> FeatureTable:
    columns: dict[str, list[float]] = {name: [] for name in names}
    state = MacdState()
    for bar in bars:
        macd, signal = state.add(bar.close, SIGNAL.name in names or HISTOGRAM.name in names)
        if MACD.name in names:
            columns[MACD.name].append(macd)
        if SIGNAL.name in names or HISTOGRAM.name in names:
            assert signal is not None
            if SIGNAL.name in names:
                columns[SIGNAL.name].append(signal)
            if HISTOGRAM.name in names:
                columns[HISTOGRAM.name].append(macd - signal)
    specs = tuple(spec for spec in (MACD, SIGNAL, HISTOGRAM) if spec.name in names)
    return FeatureTable.from_columns(
        specs,
        [bar.time for bar in bars],
        columns,
    )


COMMON = {"lastValueVisible": True, "priceLineVisible": False}
feature = FeatureDefinition(
    (MACD, SIGNAL, HISTOGRAM),
    calculate,
    (
        FeatureViewSpec(
            "macd",
            MACD.name,
            "MACD",
            "12/26 EMA difference",
            "line",
            pane="separate",
            series_options={**COMMON, "color": "#26a69a", "lineWidth": 2},
            pane_height=160,
            reference_lines=(
                {
                    "price": 0,
                    "color": "#787b86",
                    "lineStyle": 2,
                    "lineWidth": 1,
                    "axisLabelVisible": True,
                    "title": "0",
                },
            ),
        ),
        FeatureViewSpec(
            "macd_signal",
            SIGNAL.name,
            "MACD Signal",
            "9-period signal",
            "line",
            pane="separate",
            series_options={**COMMON, "color": "#ff9800", "lineWidth": 1},
            pane_height=160,
        ),
        FeatureViewSpec(
            "macd_histogram",
            HISTOGRAM.name,
            "MACD Histogram",
            "MACD minus signal",
            "histogram",
            pane="separate",
            series_options={**COMMON, "color": "#787b86"},
            pane_height=160,
        ),
    ),
    calculation_warm_up=100,
    calculate_selected=calculate_selected,
)
