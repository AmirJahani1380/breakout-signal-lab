# Breakout Research Chart Viewer

A small FastAPI application that discovers symbol/timeframe CSV or XLSX filenames at startup and loads bars only after a chart selection.

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

Use the Indicators tab to apply, hide, or remove Volume, EMA 20, and RSI 14. EMA is shown on the price pane; RSI is shown in its own 0–100 pane.

## Custom indicators

Add a module such as `app/indicators/my_indicator.py` and restart the server. Export one `Indicator` named `indicator`; no frontend or API registration is needed:

```python
from collections.abc import Sequence

from app.bars import Bar
from app.indicators import Indicator, IndicatorPoint


def calculate(bars: Sequence[Bar]) -> tuple[IndicatorPoint, ...]:
    return tuple(IndicatorPoint(bar.time, bar.close) for bar in bars)


indicator = Indicator(
    identifier="my_indicator",
    label="My indicator",
    description="Price overlay",
    series_type="LineSeries",  # or HistogramSeries
    pane="main",  # or separate
    calculate=calculate,
    series_options={"color": "#2962ff", "lineWidth": 2},
)
```

Points must use source-bar timestamps, be finite, unique, and chronological. Optional metadata supports default applied/visible state, price-scale options, separate-pane height, a fixed scale range, and reference lines; see the bundled indicator modules for concise examples. Invalid modules are skipped with a warning naming the module and problem.

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
