"""Signal when a run first reaches three bullish candles."""

from __future__ import annotations

from collections.abc import Sequence

from app.bars import Bar

from . import DetectorSetting, Event, EventConfig, EventDetector, make_event


def detect(bars: Sequence[Bar], config: EventConfig, dataset_id: str) -> tuple[Event, ...]:
    events: list[Event] = []
    minimum_body = config.detector_settings.get("three_bullish_candles.minimum_body", 0)
    for index in range(2, len(bars)):
        run = bars[index - 2 : index + 1]
        if not all(bar.close - bar.open > minimum_body for bar in run):
            continue
        if index > 2 and bars[index - 3].close - bars[index - 3].open > minimum_body:
            continue
        signal = bars[index]
        first = run[0]
        events.append(
            make_event(
                "three_bullish_candles",
                "bullish",
                signal,
                None,
                f"run:{first.time}",
                signal.time,
                {"minimum_body": minimum_body},
                dataset_id,
                "three consecutive bullish candles",
            )
        )
    return tuple(events)


detector = EventDetector(
    "three_bullish_candles",
    "Three bullish candles",
    detect,
    (DetectorSetting("minimum_body", "Minimum bullish body", 0, minimum=0),),
)
