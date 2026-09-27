"""Chronological export of canonical bars and selected feature columns."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from itertools import pairwise
from math import isfinite
from pathlib import Path
from typing import Literal

import pandas as pd
from pandas.api.extensions import ExtensionArray

from app.bars import Bar, load_bars
from app.features import FeatureDefinition, FeatureSpec, FeatureTable, discover


class FeatureExportError(ValueError):
    """A selected export cannot be completed safely."""


def _selected_warm_up(
    definitions: Sequence[FeatureDefinition], feature_names: Sequence[str]
) -> int:
    """Return source rows used only to establish selected feature state."""
    selected = frozenset(feature_names)
    return max(
        (
            max(
                definition.calculation_warm_up,
                *(spec.warm_up for spec in definition.specs if spec.name in selected),
            )
            for definition in definitions
            if selected & {spec.name for spec in definition.specs}
        ),
        default=0,
    )


def build_feature_frame(
    bars: Sequence[Bar],
    asset: str,
    timeframe: str,
    feature_names: Sequence[str],
    definitions: Sequence[FeatureDefinition] | None = None,
) -> tuple[pd.DataFrame, tuple[FeatureSpec, ...]]:
    """Calculate selected columns from each registered feature's full-history calculator."""
    registered = definitions if definitions is not None else discover()
    specs = {spec.name: spec for definition in registered for spec in definition.specs}
    if not feature_names or len(feature_names) != len(set(feature_names)):
        raise FeatureExportError("select one or more unique feature columns")
    missing = set(feature_names) - specs.keys()
    if missing:
        raise FeatureExportError(f"unknown feature columns: {sorted(missing)!r}")
    if set(feature_names) & {"asset", "timeframe", "time", "open", "high", "low", "close"}:
        raise FeatureExportError("selected features conflict with canonical columns")
    if any(later.time <= earlier.time for earlier, later in pairwise(bars)):
        raise FeatureExportError("bars must have unique, chronological timestamps")

    bundled = {spec.name: definition for definition in discover() for spec in definition.specs}
    selected = frozenset(feature_names)
    columns: dict[str, ExtensionArray] = {}
    for definition in registered:
        names = selected & {spec.name for spec in definition.specs}
        if not names:
            continue
        for name in names:
            if name in bundled and definition is not bundled[name]:
                raise FeatureExportError(
                    f"{name}: supplied definition does not match the bundled calculator"
                )
        try:
            table = (
                definition.calculate_selected(bars, names)
                if definition.calculate_selected is not None
                else definition.calculate(bars)
            )
            if not isinstance(table, FeatureTable) or table.timestamps != tuple(
                bar.time for bar in bars
            ):
                raise ValueError("calculation must return a timestamp-aligned FeatureTable")
            expected_specs = (
                tuple(spec for spec in definition.specs if spec.name in names)
                if definition.calculate_selected is not None
                else definition.specs
            )
            if table.specs != expected_specs:
                raise ValueError("calculated specs do not match the registered definition")
            columns.update({name: table.frame[name].array for name in names})
        except Exception as error:
            raise FeatureExportError(
                f"feature calculation failed for {sorted(names)!r}: {error}"
            ) from error

    frame = pd.DataFrame.from_records(
        [{"asset": asset, "timeframe": timeframe, **bar.as_dict()} for bar in bars],
        columns=["asset", "timeframe", "time", "open", "high", "low", "close", "volume"],
    )
    try:
        for name, values in columns.items():
            if name != "volume":
                frame[name] = values
        frame = frame.astype(
            {
                "asset": "string",
                "timeframe": "string",
                "time": "int64",
                **{name: specs[name].dtype for name in feature_names if name != "volume"},
            }
        )
        for name in ("open", "high", "low", "close", "volume"):
            frame[name] = frame[name].astype("float64")
    except (TypeError, ValueError, OverflowError) as error:
        raise FeatureExportError(
            f"cannot convert selected feature values {list(feature_names)!r}: {error}"
        ) from error
    for spec in (specs[name] for name in feature_names):
        if spec.dtype == "Float64" and not all(
            isfinite(float(value)) for value in frame[spec.name].dropna()
        ):
            raise FeatureExportError(f"{spec.name} calculation produced non-finite values")
    return frame[
        [
            "asset",
            "timeframe",
            "time",
            "open",
            "high",
            "low",
            "close",
            "volume",
            *(name for name in feature_names if name != "volume"),
        ]
    ], tuple(specs[name] for name in feature_names)


def export_bar_features(
    source_path: Path,
    output_directory: Path,
    asset: str,
    timeframe: str,
    feature_names: Sequence[str],
    format: Literal["csv", "parquet"],  # pylint: disable=redefined-builtin
    source_timezone: str = "UTC",
    definitions: Sequence[FeatureDefinition] | None = None,
) -> tuple[Path, Path]:
    """Write a table and JSON contract; existing output paths are never replaced."""
    if format not in ("csv", "parquet"):
        raise FeatureExportError("format must be csv or parquet")
    if not asset or not timeframe:
        raise FeatureExportError("asset and timeframe are required")
    suffix = "csv" if format == "csv" else "parquet"
    table_path = output_directory / f"bar_features.{suffix}"
    metadata_path = output_directory / f"bar_features.{suffix}.json"
    if table_path.exists() or metadata_path.exists():
        raise FileExistsError(f"export already exists in {output_directory}")
    fingerprint = hashlib.sha256()
    with source_path.open("rb") as source_stream:
        for chunk in iter(lambda: source_stream.read(1024 * 1024), b""):
            fingerprint.update(chunk)
    store = load_bars(source_path, source_timezone, f"{asset}/{timeframe}")
    registered = definitions if definitions is not None else discover()
    frame, selected_specs = build_feature_frame(
        store.bars, asset, timeframe, feature_names, registered
    )
    warm_up = _selected_warm_up(registered, feature_names)
    frame = frame.iloc[warm_up:].reset_index(drop=True)
    metadata = {
        "dataset_id": store.dataset_id,
        "asset": asset,
        "timeframe": timeframe,
        "source": source_path.name,
        "source_sha256": fingerprint.hexdigest(),
        "timestamp": "time: UTC Unix seconds, int64",
        "row_key": ["dataset_id", "time"],
        "seed_policy": (
            "Recursive indicators are seeded from the dataset start. "
            f"The first {warm_up} source rows establish feature state and are not exported."
        ),
        "export_warm_up_rows": warm_up,
        "features": [spec.as_dict() for spec in selected_specs],
        "dtypes": {column: str(dtype) for column, dtype in frame.dtypes.items()},
        "null": "CSV empty field; Parquet null; zero denominator is null",
        "float_tolerance": {"relative": 1e-12, "absolute": 1e-12},
    }
    output_directory.mkdir(parents=True, exist_ok=True)
    # Exclusive creation also protects against a second export racing this one.
    with table_path.open("xb") as stream:
        try:
            if format == "csv":
                frame.to_csv(stream, index=False, float_format="%.17g", na_rep="")
            else:
                frame.to_parquet(stream, index=False, engine="pyarrow")
            with metadata_path.open("x", encoding="utf-8") as sidecar:
                json.dump(metadata, sidecar, indent=2, allow_nan=False)
        except Exception:
            table_path.unlink(missing_ok=True)
            raise
    return table_path, metadata_path
