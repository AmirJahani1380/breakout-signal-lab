from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .bars import BarStore, SourceValidationError, load_bars

DEFAULT_SOURCE_PATH = Path(r"C:\Users\amirj\OneDrive\Desktop\Book2.xlsx")
STATIC_DIRECTORY = Path(__file__).parent.parent / "static"


@dataclass(frozen=True, slots=True)
class Settings:
    source_path: Path
    source_timezone: str = "UTC"

    @classmethod
    def from_environment(cls) -> Settings:
        return cls(
            Path(
                os.getenv("BARS_SOURCE_PATH", os.getenv("BARS_XLSX_PATH", str(DEFAULT_SOURCE_PATH)))
            ),
            os.getenv("SOURCE_TIMEZONE", "UTC"),
        )


def create_app(settings: Settings | None = None) -> FastAPI:
    configured_settings = settings or Settings.from_environment()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            app.state.store = load_bars(
                configured_settings.source_path, configured_settings.source_timezone
            )
            app.state.source_error = None
        except SourceValidationError as error:
            app.state.store = None
            app.state.source_error = str(error)
        yield

    app = FastAPI(title="Breakout Research Chart Viewer", lifespan=lifespan)

    @app.get("/api/v1/bars")
    def bars(
        before: int | None = Query(default=None, description="Exclusive UTC Unix timestamp"),
        limit: int = Query(default=1000, ge=1, le=5000),
    ) -> dict[str, object]:
        store: BarStore | None = app.state.store
        if store is None:
            raise HTTPException(
                status_code=503, detail=app.state.source_error or "source unavailable"
            )
        page, has_more = store.page(before, limit)
        return {
            "bars": [bar.as_dict() for bar in page],
            "next_before": page[0].time if has_more and page else None,
            "has_more": has_more,
        }

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIRECTORY / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIRECTORY), name="static")
    return app


app = create_app()
