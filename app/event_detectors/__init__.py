"""Causal close-crossing events with stable signal/setup identities."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
from json import dumps
from math import isfinite
from typing import cast

from app.bars import Bar
from app.features.confirmed_swing import ConfirmedSwing, confirmed_swings
from app.features.ema import ema_step

DETECTORS = {"ema_breakout": "EMA close crossing", "swing_breakout": "Confirmed swing breakout"}


class _OutstandingSwings:
    """Price-indexed active pivots; range queries visit only setups that fire."""

    def __init__(self, swings: Sequence[ConfirmedSwing], direction: str) -> None:
        self.levels = sorted({swing.level for swing in swings if swing.direction == direction})
        self.buckets: list[list[ConfirmedSwing]] = [[] for _ in self.levels]
        self.counts = [0] * (len(self.levels) + 1)

    def _change_count(self, index: int, change: int) -> None:
        position = index + 1
        while position < len(self.counts):
            self.counts[position] += change
            position += position & -position

    def _count_before(self, end: int) -> int:
        count = 0
        while end:
            count += self.counts[end]
            end -= end & -end
        return count

    def _index_at_rank(self, rank: int) -> int:
        position = 0
        bit = 1 << (len(self.levels).bit_length() - 1)
        while bit:
            candidate = position + bit
            if candidate < len(self.counts) and self.counts[candidate] < rank:
                rank -= self.counts[candidate]
                position = candidate
            bit >>= 1
        return position

    def add(self, swing: ConfirmedSwing) -> None:
        index = bisect_left(self.levels, swing.level)
        self.buckets[index].append(swing)
        self._change_count(index, 1)

    def take_crossed(self, lower: float, upper: float) -> list[ConfirmedSwing]:
        start = bisect_right(self.levels, lower)
        end = bisect_left(self.levels, upper)
        crossed: list[ConfirmedSwing] = []
        first_rank = self._count_before(start) + 1
        while first_rank <= self._count_before(end):
            index = self._index_at_rank(first_rank)
            bucket = self.buckets[index]
            crossed.extend(bucket)
            self._change_count(index, -len(bucket))
            self.buckets[index] = []
        return crossed


@dataclass(frozen=True, slots=True)
class EventConfig:
    ema_period: int = 20
    swing_left: int = 3
    swing_right: int = 3
    buffer: float = 0.0

    def __post_init__(self) -> None:
        for name in ("ema_period", "swing_left", "swing_right"):
            value = getattr(self, name)
            maximum = 1000 if name == "ema_period" else 100
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError(f"{name} must be an integer between 1 and {maximum}")
        if (
            isinstance(self.buffer, bool)
            or not isinstance(self.buffer, (int, float))
            or not isfinite(self.buffer)
            or self.buffer < 0
        ):
            raise ValueError("buffer must be a finite non-negative number")


def _event(
    detector: str,
    direction: str,
    signal: Bar,
    level: float,
    setup: str,
    availability: int,
    config: EventConfig,
    dataset_id: str,
) -> dict[str, object]:
    configuration = (
        {"ema_period": config.ema_period, "buffer": config.buffer}
        if detector == "ema_breakout"
        else {
            "swing_left": config.swing_left,
            "swing_right": config.swing_right,
            "buffer": config.buffer,
        }
    )
    identity = [dataset_id, detector, configuration, direction, setup, signal.time]
    identifier = sha256(
        dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:24]
    return {
        "id": identifier,
        "detector": detector,
        "configuration": configuration,
        "direction": direction,
        "setup_id": setup,
        "signal_time": signal.time,
        "availability_time": availability,
        "broken_level": level,
        "breakout_price": signal.close,
        "reason": (
            f"close crossed {'above' if direction == 'bullish' else 'below'} {detector} level"
        ),
    }


def detect_events(
    bars: Sequence[Bar],
    config: EventConfig = EventConfig(),
    detectors: Sequence[str] = ("ema_breakout", "swing_breakout"),
    dataset_id: str = "",
) -> tuple[tuple[dict[str, object], ...], tuple[ConfirmedSwing, ...]]:
    unknown = set(detectors) - DETECTORS.keys()
    if unknown or len(detectors) != len(set(detectors)):
        raise ValueError(f"event detectors must be unique and known: {sorted(unknown)!r}")
    swings = confirmed_swings(bars, config.swing_left, config.swing_right)
    events: list[dict[str, object]] = []
    if "ema_breakout" in detectors:
        previous_ema: float | None = None
        for index, bar in enumerate(bars):
            current_ema = ema_step(previous_ema, bar.close, config.ema_period)
            if index:
                previous_close = bars[index - 1].close
                assert previous_ema is not None
                for direction, crossed in (
                    (
                        "bullish",
                        previous_close < previous_ema - config.buffer
                        and bar.close > current_ema + config.buffer,
                    ),
                    (
                        "bearish",
                        previous_close > previous_ema + config.buffer
                        and bar.close < current_ema - config.buffer,
                    ),
                ):
                    if crossed:
                        events.append(
                            _event(
                                "ema_breakout",
                                direction,
                                bar,
                                current_ema,
                                f"ema_{config.ema_period}",
                                bar.time,
                                config,
                                dataset_id,
                            )
                        )
            previous_ema = current_ema
    if "swing_breakout" in detectors:
        # The index contains future prices, but a pivot enters its active
        # bucket only after confirmation; future bars cannot affect signals.
        active = {
            "high": _OutstandingSwings(swings, "high"),
            "low": _OutstandingSwings(swings, "low"),
        }
        next_swing = 0
        for index, bar in enumerate(bars):
            while next_swing < len(swings) and swings[next_swing].availability_time < bar.time:
                swing = swings[next_swing]
                active[swing.direction].add(swing)
                next_swing += 1
            if not index:
                continue
            previous_close = bars[index - 1].close
            if bar.close == previous_close:
                continue
            swing_type = "high" if bar.close > previous_close else "low"
            direction = "bullish" if swing_type == "high" else "bearish"
            lower = (previous_close if swing_type == "high" else bar.close) + config.buffer
            upper = (bar.close if swing_type == "high" else previous_close) - config.buffer
            if lower >= upper:
                continue
            for swing in active[swing_type].take_crossed(lower, upper):
                events.append(
                    _event(
                        "swing_breakout",
                        direction,
                        bar,
                        swing.level,
                        f"{swing.direction}:{swing.pivot_time}",
                        swing.availability_time,
                        config,
                        dataset_id,
                    )
                )
    events.sort(
        key=lambda event: (
            cast(int, event["signal_time"]),
            str(event["detector"]),
            str(event["setup_id"]),
        )
    )
    return tuple(events), swings
