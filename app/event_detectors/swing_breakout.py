"""Breakouts of the channel formed by previously confirmed swing extrema."""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence

from app.bars import Bar
from app.features.confirmed_swing import ConfirmedSwing, confirmed_swings

from . import Event, EventConfig, EventDetector, make_event


def detect(bars: Sequence[Bar], config: EventConfig, dataset_id: str) -> tuple[Event, ...]:
    swings = confirmed_swings(bars, config.swing_left, config.swing_right)
    positions = {bar.time: index for index, bar in enumerate(bars)}
    upper_candidates: deque[ConfirmedSwing] = deque()
    lower_candidates: deque[ConfirmedSwing] = deque()
    next_swing = 0
    events: list[Event] = []
    configuration = {
        "swing_lookback": config.swing_lookback,
        "swing_left": config.swing_left,
        "swing_right": config.swing_right,
        "buffer": config.buffer,
    }

    for index, bar in enumerate(bars):
        if not index:
            continue
        while next_swing < len(swings) and positions[swings[next_swing].availability_time] < index:
            swing = swings[next_swing]
            candidates = upper_candidates if swing.direction == "high" else lower_candidates
            while candidates and (
                candidates[-1].level < swing.level
                if swing.direction == "high"
                else candidates[-1].level > swing.level
            ):
                candidates.pop()
            candidates.append(swing)
            next_swing += 1
        for candidates in (upper_candidates, lower_candidates):
            while (
                candidates and positions[candidates[0].pivot_time] < index - config.swing_lookback
            ):
                candidates.popleft()
        upper = upper_candidates[0] if upper_candidates else None
        lower = lower_candidates[0] if lower_candidates else None

        if upper is not None and (
            bar.open < upper.level - config.buffer and bar.close > upper.level + config.buffer
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
            bar.open > lower.level + config.buffer and bar.close < lower.level - config.buffer
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


detector = EventDetector("swing_breakout", "Rolling confirmed swing breakout", detect)
