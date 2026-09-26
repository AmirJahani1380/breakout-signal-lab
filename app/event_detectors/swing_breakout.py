"""Breakouts of the channel formed by previously confirmed swing extrema."""

from __future__ import annotations

from collections.abc import Sequence

from app.bars import Bar
from app.features.confirmed_swing import ConfirmedSwing, confirmed_swings

from . import Event, EventConfig, EventDetector, make_event


def detect(bars: Sequence[Bar], config: EventConfig, dataset_id: str) -> tuple[Event, ...]:
    swings = confirmed_swings(bars, config.swing_left, config.swing_right)
    upper: ConfirmedSwing | None = None
    lower: ConfirmedSwing | None = None
    next_swing = 0
    events: list[Event] = []
    configuration = {
        "swing_left": config.swing_left,
        "swing_right": config.swing_right,
        "buffer": config.buffer,
    }

    for index, bar in enumerate(bars):
        if not index:
            continue
        previous = bars[index - 1]
        while next_swing < len(swings) and swings[next_swing].availability_time <= previous.time:
            swing = swings[next_swing]
            if swing.direction == "high" and (upper is None or swing.level > upper.level):
                upper = swing
            elif swing.direction == "low" and (lower is None or swing.level < lower.level):
                lower = swing
            next_swing += 1

        if upper is not None and (
            previous.close < upper.level - config.buffer and bar.close > upper.level + config.buffer
        ):
            events.append(
                make_event(
                    "swing_breakout",
                    "bullish",
                    bar,
                    upper.level,
                    f"high:{upper.pivot_time}",
                    upper.availability_time,
                    configuration,
                    dataset_id,
                )
            )
        if lower is not None and (
            previous.close > lower.level + config.buffer and bar.close < lower.level - config.buffer
        ):
            events.append(
                make_event(
                    "swing_breakout",
                    "bearish",
                    bar,
                    lower.level,
                    f"low:{lower.pivot_time}",
                    lower.availability_time,
                    configuration,
                    dataset_id,
                )
            )
    return tuple(events)


detector = EventDetector("swing_breakout", "Confirmed swing breakout", detect)
