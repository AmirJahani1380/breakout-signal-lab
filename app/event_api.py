"""HTTP endpoints for detected events and event exports."""

from __future__ import annotations

import json
import tempfile
import zipfile
from io import BytesIO
from math import isfinite
from pathlib import Path
from typing import Literal

import pandas as pd
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import Response

from .bars import SourceCatalog, SourceValidationError, load_bars
from .event_detectors import EventConfig, detect_events
from .feature_export import FeatureExportError, build_feature_frame
from .features.configuration import configure_features
from .labels import LabelConfig, label_events

EVENT_COLUMNS = (
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
    "label_config",
    "label_status",
    "label_reason",
    "entry_time",
    "entry_open",
    "entry_fill",
    "signal_atr",
    "stop_price",
    "target_price",
    "risk_price",
    "planned_end_time",
    "observation_end_time",
    "exit_time",
    "exit_fill",
    "gross_r",
    "net_r",
    "horizon_complete",
    "mae_price",
    "mfe_price",
    "mae_r",
    "mfe_r",
)


def register_event_routes(app: FastAPI, source_timezone: str, page_size: int) -> None:
    def label_configuration(request: Request) -> LabelConfig:
        def integer(name: str, default: int) -> int:
            raw = request.query_params.get(name, str(default))
            if not raw.isdecimal() or str(int(raw)) != raw:
                raise ValueError(f"{name} must be a positive integer")
            return int(raw)

        def number(name: str, default: float) -> float:
            try:
                return float(request.query_params.get(name, str(default)))
            except ValueError as error:
                raise ValueError(f"{name} must be a finite non-negative number") from error

        return LabelConfig(
            horizon=integer("label_horizon", 20),
            atr_period=integer("label_atr_period", 20),
            atr_buffer=number("label_atr_buffer", 0.05),
            slippage=number("label_slippage", 0),
            commission=number("label_commission", 0),
        )

    def event_configuration(request: Request, detector_names: list[str]) -> EventConfig:
        def integer(name: str, default: int) -> int:
            raw = request.query_params.get(name, str(default))
            if not raw.isdecimal() or str(int(raw)) != raw:
                raise ValueError(f"{name} must be an integer")
            return int(raw)

        raw_buffer = request.query_params.get("buffer", "0")
        try:
            detector_settings: dict[str, float] = {}
            for identifier in detector_names:
                for setting in app.state.event_detectors[identifier].settings:
                    key = f"{identifier}.{setting.name}"
                    try:
                        value = float(request.query_params.get(key, setting.default))
                    except ValueError as error:
                        raise ValueError(f"{key} must be a finite number") from error
                    if not isfinite(value):
                        raise ValueError(f"{key} must be finite")
                    if setting.minimum is not None and value < setting.minimum:
                        raise ValueError(f"{key} must be at least {setting.minimum}")
                    if setting.maximum is not None and value > setting.maximum:
                        raise ValueError(f"{key} must be at most {setting.maximum}")
                    detector_settings[key] = value
            return EventConfig(
                ema_period=integer("ema_period", 20),
                swing_lookback=integer("swing_lookback", 20),
                swing_left=integer("swing_left", 3),
                swing_right=integer("swing_right", 3),
                buffer=float(raw_buffer),
                detector_settings=detector_settings,
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    def selected_detectors(request: Request) -> list[str]:
        raw = request.query_params.get("detectors")
        registry = app.state.event_detectors
        names = list(registry) if raw is None else [name for name in raw.split(",") if name]
        if len(names) != len(set(names)) or set(names) - registry.keys():
            raise HTTPException(
                status_code=422, detail="detectors must contain unique known identifiers"
            )
        return names

    @app.get("/api/v1/events")
    def events(
        request: Request,
        symbol: str = Query(min_length=1, max_length=100),
        timeframe: str = Query(min_length=1, max_length=100),
        before: int | None = None,
        limit: int = Query(default=page_size, ge=1, le=page_size),
    ) -> dict[str, object]:
        source_catalog: SourceCatalog | None = app.state.catalog
        if source_catalog is None:
            raise HTTPException(status_code=503, detail=app.state.source_error)
        try:
            selection = source_catalog.selection(symbol, timeframe)
            store = load_bars(selection.source_path, source_timezone)
            detector_names = selected_detectors(request)
            config = event_configuration(request, detector_names)
            detected, swings = detect_events(
                store.bars,
                config,
                detector_names,
                f"{symbol}/{timeframe}",
                app.state.event_detectors,
            )
            page = store.page(before, limit)
            times = {bar.time for bar in page.display_bars}
            labeled = label_events(store.bars, detected, label_configuration(request))
            return {
                "events": [event for event in labeled if event["signal_time"] in times],
                "swings": [
                    {
                        "direction": swing.direction,
                        "pivot_time": swing.pivot_time,
                        "availability_time": swing.availability_time,
                        "level": swing.level,
                    }
                    for swing in swings
                    if swing.pivot_time in times
                ],
                "next_before": page.display_bars[0].time
                if page.has_more and page.display_bars
                else None,
                "has_more": page.has_more,
            }
        except (OSError, SourceValidationError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.get("/api/v1/events/export")
    def export_events(
        request: Request,
        symbol: str = Query(min_length=1, max_length=100),
        timeframe: str = Query(min_length=1, max_length=100),
        features: str = "",
        output_format: Literal["csv", "parquet"] = Query(default="csv", alias="format"),
    ) -> Response:
        source_catalog: SourceCatalog | None = app.state.catalog
        if source_catalog is None:
            raise HTTPException(status_code=503, detail=app.state.source_error)
        try:
            selection = source_catalog.selection(symbol, timeframe)
            store = load_bars(selection.source_path, source_timezone)
            definitions = configure_features(app.state.features, request.query_params)
            names = [name for name in features.split(",") if name]
            frame, specs = build_feature_frame(
                store.bars, symbol, timeframe, names or ["volume"], definitions
            )
            detector_names = selected_detectors(request)
            detected, _ = detect_events(
                store.bars,
                event_configuration(request, detector_names),
                detector_names,
                f"{symbol}/{timeframe}",
                app.state.event_detectors,
            )
            event_frame = pd.DataFrame.from_records(
                label_events(store.bars, detected, label_configuration(request))
            )
            if event_frame.empty:
                event_frame = pd.DataFrame(columns=EVENT_COLUMNS)
            event_frame["configuration"] = event_frame["configuration"].map(
                lambda value: (
                    json.dumps(value, sort_keys=True) if isinstance(value, dict) else value
                )
            )
            event_frame["label_config"] = event_frame["label_config"].map(
                lambda value: (
                    json.dumps(value, sort_keys=True) if isinstance(value, dict) else value
                )
            )
            event_frame = event_frame.merge(
                frame.drop(columns=["asset", "timeframe"]),
                left_on="signal_time",
                right_on="time",
                how="left",
                validate="many_to_one",
            )
            event_frame.insert(0, "asset", symbol)
            event_frame.insert(1, "timeframe", timeframe)
            with tempfile.TemporaryDirectory() as directory:
                table = Path(directory) / f"encountered_events.{output_format}"
                sidecar = Path(directory) / f"encountered_events.{output_format}.json"
                if output_format == "csv":
                    event_frame.to_csv(table, index=False, float_format="%.17g")
                else:
                    event_frame.to_parquet(table, index=False)
                sidecar.write_text(
                    json.dumps(
                        {
                            "schema": "encountered_events.v2",
                            "asset": symbol,
                            "timeframe": timeframe,
                            "detectors": detector_names,
                            "features": [spec.as_dict() for spec in specs if spec.name in names],
                            "event_columns": EVENT_COLUMNS,
                            "row_key": ["id"],
                        },
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                archive_bytes = BytesIO()
                with zipfile.ZipFile(archive_bytes, "w", zipfile.ZIP_DEFLATED) as archive:
                    archive.write(table, table.name)
                    archive.write(sidecar, sidecar.name)
                return Response(
                    archive_bytes.getvalue(),
                    media_type="application/zip",
                    headers={
                        "Content-Disposition": (
                            f'attachment; filename="encountered_events_{output_format}.zip"'
                        )
                    },
                )
        except (OSError, SourceValidationError, FeatureExportError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
