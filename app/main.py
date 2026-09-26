from __future__ import annotations

import base64
import binascii
import json
import os
import tempfile
import zipfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Literal, cast
from uuid import UUID

import pandas as pd
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .bars import (
    SourceCatalog,
    SourceValidationError,
    discover_source_catalog,
    load_bars,
)
from .event_detectors import DETECTORS, EventConfig, detect_events
from .feature_export import FeatureExportError, build_feature_frame, export_bar_features
from .features import (
    FeatureDefinition,
    FeatureTable,
    FeatureViewSpec,
    calculate_requested,
    discover,
)
from .features.configuration import configure_features, configure_stored_features, settings_catalog
from .stored_data import StoredDataError, StoredDataset, load_stored_dataset

DEFAULT_DATA_ROOT = Path(
    r"C:\Users\amirj\OneDrive\Desktop\programming\Trade\data analysis\mt5_data"
)
STATIC_DIRECTORY = Path(__file__).parent.parent / "static"
PAGE_SIZE = 1000


class StoredImport(BaseModel):
    session_id: UUID
    filename: str
    content_base64: str
    metadata_json: str


@dataclass(frozen=True, slots=True)
class Settings:
    data_root: Path
    source_timezone: str = "UTC"
    stored_data_root: Path | None = None

    @classmethod
    def from_environment(cls) -> Settings:
        return cls(
            Path(os.getenv("BARS_DATA_ROOT", str(DEFAULT_DATA_ROOT))),
            os.getenv("SOURCE_TIMEZONE", "UTC"),
            Path(os.environ["STORED_DATA_ROOT"]) if os.getenv("STORED_DATA_ROOT") else None,
        )


def create_app(settings: Settings | None = None) -> FastAPI:
    configured_settings = settings or Settings.from_environment()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.features = discover()
        app.state.imported_datasets = {}
        try:
            app.state.catalog = discover_source_catalog(configured_settings.data_root)
            app.state.source_error = None
        except SourceValidationError as error:
            app.state.catalog = None
            app.state.source_error = str(error)
        yield

    app = FastAPI(title="Breakout Research Chart Viewer", lifespan=lifespan)

    @app.get("/api/v1/catalog")
    def catalog(
        mode: Literal["source", "stored"] = "source",
        import_session: UUID | None = None,
    ) -> dict[str, object]:
        if mode == "stored":
            symbols = (
                _stored_catalog(configured_settings.stored_data_root)
                if configured_settings.stored_data_root
                else []
            )
            imported = app.state.imported_datasets.get(import_session, {})
            grouped: dict[str, set[str]] = {
                cast(str, entry["symbol"]): set(cast(list[str], entry["timeframes"]))
                for entry in symbols
            }
            for asset, timeframe in imported:
                grouped.setdefault(asset, set()).add(timeframe)
            return {
                "symbols": [
                    {"symbol": asset, "timeframes": sorted(times)}
                    for asset, times in sorted(grouped.items())
                ]
            }
        source_catalog: SourceCatalog | None = app.state.catalog
        if source_catalog is None:
            raise HTTPException(
                status_code=503, detail=app.state.source_error or "data root unavailable"
            )
        return {"symbols": source_catalog.symbols()}

    @app.get("/api/v1/features")
    def features(request: Request) -> dict[str, object]:
        try:
            definitions = configure_features(app.state.features, request.query_params)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return {
            "settings": [setting.as_dict() for setting in settings_catalog(app.state.features)],
            "indicators": _empty_feature_payload(definitions),
            "export_features": [
                spec.as_dict() for definition in definitions for spec in definition.specs
            ],
            "event_detectors": [
                {"id": identifier, "label": label} for identifier, label in DETECTORS.items()
            ],
        }

    def event_configuration(request: Request) -> EventConfig:
        def integer(name: str, default: int) -> int:
            raw = request.query_params.get(name, str(default))
            if not raw.isdecimal() or str(int(raw)) != raw:
                raise ValueError(f"{name} must be an integer")
            return int(raw)

        raw_buffer = request.query_params.get("buffer", "0")
        try:
            return EventConfig(
                ema_period=integer("ema_period", 20),
                swing_left=integer("swing_left", 3),
                swing_right=integer("swing_right", 3),
                buffer=float(raw_buffer),
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    def selected_detectors(request: Request) -> list[str]:
        raw = request.query_params.get("detectors")
        names = list(DETECTORS) if raw is None else [name for name in raw.split(",") if name]
        if len(names) != len(set(names)) or set(names) - DETECTORS.keys():
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
        limit: int = Query(default=PAGE_SIZE, ge=1, le=PAGE_SIZE),
    ) -> dict[str, object]:
        source_catalog: SourceCatalog | None = app.state.catalog
        if source_catalog is None:
            raise HTTPException(status_code=503, detail=app.state.source_error)
        try:
            selection = source_catalog.selection(symbol, timeframe)
            store = load_bars(selection.source_path, configured_settings.source_timezone)
            config = event_configuration(request)
            detected, swings = detect_events(
                store.bars, config, selected_detectors(request), f"{symbol}/{timeframe}"
            )
            page = store.page(before, limit)
            times = {bar.time for bar in page.display_bars}
            return {
                "events": [event for event in detected if event["signal_time"] in times],
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
        format: Literal["csv", "parquet"] = "csv",
    ) -> Response:
        source_catalog: SourceCatalog | None = app.state.catalog
        if source_catalog is None:
            raise HTTPException(status_code=503, detail=app.state.source_error)
        try:
            selection = source_catalog.selection(symbol, timeframe)
            store = load_bars(selection.source_path, configured_settings.source_timezone)
            definitions = configure_features(app.state.features, request.query_params)
            names = [name for name in features.split(",") if name]
            if names:
                frame, specs = build_feature_frame(
                    store.bars, symbol, timeframe, names, definitions
                )
            else:
                frame, specs = build_feature_frame(
                    store.bars, symbol, timeframe, ["volume"], definitions
                )
            detected, _ = detect_events(
                store.bars,
                event_configuration(request),
                selected_detectors(request),
                f"{symbol}/{timeframe}",
            )
            event_frame = pd.DataFrame.from_records(detected)
            if event_frame.empty:
                event_frame = pd.DataFrame(
                    columns=[
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
                    ]
                )
            event_frame["configuration"] = event_frame["configuration"].map(
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
                table = Path(directory) / f"encountered_events.{format}"
                sidecar = Path(directory) / f"encountered_events.{format}.json"
                if format == "csv":
                    event_frame.to_csv(table, index=False, float_format="%.17g")
                else:
                    event_frame.to_parquet(table, index=False)
                sidecar.write_text(
                    json.dumps(
                        {
                            "schema": "encountered_events.v1",
                            "asset": symbol,
                            "timeframe": timeframe,
                            "detectors": selected_detectors(request),
                            "features": [spec.as_dict() for spec in specs if spec.name in names],
                            "event_columns": [
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
                            ],
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
                            f'attachment; filename="encountered_events_{format}.zip"'
                        )
                    },
                )
        except (OSError, SourceValidationError, FeatureExportError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.post("/api/v1/stored/import")
    def import_stored(payload: StoredImport) -> dict[str, object]:
        if (
            Path(payload.filename).name != payload.filename
            or payload.filename.lower().endswith((".csv", ".parquet")) is False
        ):
            raise HTTPException(
                status_code=422,
                detail="Choose a CSV or Parquet export filename without directories",
            )
        try:
            raw = base64.b64decode(payload.content_base64, validate=True)
            if not raw:
                raise StoredDataError(f"{payload.filename}: export is empty")
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / payload.filename
                path.write_bytes(raw)
                path.with_name(path.name + ".json").write_text(
                    payload.metadata_json, encoding="utf-8"
                )
                dataset = load_stored_dataset(path)
        except (OSError, binascii.Error, StoredDataError) as error:
            raise HTTPException(status_code=422, detail=f"{payload.filename}: {error}") from error
        imported: dict[tuple[str, str], StoredDataset] = app.state.imported_datasets.setdefault(
            payload.session_id, {}
        )
        identity = (dataset.metadata["asset"], dataset.metadata["timeframe"])
        imported[identity] = dataset
        return {"dataset_id": dataset.metadata["dataset_id"], "rows": len(dataset.bars.bars)}

    @app.get("/api/v1/export")
    def export(
        request: Request,
        symbol: str = Query(min_length=1, max_length=100),
        timeframe: str = Query(min_length=1, max_length=100),
        features: str = Query(min_length=1),
        format: str = Query(pattern="^(csv|parquet)$"),
    ) -> Response:
        source_catalog: SourceCatalog | None = app.state.catalog
        if source_catalog is None:
            raise HTTPException(
                status_code=503, detail=app.state.source_error or "data root unavailable"
            )
        try:
            selection = source_catalog.selection(symbol, timeframe)
            names = features.split(",")
            with tempfile.TemporaryDirectory() as directory:
                table, sidecar = export_bar_features(
                    selection.source_path,
                    Path(directory),
                    symbol,
                    timeframe,
                    names,
                    cast(Literal["csv", "parquet"], format),
                    configured_settings.source_timezone,
                    configure_features(app.state.features, request.query_params),
                )
                archive_bytes = BytesIO()
                with zipfile.ZipFile(archive_bytes, "w", zipfile.ZIP_DEFLATED) as archive:
                    archive.write(table, table.name)
                    archive.write(sidecar, sidecar.name)
                return Response(
                    archive_bytes.getvalue(),
                    media_type="application/zip",
                    headers={
                        "Content-Disposition": f'attachment; filename="bar_features_{format}.zip"'
                    },
                )
        except (OSError, SourceValidationError, FeatureExportError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.get("/api/v1/bars")
    def bars(
        request: Request,
        symbol: str = Query(min_length=1, max_length=100),
        timeframe: str = Query(min_length=1, max_length=100),
        before: int | None = Query(default=None, description="Exclusive UTC Unix timestamp"),
        limit: int = Query(default=PAGE_SIZE, ge=1, le=PAGE_SIZE),
        features: str | None = Query(
            default=None, description="Comma-separated enabled feature view identifiers"
        ),
        mode: Literal["source", "stored"] = "source",
        import_session: UUID | None = None,
    ) -> dict[str, object]:
        if mode == "stored":
            try:
                imported: dict[tuple[str, str], StoredDataset] = app.state.imported_datasets.get(
                    import_session, {}
                )
                dataset = imported.get((symbol, timeframe))
                if dataset is None:
                    root = configured_settings.stored_data_root
                    if root is None:
                        raise StoredDataError("STORED_DATA_ROOT is not configured")
                    path = _stored_selection_path(root, symbol, timeframe)
                    dataset = load_stored_dataset(path)
                definitions = configure_stored_features(app.state.features, dataset.table.specs)
                return dataset.page(before, limit, definitions)
            except (OSError, StoredDataError, ValueError) as error:
                raise HTTPException(status_code=422, detail=str(error)) from error
        source_catalog: SourceCatalog | None = app.state.catalog
        if source_catalog is None:
            raise HTTPException(
                status_code=503, detail=app.state.source_error or "data root unavailable"
            )
        try:
            selection = source_catalog.selection(symbol, timeframe)
        except (OSError, SourceValidationError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        try:
            definitions = configure_features(app.state.features, request.query_params)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        views = {view.identifier: view for definition in definitions for view in definition.views}
        if features is None:
            enabled = set(views)
        else:
            enabled = {identifier for identifier in features.split(",") if identifier}
        unknown = enabled - views.keys()
        if unknown:
            raise HTTPException(
                status_code=422, detail=f"unknown feature identifiers: {sorted(unknown)!r}"
            )
        active_definitions = tuple(
            definition
            for definition in definitions
            if any(view.identifier in enabled for view in definition.views)
        )
        warm_up = max(
            (definition.calculation_warm_up for definition in active_definitions), default=0
        )
        try:
            page = load_bars(selection.source_path, configured_settings.source_timezone).page(
                before, limit, warm_up
            )
        except (OSError, SourceValidationError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        display_bars = page.display_bars
        display_times = {bar.time for bar in display_bars}
        feature_payload: list[dict[str, object]] = []
        for definition in definitions:
            selected_views = tuple(view for view in definition.views if view.identifier in enabled)
            required_features = frozenset(
                feature_name
                for view in selected_views
                for feature_name in (view.feature_name, view.color_feature)
                if feature_name is not None
            )
            table = (
                calculate_requested(definition, page.bars, required_features)
                if selected_views
                else None
            )
            if selected_views and table is None:
                continue
            empty_table = FeatureTable.from_columns(
                definition.specs,
                (),
                {spec.name: () for spec in definition.specs},
            )
            for view in definition.views:
                payload_view = (
                    view.definition(table, display_times)
                    if table is not None and view in selected_views
                    else view.definition(empty_table)
                )
                payload_view["settings"] = _view_settings(definition, view)
                feature_payload.append(payload_view)
        payload: dict[str, object] = {
            "bars": [bar.as_dict() for bar in display_bars],
            "indicators": feature_payload,
            "next_before": display_bars[0].time if page.has_more and display_bars else None,
            "has_more": page.has_more,
        }
        return payload

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIRECTORY / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIRECTORY), name="static")
    return app


def _empty_feature_payload(
    definitions: tuple[FeatureDefinition, ...],
) -> list[dict[str, object]]:
    payload: list[dict[str, object]] = []
    for definition in definitions:
        empty_table = FeatureTable.from_columns(
            definition.specs, (), {spec.name: () for spec in definition.specs}
        )
        for view in definition.views:
            payload_view = view.definition(empty_table)
            payload_view["settings"] = _view_settings(definition, view)
            payload.append(payload_view)
    return payload


def _view_settings(definition: FeatureDefinition, view: FeatureViewSpec) -> list[dict[str, object]]:
    parameters = next(
        spec.parameters for spec in definition.specs if spec.name == view.feature_name
    )
    return [
        setting.as_dict()
        for setting in definition.settings
        if any(name in parameters for name in setting.parameters)
    ]


app = create_app()


def _stored_catalog(root: Path | None) -> list[dict[str, object]]:
    if root is None or not root.is_dir():
        raise HTTPException(
            status_code=503,
            detail="Set STORED_DATA_ROOT to a directory of CSV/Parquet exports and sidecars",
        )
    by_asset: dict[str, list[str]] = {}
    for path in root.iterdir():
        if path.suffix.lower() not in {".csv", ".parquet"}:
            continue
        sidecar = path.with_name(path.name + ".json")
        if not sidecar.is_file():
            raise HTTPException(
                status_code=422, detail=f"{path.name}: missing metadata sidecar {sidecar.name}"
            )
        try:
            metadata = json.loads(sidecar.read_text(encoding="utf-8"))
            asset, timeframe = metadata["asset"], metadata["timeframe"]
            if not isinstance(asset, str) or not isinstance(timeframe, str):
                raise ValueError("asset and timeframe must be strings")
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise HTTPException(
                status_code=422, detail=f"{sidecar.name}: invalid dataset identity: {error}"
            ) from error
        by_asset.setdefault(asset, []).append(timeframe)
    return [
        {"symbol": asset, "timeframes": sorted(set(timeframes))}
        for asset, timeframes in sorted(by_asset.items())
    ]


def _stored_selection_path(root: Path, symbol: str, timeframe: str) -> Path:
    matching: list[Path] = []
    for path in root.iterdir():
        if path.suffix.lower() not in {".csv", ".parquet"}:
            continue
        sidecar = path.with_name(path.name + ".json")
        if not sidecar.is_file():
            continue
        try:
            identity = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(identity, dict) and (identity.get("asset"), identity.get("timeframe")) == (
            symbol,
            timeframe,
        ):
            matching.append(path)
    if not matching:
        raise StoredDataError(f"{symbol}/{timeframe}: stored export is not available")
    if len(matching) > 1:
        raise StoredDataError(
            f"{symbol}/{timeframe}: multiple stored exports; use separate directories"
        )
    return matching[0]
