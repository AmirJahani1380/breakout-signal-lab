from __future__ import annotations

from dataclasses import dataclass
from math import isclose, isfinite
from numbers import Number
from typing import Any, Literal, cast

import pandas as pd
from pandas.api.types import is_bool

from app.bars import BarStore

from . import FeatureTable


@dataclass(frozen=True, slots=True)
class FeatureColumn:
    dataset_id: str
    name: str
    source: Literal["imported", "computed"]
    values: pd.Series

    def __post_init__(self) -> None:
        if not self.dataset_id or not self.name:
            raise ValueError("dataset_id and feature name must not be empty")
        if self.source not in ("imported", "computed"):
            raise ValueError("feature source must be imported or computed")
        if self.values.index.name != "time":
            raise ValueError("feature index must be named 'time'")
        if self.values.index.has_duplicates or not self.values.index.is_monotonic_increasing:
            raise ValueError("feature timestamps must be unique and chronological")
        if any(
            isinstance(value, bool) or not isinstance(value, int) for value in self.values.index
        ):
            raise ValueError("feature timestamps must be integer Unix timestamps")


@dataclass(frozen=True, slots=True)
class FeatureMismatch:
    timestamp: int
    imported_value: object
    computed_value: object


@dataclass(frozen=True, slots=True)
class FeatureComparison:
    imported_name: str
    computed_name: str
    missing_imported_timestamps: tuple[int, ...]
    missing_computed_timestamps: tuple[int, ...]
    mismatches: tuple[FeatureMismatch, ...]

    @property
    def matches(self) -> bool:
        return not (
            self.missing_imported_timestamps or self.missing_computed_timestamps or self.mismatches
        )


def imported_feature_column(store: BarStore, name: str) -> FeatureColumn:
    if name not in store.imported_features:
        raise ValueError(f"dataset {store.dataset_id!r} has no imported feature {name!r}")
    return FeatureColumn(store.dataset_id, name, "imported", store.imported_features[name].copy())


def computed_feature_column(dataset_id: str, table: FeatureTable, name: str) -> FeatureColumn:
    if name not in table.frame:
        raise ValueError(f"computed feature table has no feature {name!r}")
    spec = next(spec for spec in table.specs if spec.name == name)
    if spec.source != "computed":
        raise ValueError(f"feature {name!r} is not marked as computed")
    return FeatureColumn(dataset_id, name, "computed", table.frame[name].copy())


def compare_feature_columns(
    imported: FeatureColumn,
    computed: FeatureColumn,
    *,
    relative_tolerance: float = 1e-9,
    absolute_tolerance: float = 0.0,
) -> FeatureComparison:
    if imported.source != "imported" or computed.source != "computed":
        raise ValueError("comparison requires an imported column and a computed column")
    if imported.dataset_id != computed.dataset_id:
        raise ValueError("feature columns must belong to the same dataset")
    if not all(
        isfinite(tolerance) and tolerance >= 0
        for tolerance in (relative_tolerance, absolute_tolerance)
    ):
        raise ValueError("comparison tolerances must be finite and non-negative")

    imported_times = set(imported.values.index)
    computed_times = set(computed.values.index)
    shared_times = sorted(imported_times & computed_times)
    mismatches = tuple(
        FeatureMismatch(
            int(timestamp), imported.values.at[timestamp], computed.values.at[timestamp]
        )
        for timestamp in shared_times
        if not _values_match(
            imported.values.at[timestamp],
            computed.values.at[timestamp],
            relative_tolerance,
            absolute_tolerance,
        )
    )
    return FeatureComparison(
        imported.name,
        computed.name,
        tuple(int(value) for value in sorted(computed_times - imported_times)),
        tuple(int(value) for value in sorted(imported_times - computed_times)),
        mismatches,
    )


def _values_match(imported: object, computed: object, relative: float, absolute: float) -> bool:
    imported_missing = bool(pd.isna(cast(Any, imported)))
    computed_missing = bool(pd.isna(cast(Any, computed)))
    if imported_missing or computed_missing:
        return imported_missing and computed_missing
    if is_bool(imported) or is_bool(computed):
        return is_bool(imported) and is_bool(computed) and bool(imported) == bool(computed)
    if isinstance(imported, Number) and isinstance(computed, Number):
        return isclose(
            float(cast(Any, imported)),
            float(cast(Any, computed)),
            rel_tol=relative,
            abs_tol=absolute,
        )
    return bool(cast(Any, imported) == cast(Any, computed))
