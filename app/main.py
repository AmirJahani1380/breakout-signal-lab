from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .bars import SourceCatalog, SourceValidationError, discover_source_catalog, load_bars
from .features import FeatureDefinition, calculate, discover

DEFAULT_DATA_ROOT = Path(
    r"C:\Users\amirj\OneDrive\Desktop\programming\Trade\data analysis\mt5_data"
)
STATIC_DIRECTORY = Path(__file__).parent.parent / "static"


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

    @app.get("/api/v1/bars")
    def bars(
        symbol: str = Query(min_length=1, max_length=100),
        timeframe: str = Query(min_length=1, max_length=100),
        before: int | None = Query(default=None, description="Exclusive UTC Unix timestamp"),
        limit: int = Query(default=1000, ge=1, le=5000),
    ) -> dict[str, object]:
        source_catalog: SourceCatalog | None = app.state.catalog
        if source_catalog is None:
            raise HTTPException(
                status_code=503, detail=app.state.source_error or "data root unavailable"
            )
        try:
            selection = source_catalog.selection(symbol, timeframe)
            store = load_bars(selection.source_path, configured_settings.source_timezone)
        except SourceValidationError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        page, has_more = store.page(before, limit)
        page_times = {bar.time for bar in page}
        definitions: tuple[FeatureDefinition, ...] = app.state.features
        feature_payload: list[dict[str, object]] = []
        for definition in definitions:
            table = calculate(definition, store.bars)
            if table is not None:
                feature_payload.extend(
                    view.definition(table, page_times) for view in definition.views
                )
        return {
            "bars": [bar.as_dict() for bar in page],
            "indicators": feature_payload,
            "next_before": page[0].time if has_more and page else None,
            "has_more": has_more,
        }

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIRECTORY / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIRECTORY), name="static")
    return app


app = create_app()
