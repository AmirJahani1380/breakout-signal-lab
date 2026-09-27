from __future__ import annotations

import importlib
import json
import logging
import pkgutil
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from math import isfinite
from pathlib import Path
from typing import Any, Literal, cast

import pandas as pd

from app.bars import Bar

from .view_validation import COLOR_PATTERN, validate_view_metadata

logger = logging.getLogger(__name__)
NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
FEATURE_DTYPES = frozenset({"Float64", "Int64", "boolean"})
RENDERERS = frozenset({"line", "histogram", "marker"})


@dataclass(frozen=True, slots=True)
class FeatureSpec:
    name: str
    dtype: Literal["Float64", "Int64", "boolean"]
    parameters: Mapping[str, object] = field(default_factory=dict)
    warm_up: int = 0
    version: str = "1"
    causality: Literal["causal", "non_causal"] = "causal"
    source: Literal["computed", "imported"] = "computed"
    selection_key: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not NAME_PATTERN.fullmatch(self.name):
            raise ValueError("feature name must contain lowercase letters, numbers, or underscores")
        if self.dtype not in FEATURE_DTYPES:
            raise ValueError(f"unsupported feature dtype {self.dtype!r}")
        if not isinstance(self.warm_up, int) or isinstance(self.warm_up, bool) or self.warm_up < 0:
            raise ValueError("warm_up must be a non-negative integer")
        if not isinstance(self.version, str) or not self.version.strip():
            raise ValueError("version must be a non-empty string")
        if self.causality not in ("causal", "non_causal"):
            raise ValueError("causality must be 'causal' or 'non_causal'")
        if self.source not in ("computed", "imported"):
            raise ValueError("source must be 'computed' or 'imported'")
        if self.selection_key is not None and not NAME_PATTERN.fullmatch(self.selection_key):
            raise ValueError(
                "selection_key must contain lowercase letters, numbers, or underscores"
            )
        try:
            json.dumps(dict(self.parameters), allow_nan=False)
        except (TypeError, ValueError) as error:
            raise ValueError(f"parameters must be JSON-compatible: {error}") from error
        object.__setattr__(self, "parameters", dict(self.parameters))

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "dtype": self.dtype,
            "parameters": dict(self.parameters),
            "warm_up": self.warm_up,
            "version": self.version,
            "causality": self.causality,
            "source": self.source,
            **({"selection_key": self.selection_key} if self.selection_key else {}),
        }


@dataclass(frozen=True, slots=True)
class FeatureTable:
    specs: tuple[FeatureSpec, ...]
    frame: pd.DataFrame

    def __post_init__(self) -> None:
        if not self.specs:
            raise ValueError("a feature table must contain at least one feature")
        names = [spec.name for spec in self.specs]
        if len(names) != len(set(names)):
            raise ValueError("feature names must be unique")
        if list(self.frame.columns) != names:
            raise ValueError(f"feature columns must be exactly {names!r}")
        frame = self.frame.copy()
        if frame.index.name != "time":
            raise ValueError("feature table index must be named 'time'")
        if frame.index.has_duplicates or not frame.index.is_monotonic_increasing:
            raise ValueError("feature timestamps must be unique and chronological")
        if any(
            isinstance(timestamp, bool) or not isinstance(timestamp, int)
            for timestamp in frame.index
        ):
            raise ValueError("feature timestamps must be integer Unix timestamps")
        frame.index = pd.Index(frame.index, dtype="int64", name="time")
        for spec in self.specs:
            frame[spec.name] = frame[spec.name].astype(spec.dtype)
            if frame[spec.name].iloc[: spec.warm_up].notna().any():
                raise ValueError(f"{spec.name} warm-up values must be null")
        _validate_feature_values(self.specs, frame)
        frame.attrs["feature_specs"] = [spec.as_dict() for spec in self.specs]
        object.__setattr__(self, "frame", frame)

    @classmethod
    def from_columns(
        cls,
        specs: Sequence[FeatureSpec],
        timestamps: Sequence[int],
        columns: Mapping[str, Iterable[object]],
    ) -> FeatureTable:
        names = [spec.name for spec in specs]
        if set(columns) != set(names):
            raise ValueError(f"feature columns must be exactly {names!r}")
        normalized_columns = {name: list(columns[name]) for name in names}
        if any(len(normalized_columns[name]) != len(timestamps) for name in names):
            raise ValueError("every feature column must match the timestamp count")
        frame = pd.DataFrame(
            {
                spec.name: pd.array(
                    cast(Any, normalized_columns[spec.name]), dtype=cast(Any, spec.dtype)
                )
                for spec in specs
            },
            index=pd.Index(timestamps, name="time"),
        )
        return cls(tuple(specs), frame)

    @classmethod
    def from_parquet(cls, path: str | Path) -> FeatureTable:
        frame = pd.read_parquet(path)
        raw_specs = frame.attrs.get("feature_specs")
        if not isinstance(raw_specs, list):
            raise ValueError("Parquet file does not contain feature metadata")
        return cls(tuple(FeatureSpec(**raw_spec) for raw_spec in raw_specs), frame)

    @property
    def timestamps(self) -> tuple[int, ...]:
        return tuple(int(timestamp) for timestamp in self.frame.index)

    def to_parquet(self, path: str | Path) -> None:
        self.frame.to_parquet(path, engine="pyarrow")


@dataclass(frozen=True, slots=True)
class FeatureViewSpec:
    identifier: str
    feature_name: str
    label: str
    description: str
    renderer: Literal["line", "histogram", "marker"] | None
    pane: Literal["main", "separate"] = "main"
    show_in_crosshair: bool = True
    default_applied: bool = False
    default_visible: bool = True
    series_options: Mapping[str, object] = field(default_factory=dict)
    price_scale_options: Mapping[str, object] = field(default_factory=dict)
    pane_height: int | None = None
    scale_range: tuple[float, float] | None = None
    reference_lines: tuple[Mapping[str, object], ...] = ()
    color_feature: str | None = None
    marker_features: tuple[str, ...] = ()
    marker_offset_bars: int = 0
    color_palette: tuple[str, str] = ("#00d08499", "#ff4d6d99")
    selection_key: str | None = None
    candle_color: str | None = None

    @property
    def visualization(self) -> str:
        if self.candle_color is not None:
            return "candle color"
        if self.renderer is None:
            return "crosshair"
        if self.pane == "separate":
            return "pane"
        return self.renderer

    def __post_init__(self) -> None:
        if not isinstance(self.identifier, str) or not NAME_PATTERN.fullmatch(self.identifier):
            raise ValueError(
                "view identifier must contain lowercase letters, numbers, or underscores"
            )
        if not isinstance(self.feature_name, str) or not NAME_PATTERN.fullmatch(self.feature_name):
            raise ValueError("feature_name must contain lowercase letters, numbers, or underscores")
        if self.selection_key is not None and not NAME_PATTERN.fullmatch(self.selection_key):
            raise ValueError(
                "selection_key must contain lowercase letters, numbers, or underscores"
            )
        if self.candle_color is not None and not COLOR_PATTERN.fullmatch(self.candle_color):
            raise ValueError("candle_color must be a hex color")
        if (
            not isinstance(self.label, str)
            or not self.label.strip()
            or not isinstance(self.description, str)
            or not self.description.strip()
        ):
            raise ValueError("view label and description must not be empty")
        if self.renderer is not None and self.renderer not in RENDERERS:
            raise ValueError(f"unsupported renderer {self.renderer!r}")
        if self.pane not in ("main", "separate"):
            raise ValueError("pane must be 'main' or 'separate'")
        for name in ("show_in_crosshair", "default_applied", "default_visible"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be a boolean")
        if self.pane_height is not None and (
            not isinstance(self.pane_height, int)
            or isinstance(self.pane_height, bool)
            or self.pane_height <= 0
        ):
            raise ValueError("pane_height must be a positive integer")
        if self.scale_range is not None:
            if len(self.scale_range) != 2 or not all(
                isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)
                for value in self.scale_range
            ):
                raise ValueError("scale_range must contain two numbers")
            if self.scale_range[0] >= self.scale_range[1]:
                raise ValueError("scale_range must contain increasing values")
        if self.color_feature is not None and not NAME_PATTERN.fullmatch(self.color_feature):
            raise ValueError(
                "color_feature must contain lowercase letters, numbers, or underscores"
            )
        if any(not NAME_PATTERN.fullmatch(name) for name in self.marker_features):
            raise ValueError("marker_features must contain valid feature names")
        if len(self.marker_features) != len(set(self.marker_features)) or (
            self.feature_name in self.marker_features
        ):
            raise ValueError("marker_features must be unique and exclude feature_name")
        if self.marker_features and self.renderer != "marker":
            raise ValueError("marker_features require the marker renderer")
        if type(self.marker_offset_bars) is not int:
            raise ValueError("marker_offset_bars must be an integer")
        if len(self.color_palette) != 2 or any(
            not isinstance(color, str) or not COLOR_PATTERN.fullmatch(color)
            for color in self.color_palette
        ):
            raise ValueError("color_palette must contain two hex colors")
        validate_view_metadata(
            self.renderer,
            self.series_options,
            self.price_scale_options,
            self.reference_lines,
        )
        try:
            json.dumps(
                {
                    "series_options": dict(self.series_options),
                    "price_scale_options": dict(self.price_scale_options),
                    "reference_lines": [dict(line) for line in self.reference_lines],
                },
                allow_nan=False,
            )
        except (TypeError, ValueError) as error:
            raise ValueError(f"view metadata must be JSON-compatible: {error}") from error

    def definition(
        self, table: FeatureTable, timestamps: set[int] | None = None
    ) -> dict[str, object]:
        feature_names = (self.feature_name, *self.marker_features)
        missing = set(feature_names) - set(table.frame.columns)
        if missing:
            raise ValueError(f"feature table has no columns {sorted(missing)!r}")
        if self.color_feature is not None and self.color_feature not in table.frame:
            raise ValueError(f"feature table has no {self.color_feature!r} color column")
        values = []
        points = []
        specs = {spec.name: spec for spec in table.specs}
        row_positions = {int(timestamp): index for index, timestamp in enumerate(table.frame.index)}
        for feature_name in feature_names:
            feature_spec = specs[feature_name]
            for timestamp, value in table.frame[feature_name].items():
                if pd.isna(value):
                    continue
                original_time = int(cast(int, timestamp))
                normalized = bool(value) if feature_spec.dtype == "boolean" else float(value)
                if feature_name == self.feature_name and (
                    timestamps is None or original_time in timestamps
                ):
                    detail = {"time": original_time, "value": normalized}
                    if self.show_in_crosshair or self.candle_color is not None:
                        values.append(detail)
                if self.renderer is None:
                    continue
                point_time = original_time
                if self.renderer == "marker" and self.marker_offset_bars:
                    point_position = row_positions[original_time] + self.marker_offset_bars
                    if not 0 <= point_position < len(table.frame.index):
                        continue
                    point_time = int(table.frame.index[point_position])
                if timestamps is not None and point_time not in timestamps:
                    continue
                point: dict[str, object] = {"time": point_time, "value": normalized}
                if self.color_feature is not None:
                    color_value = table.frame.at[timestamp, self.color_feature]
                    if not pd.isna(color_value):
                        point["color"] = self.color_palette[0 if bool(color_value) else 1]
                points.append(point)
        points.sort(key=lambda point: cast(int, point["time"]))
        series_types = {"line": "LineSeries", "histogram": "HistogramSeries"}
        series_type = series_types[self.renderer] if self.renderer in series_types else None
        return {
            "id": self.identifier,
            "selection_key": self.selection_key or self.identifier,
            "candle_color": self.candle_color,
            "feature_name": self.feature_name,
            "source": specs[self.feature_name].source,
            "label": self.label,
            "description": self.description,
            "visualization": self.visualization,
            "renderer": self.renderer,
            "series_type": series_type,
            "pane": self.pane,
            "show_in_crosshair": self.show_in_crosshair,
            "default_applied": self.default_applied,
            "default_visible": self.default_visible,
            "series_options": dict(self.series_options),
            "price_scale_options": dict(self.price_scale_options),
            "pane_height": self.pane_height,
            "scale_range": self.scale_range,
            "reference_lines": [dict(line) for line in self.reference_lines],
            "points": points,
            "values": values,
        }


@dataclass(frozen=True, slots=True)
class FeatureDefinition:
    specs: tuple[FeatureSpec, ...]
    calculate: Callable[[Sequence[Bar]], FeatureTable]
    views: tuple[FeatureViewSpec, ...]
    calculation_warm_up: int = 0
    calculate_selected: Callable[[Sequence[Bar], frozenset[str]], FeatureTable] | None = None
    settings: tuple[FeatureSetting, ...] = ()
    configure: Callable[[Mapping[str, int]], FeatureDefinition] | None = None
    calculation_look_ahead: int = 0

    def __post_init__(self) -> None:
        if (
            not isinstance(self.calculation_warm_up, int)
            or isinstance(self.calculation_warm_up, bool)
            or self.calculation_warm_up < 0
        ):
            raise ValueError("calculation_warm_up must be a non-negative integer")
        if type(self.calculation_look_ahead) is not int or self.calculation_look_ahead < 0:
            raise ValueError("calculation_look_ahead must be a non-negative integer")
        if self.calculate_selected is not None and not callable(self.calculate_selected):
            raise ValueError("calculate_selected must be callable")
        if self.settings and self.configure is None:
            raise ValueError("configurable features must provide configure")


@dataclass(frozen=True, slots=True)
class FeatureSetting:
    key: str
    label: str
    default: int
    minimum: int = 1
    maximum: int = 500
    parameters: tuple[str, ...] = ("period",)

    def __post_init__(self) -> None:
        if not isinstance(self.key, str) or not NAME_PATTERN.fullmatch(self.key):
            raise ValueError("setting key must contain lowercase letters, numbers, or underscores")
        if (
            not isinstance(self.label, str)
            or not self.label.strip()
            or any(type(value) is not int for value in (self.minimum, self.default, self.maximum))
            or not 0 <= self.minimum <= self.default <= self.maximum
        ):
            raise ValueError(f"invalid setting {self.key!r} bounds or label")
        if not self.parameters or any(
            not isinstance(name, str) or not NAME_PATTERN.fullmatch(name)
            for name in self.parameters
        ):
            raise ValueError(f"invalid stored parameters for {self.key!r}")

    def as_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "label": self.label,
            "default": self.default,
            "minimum": self.minimum,
            "maximum": self.maximum,
        }


def discover() -> tuple[FeatureDefinition, ...]:
    definitions: list[FeatureDefinition] = []
    names: set[str] = set()
    view_identifiers: set[str] = set()
    for module_info in pkgutil.iter_modules(__path__, f"{__name__}."):
        if module_info.name.rsplit(".", 1)[-1] in {
            "candle_measurements",
            "comparison",
            "configuration",
            "view_validation",
        }:
            continue
        try:
            module = importlib.import_module(module_info.name)
            definition = _validate_definition(getattr(module, "feature", None))
            definition_names = {spec.name for spec in definition.specs}
            definition_views = {view.identifier for view in definition.views}
            if names & definition_names:
                raise ValueError(f"duplicate feature names {sorted(names & definition_names)!r}")
            if view_identifiers & definition_views:
                raise ValueError(
                    f"duplicate view identifiers {sorted(view_identifiers & definition_views)!r}"
                )
        except Exception as error:
            logger.warning("Skipping feature module %s: %s", module_info.name, error)
            continue
        names.update(definition_names)
        view_identifiers.update(definition_views)
        definitions.append(definition)
    return tuple(definitions)


def calculate(definition: FeatureDefinition, bars: Sequence[Bar]) -> FeatureTable | None:
    return _calculate_and_validate(definition, bars, None)


def calculate_requested(
    definition: FeatureDefinition, bars: Sequence[Bar], feature_names: frozenset[str]
) -> FeatureTable | None:
    if not feature_names:
        raise ValueError("at least one feature must be requested")
    if not feature_names <= {spec.name for spec in definition.specs}:
        raise ValueError("requested feature is not produced by this definition")
    return _calculate_and_validate(definition, bars, feature_names)


def _calculate_and_validate(
    definition: FeatureDefinition,
    bars: Sequence[Bar],
    feature_names: frozenset[str] | None,
) -> FeatureTable | None:
    try:
        all_feature_names = frozenset(spec.name for spec in definition.specs)
        if (
            feature_names is not None
            and feature_names != all_feature_names
            and definition.calculate_selected is None
        ):
            raise ValueError(
                "multi-output definitions must implement calculate_selected for partial requests"
            )
        table = (
            definition.calculate_selected(bars, feature_names)
            if feature_names is not None and definition.calculate_selected is not None
            else definition.calculate(bars)
        )
        if not isinstance(table, FeatureTable):
            raise ValueError("calculate() must return a FeatureTable")
        expected_times = tuple(bar.time for bar in bars)
        if table.timestamps != expected_times:
            raise ValueError("feature timestamps must align exactly with source bars")
        expected_specs = (
            definition.specs
            if feature_names is None or definition.calculate_selected is None
            else tuple(spec for spec in definition.specs if spec.name in feature_names)
        )
        if table.specs != expected_specs:
            raise ValueError("calculated feature specs do not match the definition")
        _validate_feature_values(table.specs, table.frame)
        return table
    except Exception as error:
        logger.warning("Skipping feature values: %s", error)
        return None


def _validate_definition(value: object) -> FeatureDefinition:
    if not isinstance(value, FeatureDefinition):
        raise ValueError("module must export a FeatureDefinition named 'feature'")
    if not value.specs:
        raise ValueError("feature definition must contain specs")
    names = {spec.name for spec in value.specs}
    if len(names) != len(value.specs):
        raise ValueError("feature definition names must be unique")
    if not callable(value.calculate):
        raise ValueError("calculate must be callable")
    if len({view.identifier for view in value.views}) != len(value.views):
        raise ValueError("feature definition view identifiers must be unique")
    for view in value.views:
        if view.feature_name not in names:
            raise ValueError(f"view {view.identifier!r} references an unknown feature")
        if not set(view.marker_features) <= names:
            raise ValueError(f"view {view.identifier!r} references an unknown marker feature")
        if view.color_feature is not None and view.color_feature not in names:
            raise ValueError(f"view {view.identifier!r} references an unknown color feature")
    return value


def _validate_feature_values(specs: Sequence[FeatureSpec], frame: pd.DataFrame) -> None:
    for spec in specs:
        if spec.dtype == "Float64" and not all(
            isfinite(float(value)) for value in frame[spec.name].dropna()
        ):
            raise ValueError(f"{spec.name} values must be finite or null")
