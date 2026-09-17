from __future__ import annotations

import os
import tempfile
import zipfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Literal, cast

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from .bars import (
    SourceCatalog,
    SourceValidationError,
    discover_source_catalog,
    load_bars,
)
from .feature_export import FeatureExportError, export_bar_features
from .features import FeatureDefinition, FeatureTable, calculate_requested, discover

DEFAULT_DATA_ROOT = Path(
    r"C:\Users\amirj\OneDrive\Desktop\programming\Trade\data analysis\mt5_data"
)
STATIC_DIRECTORY = Path(__file__).parent.parent / "static"
PAGE_SIZE = 1000


@dataclass(frozen=True, slots=True)
class Settings:
    data_root: Path
    source_timezone: str = "UTC"

    @classmethod
    def from_environment(cls) -> Settings:
        return cls(
            Path(os.getenv("BARS_DATA_ROOT", str(DEFAULT_DATA_ROOT))),
            os.getenv("SOURCE_TIMEZONE", "UTC"),
        )


def create_app(settings: Settings | None = None) -> FastAPI:
    configured_settings = settings or Settings.from_environment()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.features = discover()
        try:
            app.state.catalog = discover_source_catalog(configured_settings.data_root)
            app.state.source_error = None
        except SourceValidationError as error:
            app.state.catalog = None
            app.state.source_error = str(error)
        yield

    app = FastAPI(title="Breakout Research Chart Viewer", lifespan=lifespan)

    @app.get("/api/v1/catalog")
    def catalog() -> dict[str, object]:
        source_catalog: SourceCatalog | None = app.state.catalog
        if source_catalog is None:
            raise HTTPException(
                status_code=503, detail=app.state.source_error or "data root unavailable"
            )
        return {"symbols": source_catalog.symbols()}

    @app.get("/api/v1/features")
    def features() -> dict[str, object]:
        definitions: tuple[FeatureDefinition, ...] = app.state.features
        return {
            "indicators": _empty_feature_payload(definitions),
            "export_features": [
                spec.as_dict() for definition in definitions for spec in definition.specs
            ],
        }

    @app.get("/api/v1/export")
    def export(
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
                    app.state.features,
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
        except (OSError, SourceValidationError, FeatureExportError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.get("/api/v1/bars")
    def bars(
        symbol: str = Query(min_length=1, max_length=100),
        timeframe: str = Query(min_length=1, max_length=100),
        before: int | None = Query(default=None, description="Exclusive UTC Unix timestamp"),
        limit: int = Query(default=PAGE_SIZE, ge=1, le=PAGE_SIZE),
        features: str | None = Query(
            default=None, description="Comma-separated enabled feature view identifiers"
        ),
    ) -> dict[str, object]:
        source_catalog: SourceCatalog | None = app.state.catalog
        if source_catalog is None:
            raise HTTPException(
                status_code=503, detail=app.state.source_error or "data root unavailable"
            )
        try:
            selection = source_catalog.selection(symbol, timeframe)
        except (OSError, SourceValidationError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        definitions: tuple[FeatureDefinition, ...] = app.state.features
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
                feature_payload.append(
                    view.definition(table, display_times)
                    if table is not None and view in selected_views
                    else view.definition(empty_table)
                )
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
        payload.extend(view.definition(empty_table) for view in definition.views)
    return payload


app = create_app()
