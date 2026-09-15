from __future__ import annotations

import re
from collections.abc import Mapping
from math import isfinite

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
MARKER_OPTION_FIELDS = frozenset({"color", "position", "shape", "size", "text"})
REFERENCE_LINE_FIELDS = frozenset(
    {"axisLabelVisible", "color", "lineStyle", "lineVisible", "lineWidth", "price", "title"}
)


def validate_view_metadata(
    renderer: str | None,
    series_options: Mapping[str, object],
    price_scale_options: Mapping[str, object],
    reference_lines: tuple[Mapping[str, object], ...],
) -> None:
    if not isinstance(series_options, Mapping):
        raise ValueError("series_options must be a mapping")
    if renderer == "marker":
        _validate_marker_options(series_options)
    elif renderer is None:
        if series_options:
            raise ValueError("a view without a renderer cannot have series_options")
    else:
        _validate_series_options(series_options)
    _validate_price_scale_options(price_scale_options)
    _validate_reference_lines(reference_lines)


def _validate_series_options(options: Mapping[str, object]) -> None:
    unknown = set(options) - SERIES_OPTION_FIELDS
    if unknown:
        raise ValueError(f"unsupported series_options fields: {sorted(unknown)!r}")
    for field_name in ("lastValueVisible", "priceLineVisible"):
        if field_name in options and not isinstance(options[field_name], bool):
            raise ValueError(f"series_options.{field_name} must be a boolean")
    if "color" in options:
        _color(options["color"], "series_options.color")
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


def _validate_marker_options(options: Mapping[str, object]) -> None:
    unknown = set(options) - MARKER_OPTION_FIELDS
    if unknown:
        raise ValueError(f"unsupported marker series_options fields: {sorted(unknown)!r}")
    _color(options.get("color", "#2962ff"), "series_options.color")
    if options.get("position", "atPriceMiddle") not in {
        "aboveBar",
        "belowBar",
        "inBar",
        "atPriceTop",
        "atPriceBottom",
        "atPriceMiddle",
    }:
        raise ValueError("series_options.position is not a supported marker position")
    if options.get("shape", "circle") not in {"arrowUp", "arrowDown", "circle", "square"}:
        raise ValueError("series_options.shape is not a supported marker shape")
    if "text" in options and not isinstance(options["text"], str):
        raise ValueError("series_options.text must be a string")
    if "size" in options and (
        isinstance(options["size"], bool)
        or not isinstance(options["size"], (int, float))
        or not isfinite(options["size"])
        or options["size"] <= 0
    ):
        raise ValueError("series_options.size must be a positive finite number")


def _validate_price_format(value: object) -> None:
    if not isinstance(value, Mapping):
        raise ValueError("series_options.priceFormat must be a mapping")
    unknown = set(value) - {"type", "precision", "minMove"}
    if unknown:
        raise ValueError(f"unsupported priceFormat fields: {sorted(unknown)!r}")
    if value.get("type") not in {"price", "volume", "percent"}:
        raise ValueError("priceFormat.type must be 'price', 'volume', or 'percent'")
    if "precision" in value:
        _integer_range(value["precision"], "priceFormat.precision", 0, 15)
    if "minMove" in value and _number(value["minMove"], "priceFormat.minMove") <= 0:
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
