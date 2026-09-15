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

from .view_validation import validate_view_metadata

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

    def __post_init__(self) -> None:
        if not isinstance(self.identifier, str) or not NAME_PATTERN.fullmatch(self.identifier):
            raise ValueError(
                "view identifier must contain lowercase letters, numbers, or underscores"
            )
        if not isinstance(self.feature_name, str) or not NAME_PATTERN.fullmatch(self.feature_name):
            raise ValueError("feature_name must contain lowercase letters, numbers, or underscores")
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
        if self.feature_name not in table.frame:
            raise ValueError(f"feature table has no {self.feature_name!r} column")
        if self.color_feature is not None and self.color_feature not in table.frame:
            raise ValueError(f"feature table has no {self.color_feature!r} color column")
        values = []
        points = []
        feature_spec = next(spec for spec in table.specs if spec.name == self.feature_name)
        for timestamp, value in table.frame[self.feature_name].items():
            if timestamps is not None and timestamp not in timestamps:
                continue
            if pd.isna(value):
                continue
            normalized = bool(value) if feature_spec.dtype == "boolean" else float(value)
            detail = {"time": int(cast(int, timestamp)), "value": normalized}
            if self.show_in_crosshair:
                values.append(detail)
            if self.renderer is not None:
                point: dict[str, object] = dict(detail)
                if self.color_feature is not None:
                    color_value = table.frame.at[timestamp, self.color_feature]
                    if not pd.isna(color_value):
                        point["color"] = "#00d08499" if bool(color_value) else "#ff4d6d99"
                points.append(point)
        series_types = {"line": "LineSeries", "histogram": "HistogramSeries"}
        series_type = series_types[self.renderer] if self.renderer in series_types else None
        return {
            "id": self.identifier,
            "feature_name": self.feature_name,
            "source": feature_spec.source,
            "label": self.label,
            "description": self.description,
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


def discover() -> tuple[FeatureDefinition, ...]:
    definitions: list[FeatureDefinition] = []
    names: set[str] = set()
    view_identifiers: set[str] = set()
    for module_info in pkgutil.iter_modules(__path__, f"{__name__}."):
        if module_info.name.rsplit(".", 1)[-1] in {
            "candle_measurements",
            "comparison",
            "view_validation",
        }:
            continue
        try:
            definition = _validate_definition(
                getattr(importlib.import_module(module_info.name), "feature", None)
            )
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
    try:
        table = definition.calculate(bars)
        if not isinstance(table, FeatureTable):
            raise ValueError("calculate() must return a FeatureTable")
        expected_times = tuple(bar.time for bar in bars)
        if table.timestamps != expected_times:
            raise ValueError("feature timestamps must align exactly with source bars")
        if table.specs != definition.specs:
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
    if not value.views:
        raise ValueError("feature definition must contain views")
    if len({view.identifier for view in value.views}) != len(value.views):
        raise ValueError("feature definition view identifiers must be unique")
    for view in value.views:
        if view.feature_name not in names:
            raise ValueError(f"view {view.identifier!r} references an unknown feature")
        if view.color_feature is not None and view.color_feature not in names:
            raise ValueError(f"view {view.identifier!r} references an unknown color feature")
    return value


def _validate_feature_values(specs: Sequence[FeatureSpec], frame: pd.DataFrame) -> None:
    for spec in specs:
        if spec.dtype == "Float64" and not all(
            isfinite(float(value)) for value in frame[spec.name].dropna()
        ):
            raise ValueError(f"{spec.name} values must be finite or null")
