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

Open `http://127.0.0.1:8000`, choose a symbol, then choose one of its timeframes. Filenames may be `SYMBOL_TIMEFRAME.csv` or the MT5 export form `SYMBOL_TIMEFRAME_max_bars.csv`; Parquet, XLSX, and hyphen-separated equivalents are also accepted. Startup only catalogs filenames, so an inaccessible or malformed unselected source does not block other datasets. `GET /api/v1/catalog` exposes available selections. `GET /api/v1/features` returns control metadata without calculating features. `GET /api/v1/bars?symbol=EURUSD&timeframe=H1&limit=1000&features=volume,ema_20` returns at most 1,000 ascending display bars and calculates only the named feature views and their dependencies. Add `before=<exclusive UTC Unix seconds>` to request the next older page. Warm-up bars are never included in the displayed `bars` array.

Use the Indicators tab to apply, hide, or remove features. Every control names its visualization: EMA is a line, RSI is a pane, candle range is crosshair-only, and engulfing is candle color. A disabled feature is removed immediately from chart series, candle colors, and crosshair details. Enabling engulfing colors matching candles yellow; disabling it restores the normal up/down colors. Other candle measurements remain crosshair-only and add no chart series.

The Export tab selects feature **columns** independently of the Indicators tab. Choose a dataset, check one or more columns, and download CSV or Parquet. Each download is a ZIP containing exactly `bar_features.csv` or `bar_features.parquet` and its `bar_features.<format>.json` sidecar. The table contains asset, timeframe, UTC Unix-second timestamp, canonical OHLCV, and the selected columns. `volume` is already canonical and appears once if selected. Rows are chronological; asset, timeframe, and timestamp identify them. The sidecar records dataset identity, SHA-256 source fingerprint, timestamp and null conventions, declared dtypes, feature versions and parameters, and numeric tolerance (`1e-12` relative and absolute). CSV empty fields and Parquet nulls represent missing values. Read CSV using the sidecar dtypes, especially nullable booleans and integers.

For scripts, `app.feature_export.export_bar_features(source_path, output_directory, asset, timeframe, feature_names, format)` writes the same table and sidecar and raises `FileExistsError` if either destination already exists. Export uses one chronological pass. EMA starts from the first close; ATR uses the first 20 true ranges; RSI uses the first 14 close changes; rolling overlap uses the previous 20 complete bars. The exporter calculates from the beginning of the source, then withholds the selected features' full warm-up (100 source rows for ATR 20 and RSI 14; 20 for rolling overlap) so the CSV does not begin with their unavailable seed values. The sidecar records this as `export_warm_up_rows`.

## Loading, paging, and warm-up policy

This experimental app deliberately has no dataset, page, or calculation cache. Each bars request loads the selected CSV, Parquet, or XLSX file with pandas, validates the complete selected dataset, and then slices the requested ascending page in memory. It does not open unselected datasets. Older pages use an exclusive timestamp cursor, so adjacent pages do not overlap and the browser can prepend history without duplicate timestamps. Source and validation errors return HTTP 422 for the selected dataset.

EMA 20, RSI 14, MACD, and ATR 20 deliberately use a bounded 100-bar seed window on every page; engulfing uses one preceding candle and rolling overlap uses 20. These recursive values are deterministic for that declared boundary policy, but they are not claimed to be mathematically identical to calculations seeded from the complete history. At the beginning of a dataset, only the available seed rows are used.

ATR 20 uses Wilder smoothing. The first true range is the first candle's high minus low; later true ranges are the maximum of high minus low and the distances from the previous close to high and low. The first ATR is the arithmetic mean of 20 true ranges at candle 20 (index 19), so earlier values are null. Each later ATR is `(previous ATR * 19 + current true range) / 20`. `atr_20_to_close` divides by the current close. Candle range, body size, upper wick, and lower wick are each divided independently by ATR 20 and by the current close. A zero denominator or unavailable ATR yields null. These values are optional crosshair details; ATR also has an optional pane.

`rolling_overlap_20` uses only the previous 20 completed candles. It spans their minimum low to maximum high with 20 equal-width bins and samples each midpoint. At each midpoint, the contribution is `max(number of candle ranges containing that price - 1, 0) / 19`; the score is the mean of the 20 contributions, between 0 and 1. Identical ranges score 1, separated ranges score 0, and greater shared coverage raises the score. Insufficient or invalid history yields null; a valid zero-width window scores 1. This is a 20-point approximation, so a narrow shared region can fall between sample points. The optional histogram uses distinct translucent colors above and below 0.5; exactly 0.5 uses the chart's default color.

## Feature architecture

Each calculation lives in its own `app/features` module and exports a `FeatureDefinition`. A definition separates calculation metadata (`FeatureSpec`) from rendering and crosshair metadata (`FeatureViewSpec`). Calculations return a `FeatureTable` whose integer Unix timestamp index exactly matches the source bars; warm-up rows remain nullable. Views only format columns already present in that table, and only views with an explicit renderer are plotted.

```python
table.to_parquet("features.parquet")
restored = FeatureTable.from_parquet("features.parquet")
```

Feature dtypes use pandas nullable `Float64`, `Int64`, or `boolean` types so nulls and logical types survive the Parquet round trip. A view may render a line, histogram, a Lightweight Charts series marker without a connecting line, a separate pane, or nothing, and crosshair visibility is configured independently. A null color feature omits the per-point color so the chart renderer can use its default. See the bundled feature modules for concise definitions. Invalid modules or non-finite calculated values are skipped with a warning naming the problem.

## CSV, Parquet, and XLSX format

CSV, Parquet, and XLSX share the same pandas-backed normalization contract. Sources must include exact names `time`, `open`, `high`, `low`, `close`, and either `volume` or `tick_volume` (when both exist, `volume` wins). The first XLSX worksheet uses the same canonical names. Timestamps accept ISO-8601 strings, UTC Unix seconds, or UTC Unix milliseconds; values with sub-second precision are rejected. Naive ISO values use `SOURCE_TIMEZONE`, whose default is `UTC`.

Additional CSV and Parquet columns are retained in `BarStore.imported_features`, indexed by normalized UTC timestamp and marked with imported provenance. They remain separate from canonical bars and computed `FeatureTable` columns, so a vendor column such as `candle_range` cannot overwrite the computed value and is not automatically displayed. Use `imported_feature_column`, `computed_feature_column`, and `compare_feature_columns` from `app.features.comparison` to compare matching columns. Numeric values use configurable relative and absolute tolerances; boolean and categorical values compare exactly. Missing timestamps and differing values are returned in the comparison result without changing either source.

`is_engulfing` is true for a bullish reversal when the previous candle is bearish, the current candle is bullish, its open is strictly inside the previous candle's low/high range, and its close is above the previous high. The bearish rule is the inverse: previous bullish, current bearish, open strictly inside the previous range, and close below the previous low. It only uses the current and preceding bars; all bundled candle calculations are causal.

Selecting a timeframe validates the complete selected source, including timestamp syntax and order, duplicate timestamps, numeric price/volume values, and OHLC geometry. An unavailable data root returns HTTP 503; an invalid selection or malformed source returns a clear HTTP 422 response with its original source row or CSV record number.

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
