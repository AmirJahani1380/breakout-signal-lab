from __future__ import annotations

from collections.abc import Sequence

from app.bars import Bar

from . import FeatureDefinition, FeatureSpec, FeatureTable, FeatureViewSpec

PERIOD = 14
SPEC = FeatureSpec("rsi_14", "Float64", {"period": PERIOD}, warm_up=PERIOD, version="1")


def calculate(bars: Sequence[Bar]) -> FeatureTable:
    values: list[float | None] = [None] * min(PERIOD, len(bars))
    if len(bars) > PERIOD:
        changes = [current.close - previous.close for previous, current in zip(bars, bars[1:])]
        average_gain = sum(max(change, 0) for change in changes[:PERIOD]) / PERIOD
        average_loss = sum(max(-change, 0) for change in changes[:PERIOD]) / PERIOD
        for index in range(PERIOD, len(bars)):
            if index > PERIOD:
                change = changes[index - 1]
                average_gain = (average_gain * (PERIOD - 1) + max(change, 0)) / PERIOD
                average_loss = (average_loss * (PERIOD - 1) + max(-change, 0)) / PERIOD
            if average_gain == average_loss == 0:
                values.append(50.0)
            elif average_loss == 0:
                values.append(100.0)
            else:
                values.append(100 - 100 / (1 + average_gain / average_loss))
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
