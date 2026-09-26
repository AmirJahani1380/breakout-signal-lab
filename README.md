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

Open `http://127.0.0.1:8000`, choose a symbol, then choose one of its timeframes. The **Indicators** tab has numeric controls in each configurable indicator row for ATR, rolling overlap, RSI, EMA, and MACD fast/slow/signal periods; changing a setting reloads the chart and feature list. Feature names, export columns, and indicator labels include their selected periods. Filenames may be `SYMBOL_TIMEFRAME.csv` or the MT5 export form `SYMBOL_TIMEFRAME_max_bars.csv`; Parquet, XLSX, and hyphen-separated equivalents are also accepted. Startup only catalogs filenames, so an inaccessible or malformed unselected source does not block other datasets. `GET /api/v1/catalog` exposes available selections. `GET /api/v1/features` returns control metadata without calculating features; it accepts `atr_period`, `rolling_overlap_period`, `rsi_period`, `ema_period`, `macd_fast_period`, `macd_slow_period`, and `macd_signal_period` query parameters. `GET /api/v1/bars?symbol=EURUSD&timeframe=H1&limit=1000&features=volume,ema_20` returns at most 1,000 ascending display bars and calculates only the named feature views and their dependencies. Add `before=<exclusive UTC Unix seconds>` to request the next older page. Warm-up bars are never included in the displayed `bars` array.

To inspect previously exported research values, set `STORED_DATA_ROOT` to a directory containing CSV or Parquet export files alongside their `.csv.json` or `.parquet.json` metadata sidecars, restart the server, and choose **Stored data** from Chart mode. This mode reads saved OHLCV and features without running feature calculators. The chart matches available indicator views to the exported feature names and period parameters, while the Stored values inspector shows every exported feature, including columns without a view and missing values. The heading shows the dataset identity, source SHA-256 as its dataset version, and each feature version. Integers beyond JavaScript's exact number range appear as strings in the inspector. The Export tab is disabled in stored mode. `GET /api/v1/catalog?mode=stored` lists these exports; `GET /api/v1/bars?mode=stored&symbol=XAUUSDzero&timeframe=D1` returns pages with `stored_values` and `stored_metadata`. A stored export must include a matching sidecar, exact identity and timestamp contract, declared columns and feature types, and chronological unique timestamps. The example at `tests/fixtures/stored_export.csv` is a three-row excerpt of the supplied XAUUSDzero D1 export, with one additional research-only value for inspector coverage. Stored exports are scanned from one directory; duplicate asset/timeframe exports are rejected at selection.

You can also import local files directly in the browser without configuring `STORED_DATA_ROOT`. Choose **Stored data**, then select one or more `.csv` or `.parquet` exports together with their matching `<export filename>.json` sidecars in the file picker. For example, select `bar_features.csv` and `bar_features.csv.json` together. For a multi-file batch, use distinct filenames for each export/sidecar pair; repeated default filenames from different folders cannot be matched by the browser picker, so import those pairs in separate batches. Each file reports its own success or validation error, and valid imports remain available in that browser tab after a refresh. The symbol and timeframe controls group imports using the exported asset/timeframe metadata, regardless of filenames. Imports remain accessible until the server restarts or the browser tab closes. They are held in server memory, which is cleared on restart; closing a tab does not immediately reclaim that memory. Imports are not copied into the configured data root.

Use the Indicators tab to apply, hide, or remove features. Every control names its visualization: EMA is a line, RSI is a pane, candle range is crosshair-only, and engulfing is candle color. A disabled feature is removed immediately from chart series, candle colors, and crosshair details. Enabling engulfing colors matching candles yellow; disabling it restores the normal up/down colors. Other candle measurements remain crosshair-only and add no chart series.

## Breakout events

The **Events** tab offers independent EMA and confirmed swing detectors. Check a detector to show its signal close on the candle with a green upward pointer for bullish events or a red downward pointer for bearish events. Red dots mark confirmed swing highs and lows on their pivot candles. Select an event in the list or use Previous/Next to navigate; the selected event shows its exact setup, signal and availability times, frozen broken level, and a chart level line. Events are available for calculated source data. `GET /api/v1/events?symbol=EURUSD&timeframe=H1&limit=1000` returns records and pivot markers for the same display window as `/api/v1/bars`; `before` is an exclusive timestamp cursor. The detector list comes from `/api/v1/features` as `event_detectors`.

EMA uses the existing EMA calculation seeded from the first close of the **complete selected source**, default period 20. A bullish signal requires the preceding close to be strictly below the preceding EMA minus the price buffer and the signal close to be strictly above the signal EMA plus the buffer; bearish is the inverse. The broken level is the EMA value on the signal candle. Equal closes produce no crossing. Only closes matter, so an intrabar wick or opening gap without a qualifying close does not signal. A later reversal across both thresholds can produce another EMA event.

A swing high has a high strictly greater than the highs of the preceding `left` and following `right` bars; a swing low uses strictly smaller lows. Equal highs or lows anywhere in that window reject that pivot. Defaults are `left=3` and `right=3`: three bars on each side of the pivot. A pivot is confirmed at the close of its third right-hand bar and can be broken **starting on the next bar**. For each confirmed high, the preceding close must be strictly below its frozen high minus the buffer, and the signal close strictly above that high plus the buffer. A low uses the reverse rule. Each pivot can signal once; a new confirmed pivot is a new setup. `confirmed_swing_high_3_3` and `confirmed_swing_low_3_3` feature columns store the frozen level on the **confirmation** bar, where it first becomes known, while chart dots are drawn on pivot candles. Swing settings in Events accept 1–100 bars per side; the default feature columns use 3/3.

The price buffer defaults to 0 and is an absolute price amount, not a percentage. It must be finite and non-negative. EMA period accepts 1–1000; swing left/right accept 1–100 in the Events UI. These values are sent as `ema_period`, `swing_left`, `swing_right`, and `buffer`. Event records include a deterministic ID scoped to dataset, detector configuration, direction, setup and signal time; direction, setup ID, signal and availability times, frozen broken level, breakout close and reason are explicit. The ID and event membership do not depend on chart page size. Calculating from the complete selected source means adding or changing later bars cannot change earlier events.

The Export tab can download **encountered events** as a separate CSV or Parquet ZIP. Choose event detectors and optional feature columns, then use the event download buttons. This export includes one row per signal with the event fields, asset/timeframe, canonical signal bar OHLCV, selected feature values calculated from the source start, and a JSON schema sidecar. It does not alter the existing bar feature export. The matching API is `GET /api/v1/events/export?symbol=EURUSD&timeframe=H1&detectors=ema_breakout,swing_breakout&features=ema_20&format=csv`.

The Export tab selects feature **columns** independently of the Indicators tab. Choose a dataset, check one or more columns, and download CSV or Parquet. Each download is a ZIP containing exactly `bar_features.csv` or `bar_features.parquet` and its `bar_features.<format>.json` sidecar. The table contains asset, timeframe, UTC Unix-second timestamp, canonical OHLCV, and the selected columns. `volume` is already canonical and appears once if selected. Rows are chronological; asset, timeframe, and timestamp identify them. The sidecar records dataset identity, SHA-256 source fingerprint, timestamp and null conventions, declared dtypes, feature versions and parameters, and numeric tolerance (`1e-12` relative and absolute). CSV empty fields and Parquet nulls represent missing values. Read CSV using the sidecar dtypes, especially nullable booleans and integers.

For scripts, `app.feature_export.export_bar_features(source_path, output_directory, asset, timeframe, feature_names, format)` writes the same table and sidecar and raises `FileExistsError` if either destination already exists. EMA starts from the first close; ATR uses the first selected-period true ranges; RSI uses the first selected-period close changes; rolling overlap uses the previous selected number of complete bars. The exporter calculates from the beginning of the source, then withholds the selected features' full warm-up (at least 100 source rows for recursive indicators, increased when a selected period is larger; the selected number of rows for rolling overlap) so the CSV does not begin with unavailable seed values. The sidecar records this as `export_warm_up_rows`.

## Loading, paging, and warm-up policy

This experimental app deliberately has no dataset, page, or calculation cache. Each bars request loads the selected CSV, Parquet, or XLSX file with pandas, validates the complete selected dataset, and then slices the requested ascending page in memory. It does not open unselected datasets. Older pages use an exclusive timestamp cursor, so adjacent pages do not overlap and the browser can prepend history without duplicate timestamps. Source and validation errors return HTTP 422 for the selected dataset.

EMA, RSI, MACD, and ATR use a bounded seed window on every page: at least 100 bars, increased to the selected indicator period where needed; engulfing uses one preceding candle and rolling overlap uses the selected period. These recursive values are deterministic for that declared boundary policy, but they are not claimed to be mathematically identical to calculations seeded from the complete history. At the beginning of a dataset, only the available seed rows are used.

ATR uses Wilder smoothing. The first true range is the first candle's high minus low; later true ranges are the maximum of high minus low and the distances from the previous close to high and low. For selected period `p`, the first ATR is the arithmetic mean of `p` true ranges at candle `p` (index `p - 1`), so earlier values are null. Each later ATR is `(previous ATR * (p - 1) + current true range) / p`. `atr_p_to_close` divides by the current close. Candle range, body size, upper wick, and lower wick are each divided independently by ATR `p` and by the current close. A zero denominator or unavailable ATR yields null. These values are optional crosshair details; ATR also has an optional pane. The default period is 20.

`rolling_overlap_p` uses only the previous `p` completed candles. It spans their minimum low to maximum high with `p` equal-width bins and samples each midpoint. At each midpoint, the contribution is `max(number of candle ranges containing that price - 1, 0) / (p - 1)`; the score is the mean of the `p` contributions, between 0 and 1. Identical ranges score 1, separated ranges score 0, and greater shared coverage raises the score. Insufficient or invalid history yields null; a valid zero-width window scores 1. This is a `p`-point approximation, so a narrow shared region can fall between sample points. The optional histogram uses distinct translucent colors above and below 0.5; exactly 0.5 uses the chart's default color. The default period is 20.

## Feature architecture

Each calculation lives in its own `app/features` module and exports a `FeatureDefinition`. A definition separates calculation metadata (`FeatureSpec`) from rendering and crosshair metadata (`FeatureViewSpec`). Calculations return a `FeatureTable` whose integer Unix timestamp index exactly matches the source bars; warm-up rows remain nullable. Views only format columns already present in that table, and only views with an explicit renderer are plotted.

To add an indicator, create a module in `app/features/` that exports `feature = FeatureDefinition(...)`; discovery adds it to the chart and export catalog. For a numeric control, give the definition a `FeatureSetting` in `settings` and a `configure` callback that returns a definition for the requested values. Record those values in each `FeatureSpec.parameters`, and give changing feature columns and views stable `selection_key` values so checked exports and applied views survive a settings change. The API and browser build controls from these declarations; adding an indicator requires no edits to `app/main.py`, `static/app.js`, or `static/index.html`. If a calculation needs preceding bars, declare its warm-up and return an aligned `FeatureTable`; export uses the feature's calculator for the full source history.

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
