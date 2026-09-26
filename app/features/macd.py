from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.bars import Bar

from . import FeatureDefinition, FeatureSetting, FeatureSpec, FeatureTable, FeatureViewSpec
from .ema import ema_step

DEFAULT_FAST, DEFAULT_SLOW, DEFAULT_SIGNAL = 12, 26, 9


def _validate_periods(fast_period: int, slow_period: int, signal_period: int) -> None:
    if (
        any(
            isinstance(period, bool) or not isinstance(period, int) or period < 1
            for period in (fast_period, slow_period, signal_period)
        )
        or fast_period >= slow_period
    ):
        raise ValueError(
            "MACD periods must be positive integers; fast period must be below slow period"
        )


@dataclass(slots=True)
class MacdState:
    fast: float | None = None
    slow: float | None = None
    signal: float | None = None
    fast_period: int = DEFAULT_FAST
    slow_period: int = DEFAULT_SLOW
    signal_period: int = DEFAULT_SIGNAL

    def __post_init__(self) -> None:
        _validate_periods(self.fast_period, self.slow_period, self.signal_period)

    def add(self, close: float, needs_signal: bool) -> tuple[float, float | None]:
        self.fast = ema_step(self.fast, close, self.fast_period)
        self.slow = ema_step(self.slow, close, self.slow_period)
        macd = self.fast - self.slow
        if needs_signal:
            self.signal = ema_step(self.signal, macd, self.signal_period)
        return macd, self.signal


def make_feature(
    fast_period: int = DEFAULT_FAST,
    slow_period: int = DEFAULT_SLOW,
    signal_period: int = DEFAULT_SIGNAL,
) -> FeatureDefinition:
    _validate_periods(fast_period, slow_period, signal_period)
    suffix = f"{fast_period}_{slow_period}_{signal_period}"
    parameters = {
        "fast_period": fast_period,
        "slow_period": slow_period,
        "signal_period": signal_period,
    }
    macd = FeatureSpec(f"macd_{suffix}", "Float64", parameters, selection_key="macd")
    signal = FeatureSpec(
        f"macd_signal_{suffix}",
        "Float64",
        parameters,
        selection_key="macd_signal",
    )
    hist = FeatureSpec(
        f"macd_histogram_{suffix}",
        "Float64",
        parameters,
        selection_key="macd_histogram",
    )

    def calculate(bars: Sequence[Bar]) -> FeatureTable:
        state = MacdState(
            fast_period=fast_period, slow_period=slow_period, signal_period=signal_period
        )
        columns: dict[str, list[float]] = {spec.name: [] for spec in (macd, signal, hist)}
        for bar in bars:
            value, sig = state.add(bar.close, True)
            assert sig is not None
            columns[macd.name].append(value)
            columns[signal.name].append(sig)
            columns[hist.name].append(value - sig)
        return FeatureTable.from_columns((macd, signal, hist), [b.time for b in bars], columns)

    def calculate_selected(bars: Sequence[Bar], names: frozenset[str]) -> FeatureTable:
        table = calculate(bars)
        selected = tuple(spec for spec in (macd, signal, hist) if spec.name in names)
        return FeatureTable(selected, table.frame[[spec.name for spec in selected]])

    common = {"lastValueVisible": True, "priceLineVisible": False}
    views = (
        FeatureViewSpec(
            macd.name,
            macd.name,
            "MACD",
            f"{fast_period}/{slow_period} EMA difference",
            "line",
            pane="separate",
            series_options={**common, "color": "#26a69a", "lineWidth": 2},
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
            selection_key="macd",
        ),
        FeatureViewSpec(
            signal.name,
            signal.name,
            "MACD signal",
            f"{signal_period}-period signal EMA",
            "line",
            pane="separate",
            series_options={**common, "color": "#ff9800", "lineWidth": 1},
            pane_height=160,
            selection_key="macd_signal",
        ),
        FeatureViewSpec(
            hist.name,
            hist.name,
            "MACD histogram",
            f"{fast_period}/{slow_period} EMA difference minus its "
            f"{signal_period}-period signal EMA",
            "histogram",
            pane="separate",
            series_options={**common, "color": "#787b86"},
            pane_height=160,
            selection_key="macd_histogram",
        ),
    )
    return FeatureDefinition(
        (macd, signal, hist),
        calculate,
        views,
        calculation_warm_up=max(100, slow_period, signal_period),
        calculate_selected=calculate_selected,
        settings=(
            FeatureSetting(
                "macd_fast_period", "MACD fast", DEFAULT_FAST, parameters=("fast_period",)
            ),
            FeatureSetting(
                "macd_slow_period", "MACD slow", DEFAULT_SLOW, 2, parameters=("slow_period",)
            ),
            FeatureSetting(
                "macd_signal_period", "MACD signal", DEFAULT_SIGNAL, parameters=("signal_period",)
            ),
        ),
        configure=lambda values: make_feature(
            values["macd_fast_period"], values["macd_slow_period"], values["macd_signal_period"]
        ),
    )


feature = make_feature()
