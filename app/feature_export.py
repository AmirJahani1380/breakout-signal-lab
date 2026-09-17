"""Chronological, stateful export of canonical bars and selected feature columns."""

from __future__ import annotations

import hashlib
import json
from collections import deque
from collections.abc import Sequence
from math import isfinite
from pathlib import Path
from typing import Literal

import pandas as pd

from app.bars import Bar, load_bars
from app.features import FeatureDefinition, FeatureSpec, FeatureTable, discover
from app.features.atr_20 import Atr20State
from app.features.candle_measurements import measure_candles
from app.features.ema_20 import ema_step as _ema
from app.features.is_engulfing import _engulfs
from app.features.macd import MacdState
from app.features.rolling_overlap_20 import overlap_score
from app.features.rsi_14 import Rsi14State


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


class _BarFeatureState:
    def __init__(self) -> None:
        self.previous: Bar | None = None
        self.previous_bars: deque[Bar] = deque(maxlen=20)
        self.ema_20: float | None = None
        self.macd = MacdState()
        self.atr = Atr20State()
        self.rsi = Rsi14State()

    def add(self, bar: Bar, selected: frozenset[str]) -> dict[str, float | int | bool | None]:
        # Dataset-start seed: first close for EMAs, first high-low for ATR, and
        # the first 14 close changes for RSI. State advances exactly once per bar.
        measured = measure_candles((bar,))
        candle_range = measured.candle_range[0]
        body_size = measured.body_size[0]
        upper_wick = measured.upper_wick[0]
        lower_wick = measured.lower_wick[0]
        if "ema_20" in selected:
            self.ema_20 = _ema(self.ema_20, bar.close, 20)
        macd: float | None = None
        macd_signal: float | None = None
        if selected & {"macd", "macd_signal", "macd_histogram"}:
            macd, macd_signal = self.macd.add(
                bar.close, bool(selected & {"macd_signal", "macd_histogram"})
            )
        atr: float | None = None
        if (
            "atr_20" in selected
            or "atr_20_to_close" in selected
            or any(name.endswith("_to_atr_20") for name in selected)
        ):
            atr = self.atr.add(bar)
        rsi = self.rsi.add(bar.close) if "rsi_14" in selected else None
        overlap = (
            overlap_score(tuple(self.previous_bars))
            if "rolling_overlap_20" in selected or "rolling_overlap_20_above_half" in selected
            else None
        )
        values: dict[str, float | int | bool | None] = {
            "candle_range": candle_range,
            "body_size": body_size,
            "upper_wick_size": upper_wick,
            "lower_wick_size": lower_wick,
            "body_to_range_ratio": body_size / candle_range if candle_range else 0.0,
            "candle_direction": (bar.close > bar.open) - (bar.close < bar.open),
            "is_engulfing": _engulfs(self.previous, bar) if self.previous else False,
            "volume": bar.volume,
            "volume_up": bar.close >= bar.open,
            "ema_20": self.ema_20,
            "rsi_14": rsi,
            "atr_20": atr,
            "macd": macd,
            "macd_signal": macd_signal,
            "macd_histogram": macd - macd_signal
            if macd is not None and macd_signal is not None
            else None,
            "rolling_overlap_20": overlap,
            "rolling_overlap_20_above_half": None
            if overlap is None or overlap == 0.5
            else overlap > 0.5,
        }
        numerators = {
            "candle_range": candle_range,
            "body_size": body_size,
            "upper_wick": upper_wick,
            "lower_wick": lower_wick,
            "atr_20": atr,
        }
        for name in selected:
            if "_to_" in name and name not in values:
                numerator_name, denominator_name = name.rsplit("_to_", 1)
                numerator = numerators[numerator_name]
                denominator = atr if denominator_name == "atr_20" else bar.close
                values[name] = (
                    numerator / denominator
                    if numerator is not None and denominator is not None and denominator != 0
                    else None
                )
        self.previous_bars.append(bar)
        self.previous = bar
        return {name: values[name] for name in selected}


def build_feature_frame(
    bars: Sequence[Bar],
    asset: str,
    timeframe: str,
    feature_names: Sequence[str],
    definitions: Sequence[FeatureDefinition] | None = None,
) -> tuple[pd.DataFrame, tuple[FeatureSpec, ...]]:
    """Calculate each selected column in one chronological pass over the bars."""
    bundled = discover()
    registered = definitions if definitions is not None else bundled
    specs = {spec.name: spec for definition in registered for spec in definition.specs}
    if not feature_names or len(feature_names) != len(set(feature_names)):
        raise FeatureExportError("select one or more unique feature columns")
    missing = set(feature_names) - specs.keys()
    if missing:
        raise FeatureExportError(f"unknown feature columns: {sorted(missing)!r}")
    if set(feature_names) & {"asset", "timeframe", "time", "open", "high", "low", "close"}:
        raise FeatureExportError("selected features conflict with canonical columns")
    selected = frozenset(feature_names)
    built_in = {
        "candle_range",
        "body_size",
        "upper_wick_size",
        "lower_wick_size",
        "body_to_range_ratio",
        "candle_direction",
        "is_engulfing",
        "volume",
        "volume_up",
        "ema_20",
        "rsi_14",
        "atr_20",
        "macd",
        "macd_signal",
        "macd_histogram",
        "rolling_overlap_20",
        "rolling_overlap_20_above_half",
    }
    built_in.update(
        name
        for name in selected
        if name in specs
        and name.rsplit("_to_", 1)[0]
        in {"candle_range", "body_size", "upper_wick", "lower_wick", "atr_20"}
        and name.rsplit("_to_", 1)[-1] in {"atr_20", "close"}
    )
    bundled_by_name = {spec.name: definition for definition in bundled for spec in definition.specs}
    for definition in registered:
        for spec in definition.specs:
            if spec.name in selected & built_in and definition is not bundled_by_name.get(
                spec.name
            ):
                raise FeatureExportError(
                    f"{spec.name}: supplied definition does not match "
                    "the bundled incremental calculator"
                )
    custom_definitions: list[tuple[FeatureDefinition, frozenset[str]]] = []
    for definition in registered:
        custom_names = (selected - built_in) & {spec.name for spec in definition.specs}
        if custom_names:
            if definition.calculation_warm_up or any(
                spec.warm_up for spec in definition.specs if spec.name in custom_names
            ):
                raise FeatureExportError(
                    f"no incremental calculator for feature columns: {sorted(custom_names)!r}"
                )
            custom_definitions.append((definition, frozenset(custom_names)))
    state = _BarFeatureState()
    rows: list[dict[str, object]] = []
    prior_time: int | None = None
    for bar in bars:
        if prior_time is not None and bar.time <= prior_time:
            raise FeatureExportError("bars must have unique, chronological timestamps")
        try:
            values = state.add(bar, selected & built_in)
            for definition, names in custom_definitions:
                table = (
                    definition.calculate_selected((bar,), names)
                    if definition.calculate_selected is not None
                    else definition.calculate((bar,))
                )
                if not isinstance(table, FeatureTable) or table.timestamps != (bar.time,):
                    raise ValueError("calculation must return one timestamp-aligned FeatureTable")
                expected_specs = (
                    tuple(spec for spec in definition.specs if spec.name in names)
                    if definition.calculate_selected is not None
                    else definition.specs
                )
                if table.specs != expected_specs:
                    raise ValueError(
                        f"calculated specs for {sorted(names)!r} "
                        "do not match the registered definition"
                    )
                if not names <= set(table.frame.columns):
                    raise ValueError(
                        f"calculation omitted {sorted(names - set(table.frame.columns))!r}"
                    )
                values.update({name: table.frame.iloc[0][name] for name in names})
        except Exception as error:
            raise FeatureExportError(
                f"feature calculation failed for {list(feature_names)!r} at {bar.time}: {error}"
            ) from error
        rows.append(
            {"asset": asset, "timeframe": timeframe, "time": bar.time, **bar.as_dict(), **values}
        )
        prior_time = bar.time
    columns = [
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
    try:
        frame = pd.DataFrame.from_records(rows, columns=columns)
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
    return frame, tuple(specs[name] for name in feature_names)


def export_bar_features(
    source_path: Path,
    output_directory: Path,
    asset: str,
    timeframe: str,
    feature_names: Sequence[str],
    format: Literal["csv", "parquet"],
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
            "dataset start; EMA first close, ATR first 20 true ranges, "
            "RSI first 14 close changes; rolling overlap previous 20 bars. "
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
