from __future__ import annotations

from collections.abc import Sequence

from app.bars import Bar

from . import Indicator, IndicatorPoint

PERIOD = 14


def calculate(bars: Sequence[Bar]) -> tuple[IndicatorPoint, ...]:
    if len(bars) <= PERIOD:
        return ()
    average_gain = 0.0
    average_loss = 0.0
    for previous, current in zip(bars, bars[1 : PERIOD + 1]):
        change = current.close - previous.close
        average_gain += max(change, 0)
        average_loss += max(-change, 0)
    average_gain /= PERIOD
    average_loss /= PERIOD
    points: list[IndicatorPoint] = []
    for index in range(PERIOD, len(bars)):
        if index > PERIOD:
            change = bars[index].close - bars[index - 1].close
            average_gain = (average_gain * (PERIOD - 1) + max(change, 0)) / PERIOD
            average_loss = (average_loss * (PERIOD - 1) + max(-change, 0)) / PERIOD
        if average_gain == 0 and average_loss == 0:
            value = 50.0
        elif average_loss == 0:
            value = 100.0
        else:
            value = 100 - 100 / (1 + average_gain / average_loss)
        points.append(IndicatorPoint(bars[index].time, value))
    return tuple(points)


indicator = Indicator(
    identifier="rsi_14",
    label="RSI 14",
    description="Separate 0–100 pane",
    series_type="LineSeries",
    pane="separate",
    calculate=calculate,
    series_options={
        "color": "#ab47bc",
        "lineWidth": 2,
        "lastValueVisible": True,
        "priceLineVisible": False,
    },
    pane_height=160,
    scale_range=(0, 100),
    reference_lines=(
        {
            "price": 70,
            "color": "#787b86",
            "lineStyle": 2,
            "lineWidth": 1,
            "axisLabelVisible": True,
            "title": "70",
        },
        {
            "price": 50,
            "color": "#555864",
            "lineStyle": 2,
            "lineWidth": 1,
            "axisLabelVisible": True,
            "title": "50",
        },
        {
            "price": 30,
            "color": "#787b86",
            "lineStyle": 2,
            "lineWidth": 1,
            "axisLabelVisible": True,
            "title": "30",
        },
    ),
)
