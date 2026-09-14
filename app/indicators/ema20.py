from __future__ import annotations

from collections.abc import Sequence

from app.bars import Bar

from . import Indicator, IndicatorPoint

PERIOD = 20


def calculate(bars: Sequence[Bar]) -> tuple[IndicatorPoint, ...]:
    if not bars:
        return ()
    multiplier = 2 / (PERIOD + 1)
    value = bars[0].close
    points = [IndicatorPoint(bars[0].time, value)]
    for bar in bars[1:]:
        value = (bar.close - value) * multiplier + value
        points.append(IndicatorPoint(bar.time, value))
    return tuple(points)


indicator = Indicator(
    identifier="ema_20",
    label="EMA 20",
    description="Price overlay",
    series_type="LineSeries",
    pane="main",
    calculate=calculate,
    series_options={
        "color": "#ff9800",
        "lineWidth": 2,
        "lastValueVisible": False,
        "priceLineVisible": False,
    },
)
