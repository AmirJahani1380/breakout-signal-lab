# Breakout Research Chart Viewer

A small FastAPI application that discovers symbol/timeframe CSV, Parquet, or XLSX filenames at startup and loads bars only after a chart selection. Calculated features use timestamp-aligned pandas tables and can be persisted as Parquet through PyArrow.

## Setup and run

Python 3.11+ is required. The default data root is `C:\Users\amirj\OneDrive\Desktop\programming\Trade\data analysis\mt5_data`; set `BARS_DATA_ROOT` to use another directory. Changing either setting requires a restart.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
npm ci
npx playwright install chromium
$env:BARS_DATA_ROOT = 'C:\market-data' # contains EURUSD_H1.csv/.parquet/.xlsx, etc.
$env:SOURCE_TIMEZONE = 'UTC' # optional, applies only to naive timestamps
uvicorn app.main:app --reload
```

POSIX shells use `BARS_DATA_ROOT=/path/to/market-data SOURCE_TIMEZONE=UTC uvicorn app.main:app --reload`.

Open `http://127.0.0.1:8000`, choose a symbol, then choose one of its timeframes. Filenames may be `SYMBOL_TIMEFRAME.csv` or the MT5 export form `SYMBOL_TIMEFRAME_max_bars.csv`; Parquet, XLSX, and hyphen-separated equivalents are also accepted. Startup reads only filenames and filesystem metadata; it does not parse bar rows. `GET /api/v1/catalog` exposes available selections, and `GET /api/v1/bars?symbol=EURUSD&timeframe=H1&limit=1000` loads the selected file and returns discovered indicator definitions and points in its `indicators` array. Add `before=<exclusive UTC Unix seconds>` to request older history.

Use the Indicators tab to apply, hide, or remove Volume, EMA 20, RSI 14, and the three MACD outputs. EMA is shown on the price pane; RSI and MACD use separate panes. Candle range, body, body/range, upper and lower wick, direction, and engulfing values are configured as crosshair-only details, so they add no chart markers or lines.

## Feature architecture

Each calculation lives in its own `app/features` module and exports a `FeatureDefinition`. A definition separates calculation metadata (`FeatureSpec`) from rendering and crosshair metadata (`FeatureViewSpec`). Calculations return a `FeatureTable` whose integer Unix timestamp index exactly matches the source bars; warm-up rows remain nullable. Views only format columns already present in that table, and only views with an explicit renderer are plotted.

```python
table.to_parquet("features.parquet")
restored = FeatureTable.from_parquet("features.parquet")
```

Feature dtypes use pandas nullable `Float64`, `Int64`, or `boolean` types so nulls and logical types survive the Parquet round trip. A view may render a line, histogram, a Lightweight Charts series marker without a connecting line, a separate pane, or nothing, and crosshair visibility is configured independently. A null color feature omits the per-point color so the chart renderer can use its default. See the bundled feature modules for concise definitions. Invalid modules or non-finite calculated values are skipped with a warning naming the problem.

## CSV, Parquet, and XLSX format

CSV and Parquet use the same pandas-backed normalization and validation. Their columns must include exact names `time`, `open`, `high`, `low`, `close`, and either `volume` or `tick_volume` (when both exist, `volume` wins). The first XLSX worksheet uses the same canonical names. Timestamps accept ISO-8601 strings, UTC Unix seconds, or UTC Unix milliseconds; values with sub-second precision are rejected. Naive ISO values use `SOURCE_TIMEZONE`, whose default is `UTC`.

Additional CSV and Parquet columns are retained in `BarStore.imported_features`, indexed by normalized UTC timestamp and marked with imported provenance. They remain separate from canonical bars and computed `FeatureTable` columns, so a vendor column such as `candle_range` cannot overwrite the computed value and is not automatically displayed. Use `imported_feature_column`, `computed_feature_column`, and `compare_feature_columns` from `app.features.comparison` to compare matching columns. Numeric values use configurable relative and absolute tolerances; boolean and categorical values compare exactly. Missing timestamps and differing values are returned in the comparison result without changing either source.

`is_engulfing` is true for a bullish reversal when the previous candle is bearish, the current candle is bullish, its open is strictly inside the previous candle's low/high range, and its close is above the previous high. The bearish rule is the inverse: previous bullish, current bearish, open strictly inside the previous range, and close below the previous low. It only uses the current and preceding bars; all bundled candle calculations are causal.

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
