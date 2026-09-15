from __future__ import annotations

from collections.abc import Sequence

from app.bars import Bar

from . import FeatureDefinition, FeatureSpec, FeatureTable, FeatureViewSpec

VOLUME = FeatureSpec("volume", "Float64", version="1")
VOLUME_UP = FeatureSpec("volume_up", "boolean", version="1")


def calculate(bars: Sequence[Bar]) -> FeatureTable:
    return FeatureTable.from_columns(
        (VOLUME, VOLUME_UP),
        [bar.time for bar in bars],
        {
            VOLUME.name: [bar.volume for bar in bars],
            VOLUME_UP.name: [bar.close >= bar.open for bar in bars],
        },
    )


feature = FeatureDefinition(
    (VOLUME, VOLUME_UP),
    calculate,
    (
        FeatureViewSpec(
            "volume",
            VOLUME.name,
            "Volume",
            "Lower chart overlay",
            "histogram",
            show_in_crosshair=True,
            default_applied=True,
            series_options={
                "priceFormat": {"type": "volume"},
                "priceScaleId": "volume",
                "lastValueVisible": False,
            },
            price_scale_options={"scaleMargins": {"top": 0.8, "bottom": 0}},
            color_feature=VOLUME_UP.name,
        ),
        FeatureViewSpec(
            "volume_up",
            VOLUME_UP.name,
            "Up bar",
            "Close is at or above open",
            None,
            show_in_crosshair=True,
        ),
    ),
)
