from __future__ import annotations

import importlib
import json
import logging
import pkgutil
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from math import isfinite
from typing import Literal

from app.bars import Bar

logger = logging.getLogger(__name__)
SUPPORTED_SERIES = frozenset({"HistogramSeries", "LineSeries"})
IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
COLOR_PATTERN = re.compile(r"^#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?$")
SERIES_OPTION_FIELDS = frozenset(
    {
        "base",
        "color",
        "lastValueVisible",
        "lineStyle",
        "lineWidth",
        "priceFormat",
        "priceLineVisible",
        "priceScaleId",
        "title",
    }
)
REFERENCE_LINE_FIELDS = frozenset(
    {"axisLabelVisible", "color", "lineStyle", "lineVisible", "lineWidth", "price", "title"}
)


@dataclass(frozen=True, slots=True)
class IndicatorPoint:
    time: int
    value: float
    color: str | None = None

    def as_dict(self) -> dict[str, int | float | str]:
        point: dict[str, int | float | str] = {"time": self.time, "value": self.value}
        if self.color is not None:
            point["color"] = self.color
        return point


@dataclass(frozen=True, slots=True)
class Indicator:
    identifier: str
    label: str
    description: str
    series_type: str
    pane: Literal["main", "separate"]
    calculate: Callable[[Sequence[Bar]], tuple[IndicatorPoint, ...]]
    default_applied: bool = False
    default_visible: bool = True
    series_options: Mapping[str, object] = field(default_factory=dict)
    price_scale_options: Mapping[str, object] = field(default_factory=dict)
    pane_height: int | None = None
    scale_range: tuple[float, float] | None = None
    reference_lines: tuple[Mapping[str, object], ...] = ()

    def definition(self, points: Sequence[IndicatorPoint]) -> dict[str, object]:
        return {
            "id": self.identifier,
            "label": self.label,
            "description": self.description,
            "series_type": self.series_type,
            "pane": self.pane,
            "default_applied": self.default_applied,
            "default_visible": self.default_visible,
            "series_options": dict(self.series_options),
            "price_scale_options": dict(self.price_scale_options),
            "pane_height": self.pane_height,
            "scale_range": self.scale_range,
            "reference_lines": [dict(line) for line in self.reference_lines],
            "points": [point.as_dict() for point in points],
        }


def discover() -> tuple[Indicator, ...]:
    indicators: list[Indicator] = []
    identifiers: set[str] = set()
    for module_info in pkgutil.iter_modules(__path__, f"{__name__}."):
        try:
            module = importlib.import_module(module_info.name)
            indicator = _validate(getattr(module, "indicator", None))
            if indicator.identifier in identifiers:
                raise ValueError(f"duplicate id {indicator.identifier!r}")
        except Exception as error:
            logger.warning("Skipping indicator module %s: %s", module_info.name, error)
            continue
        identifiers.add(indicator.identifier)
        indicators.append(indicator)
    return tuple(sorted(indicators, key=lambda entry: entry.label))


def calculate(indicator: Indicator, bars: Sequence[Bar]) -> tuple[IndicatorPoint, ...] | None:
    try:
        points = indicator.calculate(bars)
        if not isinstance(points, tuple):
            raise ValueError("calculate() must return a tuple of IndicatorPoint values")
        valid_times = {bar.time for bar in bars}
        previous_time: int | None = None
        for point in points:
            if not isinstance(point, IndicatorPoint):
                raise ValueError("calculate() must return IndicatorPoint values")
            if not isinstance(point.time, int) or isinstance(point.time, bool):
                raise ValueError("point times must be integer Unix timestamps")
            if point.time not in valid_times:
                raise ValueError(f"point time {point.time} is not present in the source bars")
            if previous_time is not None and point.time <= previous_time:
                raise ValueError("point times must be unique and chronological")
            if isinstance(point.value, bool) or not isfinite(point.value):
                raise ValueError(f"point at {point.time} is not finite")
            if point.color is not None:
                _color(point.color, f"point color at {point.time}")
            previous_time = point.time
        return points
    except Exception as error:
        logger.warning("Skipping indicator %s values: %s", indicator.identifier, error)
        return None


def _validate(indicator: object) -> Indicator:
    if not isinstance(indicator, Indicator):
        raise ValueError("module must export an Indicator named 'indicator'")
    if not IDENTIFIER_PATTERN.fullmatch(indicator.identifier):
        raise ValueError("id must contain lowercase letters, numbers, or underscores")
    if not indicator.label.strip() or not indicator.description.strip():
        raise ValueError("label and description must not be empty")
    if indicator.series_type not in SUPPORTED_SERIES:
        raise ValueError(f"unsupported series type {indicator.series_type!r}")
    if indicator.pane not in ("main", "separate"):
        raise ValueError("pane must be 'main' or 'separate'")
    if not callable(indicator.calculate):
        raise ValueError("calculate must be callable")
    if not isinstance(indicator.default_applied, bool) or not isinstance(
        indicator.default_visible, bool
    ):
        raise ValueError("default applied and visible values must be booleans")
    _validate_series_options(indicator.series_options)
    _validate_price_scale_options(indicator.price_scale_options)
    _validate_reference_lines(indicator.reference_lines)
    if indicator.pane_height is not None and (
        not isinstance(indicator.pane_height, int)
        or isinstance(indicator.pane_height, bool)
        or indicator.pane_height <= 0
    ):
        raise ValueError("pane_height must be a positive integer")
    if indicator.scale_range is not None:
        if not isinstance(indicator.scale_range, tuple) or len(indicator.scale_range) != 2:
            raise ValueError("scale_range must be a two-number tuple")
        minimum, maximum = indicator.scale_range
        _number(minimum, "scale_range minimum")
        _number(maximum, "scale_range maximum")
        if minimum >= maximum:
            raise ValueError("scale_range must contain increasing finite values")
    try:
        json.dumps(indicator.definition(()), allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError(f"metadata must be JSON-compatible: {error}") from error
    return indicator


def _validate_series_options(options: Mapping[str, object]) -> None:
    if not isinstance(options, Mapping):
        raise ValueError("series_options must be a mapping")
    unknown = set(options) - SERIES_OPTION_FIELDS
    if unknown:
        raise ValueError(f"unsupported series_options fields: {sorted(unknown)!r}")
    for field_name in ("lastValueVisible", "priceLineVisible"):
        if field_name in options and not isinstance(options[field_name], bool):
            raise ValueError(f"series_options.{field_name} must be a boolean")
    for field_name in ("color",):
        if field_name in options:
            _color(options[field_name], f"series_options.{field_name}")
    for field_name in ("title", "priceScaleId"):
        if field_name in options and (
            not isinstance(options[field_name], str) or not options[field_name]
        ):
            raise ValueError(f"series_options.{field_name} must be a non-empty string")
    if "lineWidth" in options:
        _integer_range(options["lineWidth"], "series_options.lineWidth", 1, 4)
    if "lineStyle" in options:
        _integer_range(options["lineStyle"], "series_options.lineStyle", 0, 4)
    if "base" in options:
        _number(options["base"], "series_options.base")
    if "priceFormat" in options:
        _validate_price_format(options["priceFormat"])


def _validate_price_format(price_format: object) -> None:
    if not isinstance(price_format, Mapping):
        raise ValueError("series_options.priceFormat must be a mapping")
    unknown = set(price_format) - {"type", "precision", "minMove"}
    if unknown:
        raise ValueError(f"unsupported priceFormat fields: {sorted(unknown)!r}")
    if price_format.get("type") not in {"price", "volume", "percent"}:
        raise ValueError("priceFormat.type must be 'price', 'volume', or 'percent'")
    if "precision" in price_format:
        _integer_range(price_format["precision"], "priceFormat.precision", 0, 15)
    if "minMove" in price_format and _number(price_format["minMove"], "priceFormat.minMove") <= 0:
        raise ValueError("priceFormat.minMove must be positive")


def _validate_price_scale_options(options: Mapping[str, object]) -> None:
    if not isinstance(options, Mapping):
        raise ValueError("price_scale_options must be a mapping")
    if not options:
        return
    if set(options) != {"scaleMargins"}:
        raise ValueError("price_scale_options supports only scaleMargins")
    margins = options["scaleMargins"]
    if not isinstance(margins, Mapping) or set(margins) != {"top", "bottom"}:
        raise ValueError("scaleMargins must contain top and bottom")
    top = _number(margins["top"], "scaleMargins.top")
    bottom = _number(margins["bottom"], "scaleMargins.bottom")
    if not 0 <= top < 1 or not 0 <= bottom < 1 or top + bottom >= 1:
        raise ValueError("scaleMargins must be between 0 and 1 and total less than 1")


def _validate_reference_lines(lines: tuple[Mapping[str, object], ...]) -> None:
    if not isinstance(lines, tuple):
        raise ValueError("reference_lines must be a tuple")
    for index, line in enumerate(lines):
        name = f"reference_lines[{index}]"
        if not isinstance(line, Mapping):
            raise ValueError(f"{name} must be a mapping")
        unknown = set(line) - REFERENCE_LINE_FIELDS
        if unknown:
            raise ValueError(f"unsupported {name} fields: {sorted(unknown)!r}")
        if "price" not in line:
            raise ValueError(f"{name}.price is required")
        _number(line["price"], f"{name}.price")
        if "color" in line:
            _color(line["color"], f"{name}.color")
        if "lineWidth" in line:
            _integer_range(line["lineWidth"], f"{name}.lineWidth", 1, 4)
        if "lineStyle" in line:
            _integer_range(line["lineStyle"], f"{name}.lineStyle", 0, 4)
        for field_name in ("axisLabelVisible", "lineVisible"):
            if field_name in line and not isinstance(line[field_name], bool):
                raise ValueError(f"{name}.{field_name} must be a boolean")
        if "title" in line and not isinstance(line["title"], str):
            raise ValueError(f"{name}.title must be a string")


def _color(value: object, name: str) -> None:
    if not isinstance(value, str) or not COLOR_PATTERN.fullmatch(value):
        raise ValueError(f"{name} must be a 6- or 8-digit hex color")


def _integer_range(value: object, name: str, minimum: int, maximum: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer from {minimum} to {maximum}")


def _number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return float(value)
