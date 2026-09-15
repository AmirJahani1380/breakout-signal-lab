from __future__ import annotations

from collections.abc import Sequence

import pandas as pd

from app.bars import Bar

from . import FeatureDefinition, FeatureSpec, FeatureTable, FeatureViewSpec

MACD = FeatureSpec("macd", "Float64", {"fast_period": 12, "slow_period": 26}, version="1")
SIGNAL = FeatureSpec("macd_signal", "Float64", {"period": 9}, version="1")
HISTOGRAM = FeatureSpec("macd_histogram", "Float64", version="1")


def calculate(bars: Sequence[Bar]) -> FeatureTable:
    return calculate_selected(bars, frozenset({MACD.name, SIGNAL.name, HISTOGRAM.name}))


def calculate_selected(bars: Sequence[Bar], names: frozenset[str]) -> FeatureTable:
    closes = pd.Series([bar.close for bar in bars], dtype="Float64")
    macd = closes.ewm(span=12, adjust=False).mean() - closes.ewm(span=26, adjust=False).mean()
    columns: dict[str, pd.Series] = {}
    if MACD.name in names:
        columns[MACD.name] = macd
    if SIGNAL.name in names or HISTOGRAM.name in names:
        signal = macd.ewm(span=9, adjust=False).mean()
        if SIGNAL.name in names:
            columns[SIGNAL.name] = signal
        if HISTOGRAM.name in names:
            columns[HISTOGRAM.name] = macd - signal
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
