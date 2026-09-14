from __future__ import annotations

from collections.abc import Sequence

from app.bars import Bar

from . import Indicator, IndicatorPoint


def calculate(bars: Sequence[Bar]) -> tuple[IndicatorPoint, ...]:
    return tuple(
        IndicatorPoint(
            bar.time,
            bar.volume,
            "#00d08499" if bar.close >= bar.open else "#ff4d6d99",
        )
        for bar in bars
    )


indicator = Indicator(
    identifier="volume",
    label="Volume",
    description="Lower chart overlay",
    series_type="HistogramSeries",
    pane="main",
    calculate=calculate,
    default_applied=True,
    series_options={
        "priceFormat": {"type": "volume"},
        "priceScaleId": "volume",
        "lastValueVisible": False,
    },
    price_scale_options={"scaleMargins": {"top": 0.8, "bottom": 0}},
)
