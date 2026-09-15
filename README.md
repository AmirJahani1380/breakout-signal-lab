# Breakout Research Chart Viewer

A small FastAPI application that discovers symbol/timeframe CSV or XLSX filenames at startup and loads bars only after a chart selection. Calculated features use timestamp-aligned pandas tables and can be persisted as Parquet through PyArrow.

## Setup and run

Python 3.11+ is required. The default data root is `C:\Users\amirj\OneDrive\Desktop\programming\Trade\data analysis\mt5_data`; set `BARS_DATA_ROOT` to use another directory. Changing either setting requires a restart.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
npm ci
npx playwright install chromium
$env:BARS_DATA_ROOT = 'C:\market-data' # contains EURUSD_H1.csv, EURUSD_M15.xlsx, etc.
$env:SOURCE_TIMEZONE = 'UTC' # optional, applies only to naive timestamps
uvicorn app.main:app --reload
```

POSIX shells use `BARS_DATA_ROOT=/path/to/market-data SOURCE_TIMEZONE=UTC uvicorn app.main:app --reload`.

Open `http://127.0.0.1:8000`, choose a symbol, then choose one of its timeframes. Filenames may be `SYMBOL_TIMEFRAME.csv` or the MT5 export form `SYMBOL_TIMEFRAME_max_bars.csv`; XLSX and hyphen-separated equivalents are also accepted. Startup reads only filenames and filesystem metadata; it does not parse bar rows. `GET /api/v1/catalog` exposes available selections, and `GET /api/v1/bars?symbol=EURUSD&timeframe=H1&limit=1000` loads the selected file and returns discovered indicator definitions and points in its `indicators` array. Add `before=<exclusive UTC Unix seconds>` to request older history.

Use the Indicators tab to apply, hide, or remove Volume, EMA 20, RSI 14, and the three MACD outputs. EMA is shown on the price pane; RSI and MACD use separate panes.

## Feature architecture

Each calculation lives in its own `app/features` module and exports a `FeatureDefinition`. A definition separates calculation metadata (`FeatureSpec`) from rendering and crosshair metadata (`FeatureViewSpec`). Calculations return a `FeatureTable` whose integer Unix timestamp index exactly matches the source bars; warm-up rows remain nullable. Views only format columns already present in that table.

```python
table.to_parquet("features.parquet")
restored = FeatureTable.from_parquet("features.parquet")
```

Feature dtypes use pandas nullable `Float64`, `Int64`, or `boolean` types so nulls and logical types survive the Parquet round trip. A view may render a line, histogram, a Lightweight Charts series marker without a connecting line, a separate pane, or nothing, and crosshair visibility is configured independently. A null color feature omits the per-point color so the chart renderer can use its default. See the bundled feature modules for concise definitions. Invalid modules or non-finite calculated values are skipped with a warning naming the problem.

## CSV and XLSX format

The CSV header or first XLSX worksheet must have exact headers `time`, `open`, `high`, `low`, `close`, and either `volume` or `tick_volume` (when both exist, `volume` wins). Extra columns are ignored. Timestamps accept ISO-8601 strings, UTC Unix seconds, or UTC Unix milliseconds; values with sub-second precision are rejected. Naive ISO values use `SOURCE_TIMEZONE`, whose default is `UTC`.

The application validates file availability, headers, timestamps, numeric values, volume, chronological order, duplicates, and OHLC geometry when a timeframe is selected. An unavailable data root returns HTTP 503; an invalid selection or malformed selected file returns a clear HTTP 422 response.

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
