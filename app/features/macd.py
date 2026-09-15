from __future__ import annotations

from collections.abc import Sequence

import pandas as pd

from app.bars import Bar

from . import FeatureDefinition, FeatureSpec, FeatureTable, FeatureViewSpec

MACD = FeatureSpec("macd", "Float64", {"fast_period": 12, "slow_period": 26}, version="1")
SIGNAL = FeatureSpec("macd_signal", "Float64", {"period": 9}, version="1")
HISTOGRAM = FeatureSpec("macd_histogram", "Float64", version="1")


def calculate(bars: Sequence[Bar]) -> FeatureTable:
    closes = pd.Series([bar.close for bar in bars], dtype="Float64")
    macd = closes.ewm(span=12, adjust=False).mean() - closes.ewm(span=26, adjust=False).mean()
    signal = macd.ewm(span=9, adjust=False).mean()
    return FeatureTable.from_columns(
        (MACD, SIGNAL, HISTOGRAM),
        [bar.time for bar in bars],
        {MACD.name: macd, SIGNAL.name: signal, HISTOGRAM.name: macd - signal},
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
)
