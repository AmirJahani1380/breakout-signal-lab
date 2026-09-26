"""Discoverable close-crossing event detectors."""

from __future__ import annotations

import importlib
import json
import logging
import pkgutil
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from math import isfinite
from typing import Literal, cast

from app.bars import Bar
from app.features.confirmed_swing import ConfirmedSwing, confirmed_swings

logger = logging.getLogger(__name__)
DETECTOR_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
Event = dict[str, object]
Direction = Literal["bullish", "bearish"]


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


DetectFunction = Callable[[Sequence[Bar], EventConfig, str], Sequence[Event]]


@dataclass(frozen=True, slots=True)
class EventDetector:
    """A detector module's stable identifier, UI label, and event calculation."""

    identifier: str
    label: str
    detect: DetectFunction

    def __post_init__(self) -> None:
        if not isinstance(self.identifier, str) or not DETECTOR_ID_PATTERN.fullmatch(
            self.identifier
        ):
            raise ValueError(
                "detector identifier must contain lowercase letters, numbers, or underscores"
            )
        if not isinstance(self.label, str) or not self.label.strip():
            raise ValueError("detector label must not be empty")
        if not callable(self.detect):
            raise ValueError("detector detect must be callable")


def make_event(
    detector: str,
    direction: Direction,
    signal: Bar,
    broken_level: float,
    setup_id: str,
    availability_time: int,
    configuration: Mapping[str, object],
    dataset_id: str = "",
) -> Event:
    """Build the shared event response shape and deterministic event ID."""
    if direction not in ("bullish", "bearish"):
        raise ValueError("event direction must be bullish or bearish")
    try:
        normalized_configuration = dict(configuration)
        encoded_configuration = json.dumps(
            normalized_configuration, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
    except (TypeError, ValueError) as error:
        raise ValueError(f"event configuration must be finite JSON data: {error}") from error
    identity = [
        dataset_id,
        detector,
        json.loads(encoded_configuration),
        direction,
        setup_id,
        signal.time,
    ]
    identifier = sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:24]
    return {
        "id": identifier,
        "detector": detector,
        "configuration": normalized_configuration,
        "direction": direction,
        "setup_id": setup_id,
        "signal_time": signal.time,
        "availability_time": availability_time,
        "broken_level": broken_level,
        "breakout_price": signal.close,
        "reason": (
            f"close crossed {'above' if direction == 'bullish' else 'below'} {detector} level"
        ),
    }


def discover_detectors() -> dict[str, EventDetector]:
    """Load valid modules that export an EventDetector named ``detector``."""
    discovered: dict[str, EventDetector] = {}
    for module_info in pkgutil.iter_modules(__path__, f"{__name__}."):
        try:
            module = importlib.import_module(module_info.name)
            detector = getattr(module, "detector", None)
            if not isinstance(detector, EventDetector):
                raise ValueError("module must export an EventDetector named 'detector'")
            if detector.identifier in discovered:
                raise ValueError(f"duplicate detector identifier {detector.identifier!r}")
        except Exception as error:
            logger.warning("Skipping event detector module %s: %s", module_info.name, error)
            continue
        discovered[detector.identifier] = detector
    return discovered


def _validated_event(
    detector_id: str, index: int, candidate: object, closes: Mapping[int, float]
) -> Event:
    prefix = f"detector {detector_id!r} event {index}"
    required = {
        "id",
        "detector",
        "configuration",
        "direction",
        "setup_id",
        "signal_time",
        "availability_time",
        "broken_level",
        "breakout_price",
        "reason",
    }
    if not isinstance(candidate, dict):
        raise ValueError(f"{prefix} must be a record from make_event")
    missing = required - candidate.keys()
    if missing:
        raise ValueError(f"{prefix} is missing fields: {sorted(missing)!r}")
    if candidate["detector"] != detector_id:
        raise ValueError(f"{prefix} has a different detector identifier")
    for field in ("id", "setup_id", "reason"):
        if not isinstance(candidate[field], str) or not candidate[field].strip():
            raise ValueError(f"{prefix} {field} must be a non-empty string")
    if candidate["direction"] not in ("bullish", "bearish"):
        raise ValueError(f"{prefix} direction must be bullish or bearish")
    signal_time = candidate["signal_time"]
    availability_time = candidate["availability_time"]
    if type(signal_time) is not int or signal_time not in closes:
        raise ValueError(f"{prefix} signal_time must identify a source bar")
    if type(availability_time) is not int or availability_time > signal_time:
        raise ValueError(f"{prefix} availability_time must be no later than signal_time")
    for field in ("broken_level", "breakout_price"):
        number = candidate[field]
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not isfinite(number):
            raise ValueError(f"{prefix} {field} must be a finite number")
    if candidate["breakout_price"] != closes[signal_time]:
        raise ValueError(f"{prefix} breakout_price must match the signal close")
    if not isinstance(candidate["configuration"], dict):
        raise ValueError(f"{prefix} configuration must be a JSON object")
    try:
        json.dumps(candidate, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{prefix} must contain finite JSON values: {error}") from error
    return candidate


def detect_events(
    bars: Sequence[Bar],
    config: EventConfig = EventConfig(),
    detectors: Sequence[str] | None = None,
    dataset_id: str = "",
    registry: Mapping[str, EventDetector] | None = None,
) -> tuple[tuple[Event, ...], tuple[ConfirmedSwing, ...]]:
    """Run selected detector modules and return their events plus confirmed pivots."""
    registered = DETECTORS if registry is None else registry
    selected = tuple(registered) if detectors is None else tuple(detectors)
    unknown = set(selected) - registered.keys()
    if unknown or len(selected) != len(set(selected)):
        raise ValueError(f"event detectors must be unique and known: {sorted(unknown)!r}")

    closes = {bar.time: bar.close for bar in bars}
    events: list[Event] = []
    for identifier in selected:
        detected = registered[identifier].detect(bars, config, dataset_id)
        if not isinstance(detected, Sequence) or isinstance(detected, (str, bytes)):
            raise ValueError(f"detector {identifier!r} must return a sequence of event records")
        events.extend(
            _validated_event(identifier, index, event, closes)
            for index, event in enumerate(detected)
        )
    swings = confirmed_swings(bars, config.swing_left, config.swing_right)
    events.sort(
        key=lambda event: (
            cast(int, event["signal_time"]),
            str(event["detector"]),
            str(event["setup_id"]),
        )
    )
    return tuple(events), swings


DETECTORS = discover_detectors()
