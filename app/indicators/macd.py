from __future__ import annotations

from collections.abc import Sequence

from app.bars import Bar

from . import Indicator, IndicatorPoint

FAST_PERIOD = 12
SLOW_PERIOD = 26


def calculate(bars: Sequence[Bar]) -> tuple[IndicatorPoint, ...]:
    if not bars:
        return ()
    fast_multiplier = 2 / (FAST_PERIOD + 1)
    slow_multiplier = 2 / (SLOW_PERIOD + 1)
    fast = slow = bars[0].close
    points = [IndicatorPoint(bars[0].time, 0.0)]
    for bar in bars[1:]:
        fast = (bar.close - fast) * fast_multiplier + fast
        slow = (bar.close - slow) * slow_multiplier + slow
        points.append(IndicatorPoint(bar.time, fast - slow))
    return tuple(points)


indicator = Indicator(
    identifier="macd",
    label="MACD",
    description="12/26 exponential moving-average difference",
    series_type="LineSeries",
    pane="separate",
    calculate=calculate,
    series_options={
        "color": "#26a69a",
        "lineWidth": 2,
        "lastValueVisible": True,
        "priceLineVisible": False,
    },
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
)
