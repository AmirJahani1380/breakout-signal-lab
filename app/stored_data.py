"""Read an exported feature table as immutable chart input."""

from __future__ import annotations

import json
from bisect import bisect_left
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from .bars import Bar, BarStore, SourceValidationError, load_bars
from .features import FeatureDefinition, FeatureSpec, FeatureTable


class StoredDataError(ValueError):
    """An export and its sidecar do not form a usable dataset."""


def _stored_value(value: Any) -> object:
    if pd.isna(value):
        return None
    native = value.item() if hasattr(value, "item") else value
    # Browser numbers cannot represent integers outside this range exactly.
    if isinstance(native, int) and not isinstance(native, bool) and abs(native) > 2**53 - 1:
        return str(native)
    return native


def _stored_integer(value: Any, name: str) -> object:
    if pd.isna(value):
        return pd.NA
    if isinstance(value, bool) or (isinstance(value, float) and not value.is_integer()):
        raise ValueError(f"{name}: expected integer or null")
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"{name}: expected integer or null") from error


@dataclass(frozen=True)
class StoredDataset:
    bars: BarStore
    table: FeatureTable
    metadata: dict[str, Any]

    def page(
        self, before: int | None, limit: int, definitions: tuple[FeatureDefinition, ...]
    ) -> dict[str, object]:
        end = len(self.bars.bars) if before is None else bisect_left(self.bars.times, before)
        start = max(0, end - limit)
        shown = self.bars.bars[start:end]
        times = {bar.time for bar in shown}
        available = {spec.name for spec in self.table.specs}
        views = [view for definition in definitions for view in definition.views]
        indicators = [
            view.definition(self.table, times)
            for view in views
            if view.feature_name in available
            and (view.color_feature is None or view.color_feature in available)
        ]
        values = {
            spec.name: [
                {
                    "time": bar.time,
                    "value": _stored_value(value),
                }
                for bar, value in zip(
                    shown, self.table.frame.iloc[start:end][spec.name], strict=True
                )
            ]
            for spec in self.table.specs
        }
        return {
            "bars": [bar.as_dict() for bar in shown],
            "indicators": indicators,
            "stored_values": values,
            "stored_metadata": {
                "dataset_id": self.metadata["dataset_id"],
                "dataset_version": self.metadata.get(
                    "dataset_version", self.metadata["source_sha256"]
                ),
                "features": self.metadata["features"],
            },
            "next_before": shown[0].time if start and shown else None,
            "has_more": start > 0,
        }


def load_stored_dataset(path: Path) -> StoredDataset:
    sidecar = path.with_name(path.name + ".json")
    if path.suffix.lower() not in {".csv", ".parquet"}:
        raise StoredDataError(f"{path.name}: expected a CSV or Parquet export")
    if not sidecar.is_file():
        raise StoredDataError(f"{path.name}: missing metadata sidecar {sidecar.name}")
    try:
        metadata = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise StoredDataError(f"{sidecar.name}: cannot read metadata: {error}") from error
    if not isinstance(metadata, dict):
        raise StoredDataError(f"{sidecar.name}: metadata must be an object")
    required = (
        "dataset_id",
        "asset",
        "timeframe",
        "source_sha256",
        "timestamp",
        "row_key",
        "features",
        "dtypes",
    )
    missing = [name for name in required if name not in metadata]
    if missing:
        raise StoredDataError(
            f"{sidecar.name}: missing metadata fields {missing}; re-export with the JSON sidecar"
        )
    if metadata["dataset_id"] != f"{metadata['asset']}/{metadata['timeframe']}":
        raise StoredDataError(f"{sidecar.name}: dataset_id must match asset/timeframe")
    if metadata["timestamp"] != "time: UTC Unix seconds, int64" or metadata["row_key"] != [
        "dataset_id",
        "time",
    ]:
        raise StoredDataError(f"{sidecar.name}: incompatible timestamp or row_key contract")
    if not isinstance(metadata["features"], list) or not metadata["features"]:
        raise StoredDataError(f"{sidecar.name}: features must list at least one column")
    try:
        specs = tuple(FeatureSpec(**raw) for raw in metadata["features"])
    except (TypeError, ValueError) as error:
        raise StoredDataError(f"{sidecar.name}: invalid feature metadata: {error}") from error
    names = [spec.name for spec in specs]
    if len(names) != len(set(names)):
        raise StoredDataError(f"{sidecar.name}: duplicate feature names")
    if not isinstance(metadata["dtypes"], dict):
        raise StoredDataError(f"{sidecar.name}: dtypes must be an object")
    required_columns = [
        "asset",
        "timeframe",
        "time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        *(name for name in names if name != "volume"),
    ]
    expected_types = {
        "asset": "string",
        "timeframe": "string",
        "time": "int64",
        **{name: "float64" for name in ("open", "high", "low", "close", "volume")},
        **{spec.name: spec.dtype for spec in specs if spec.name != "volume"},
    }
    if any(metadata["dtypes"].get(name) != dtype for name, dtype in expected_types.items()):
        raise StoredDataError(
            f"{sidecar.name}: dtypes do not match required canonical columns or feature types"
        )
    try:
        frame = (
            pd.read_csv(path, dtype="string", keep_default_na=False)
            if path.suffix.lower() == ".csv"
            else pd.read_parquet(path)
        )
    except Exception as error:
        raise StoredDataError(f"{path.name}: cannot read export: {error}") from error
    if list(frame.columns) != required_columns:
        raise StoredDataError(f"{path.name}: columns must match sidecar order: {required_columns}")
    if path.suffix.lower() == ".parquet":
        for spec in specs:
            column_dtype = frame[spec.name].dtype
            expected_parquet_types = {
                "Float64": {"float64", "Float64"},
                "Int64": {"int64", "Int64"},
                "boolean": {"bool", "boolean"},
            }
            if str(column_dtype) not in expected_parquet_types[spec.dtype]:
                raise StoredDataError(
                    f"{path.name}: {spec.name} Parquet type {column_dtype} "
                    f"does not match metadata {spec.dtype}"
                )
    if frame.empty:
        raise StoredDataError(f"{path.name}: export has no rows")
    if (
        not frame["asset"].eq(metadata["asset"]).all()
        or not frame["timeframe"].eq(metadata["timeframe"]).all()
    ):
        raise StoredDataError(
            f"{path.name}: row asset/timeframe does not match metadata dataset_id"
        )
    try:
        timestamps = pd.to_numeric(frame["time"], errors="raise")
        if timestamps.isna().any() or any(str(value) != str(int(value)) for value in timestamps):
            raise ValueError("timestamps must be whole UTC Unix seconds")
        times = [int(value) for value in timestamps]
        if any(later <= earlier for earlier, later in zip(times, times[1:])):
            raise ValueError("timestamps must be strictly increasing and unique")
        columns: dict[str, list[object]] = {}
        for spec in specs:
            values = frame[spec.name].replace("", pd.NA)
            if spec.dtype == "boolean":
                if not values.dropna().isin([True, False, "True", "False"]).all():
                    raise ValueError(f"{spec.name}: expected boolean True/False or null")
                values = values.map({True: True, False: False, "True": True, "False": False})
            elif spec.dtype == "Int64":
                values = values.map(lambda value: _stored_integer(value, spec.name))
            else:
                values = values.map(lambda value: pd.NA if pd.isna(value) else float(value))
            columns[spec.name] = values.tolist()
        # Export warm-up rows have already been withheld; do not apply source-start checks again.
        view_specs = tuple(
            FeatureSpec(
                spec.name, spec.dtype, spec.parameters, 0, spec.version, spec.causality, spec.source
            )
            for spec in specs
        )
        table = FeatureTable.from_columns(view_specs, times, columns)
        validated_bars = load_bars(path, "UTC", metadata["dataset_id"])
        if validated_bars.times != table.timestamps:
            raise ValueError("feature timestamps do not align exactly with OHLCV bars")
        bars = BarStore(
            (
                Bar(
                    time,
                    *(
                        float(frame.iloc[index][name])
                        for name in ("open", "high", "low", "close", "volume")
                    ),
                )
                for index, time in enumerate(times)
            ),
            dataset_id=metadata["dataset_id"],
        )
    except (ValueError, TypeError, SourceValidationError) as error:
        raise StoredDataError(f"{path.name}: {error}") from error
    return StoredDataset(bars, table, metadata)
