# Breakout Research Chart Viewer

A small FastAPI application that loads one CSV or XLSX file once at startup and displays its bars in a dark, TradingView-style chart.

## Setup and run

Python 3.11+ is required. The default source is `C:\Users\amirj\OneDrive\Desktop\Book2.xlsx`; it is used unless `BARS_SOURCE_PATH` is set. Changing either setting requires a restart.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
npm ci
npx playwright install chromium
$env:BARS_SOURCE_PATH = 'C:\Users\amirj\OneDrive\Desktop\Book2.xlsx' # or Book2.csv
$env:SOURCE_TIMEZONE = 'UTC' # optional, applies only to naive timestamps
uvicorn app.main:app --reload
```

POSIX shells use `BARS_SOURCE_PATH=/path/to/Book2.xlsx SOURCE_TIMEZONE=UTC uvicorn app.main:app --reload`.

Open `http://127.0.0.1:8000`. `GET /api/v1/bars?limit=1000` returns the newest page in ascending order. Add `before=<exclusive UTC Unix seconds>` to request older history.

## CSV and XLSX format

The CSV header or first XLSX worksheet must have exact headers `time`, `open`, `high`, `low`, `close`, and either `volume` or `tick_volume` (when both exist, `volume` wins). Extra columns are ignored. Timestamps accept ISO-8601 strings, UTC Unix seconds, or UTC Unix milliseconds; values with sub-second precision are rejected. Naive ISO values use `SOURCE_TIMEZONE`, whose default is `UTC`.

The application validates file availability, headers, timestamps, numeric values, volume, chronological order, duplicates, and OHLC geometry at startup. A failed source remains unavailable with a clear HTTP 503 response.

## Verification

```powershell
pytest
ruff format --check .
ruff check .
mypy
npm run format:check
npm run lint
npm run typecheck
```
