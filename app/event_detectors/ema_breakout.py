"""EMA close-crossing breakout detector."""

from __future__ import annotations

from collections.abc import Sequence

from app.bars import Bar
from app.features.ema import ema_step

from . import Direction, Event, EventConfig, EventDetector, make_event


def detect(bars: Sequence[Bar], config: EventConfig, dataset_id: str) -> tuple[Event, ...]:
    events: list[Event] = []
    previous_ema: float | None = None
    configuration = {"ema_period": config.ema_period, "buffer": config.buffer}
    for bar in bars:
        current_ema = ema_step(previous_ema, bar.close, config.ema_period)
        if previous_ema is not None:
            crossings: tuple[tuple[Direction, bool], ...] = (
                (
                    "bullish",
                    bar.open < current_ema - config.buffer
                    and bar.close > current_ema + config.buffer,
                ),
                (
                    "bearish",
                    bar.open > current_ema + config.buffer
                    and bar.close < current_ema - config.buffer,
                ),
            )
            for direction, crossed in crossings:
                if crossed:
                    events.append(
                        make_event(
                            "ema_breakout",
                            direction,
                            bar,
                            current_ema,
                            f"ema_{config.ema_period}",
                            bar.time,
                            configuration,
                            dataset_id,
                        )
                    )
        previous_ema = current_ema
    return tuple(events)


detector = EventDetector("ema_breakout", "EMA close crossing", detect)
