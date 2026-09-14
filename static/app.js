// @ts-check
const LightweightCharts = /** @type {any} */ (window).LightweightCharts;

const chartElement = /** @type {HTMLElement | null} */ (
  document.querySelector("#chart")
);
const stateElement = document.querySelector("#state");
const legendElement = document.querySelector("#legend");
const symbolElement = document.querySelector("#symbol");
const timeframesElement = document.querySelector("#timeframes");
if (
  !(chartElement instanceof HTMLElement) ||
  !stateElement ||
  !legendElement ||
  !(symbolElement instanceof HTMLSelectElement) ||
  !(timeframesElement instanceof HTMLElement)
)
  throw new Error("Missing chart UI");
const chartContainer = chartElement;
const state = stateElement;
const legend = legendElement;
const symbolSelect = symbolElement;
const timeframes = timeframesElement;

const chart = LightweightCharts.createChart(chartContainer, {
  autoSize: true,
  layout: { background: { color: "#131722" }, textColor: "#d1d4dc" },
  grid: { vertLines: { color: "#1f2430" }, horzLines: { color: "#1f2430" } },
  timeScale: { timeVisible: true, secondsVisible: false },
});
/** @type {any} */ (window).__breakoutChart = chart;
const candles = chart.addSeries(LightweightCharts.CandlestickSeries, {
  upColor: "#26a69a",
  downColor: "#ef5350",
  borderVisible: false,
  wickUpColor: "#26a69a",
  wickDownColor: "#ef5350",
});
const volume = chart.addSeries(LightweightCharts.HistogramSeries, {
  priceFormat: { type: "volume" },
  priceScaleId: "volume",
  lastValueVisible: false,
});
volume.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });

/** @type {Array<{time:number, open:number, high:number, low:number, close:number, volume:number}>} */
let bars = [];
/** @type {number | null} */
let nextBefore = null;
let hasMore = true;
let loading = false;
let initialized = false;
/** @type {{symbol:string, timeframe:string} | null} */
let activeSelection = null;
let selectionVersion = 0;

function redraw() {
  candles.setData(bars);
  volume.setData(
    bars.map((bar) => ({
      time: bar.time,
      value: bar.volume,
      color: bar.close >= bar.open ? "#00d08499" : "#ff4d6d99",
    })),
  );
  chartContainer.dataset.barCount = String(bars.length);
}

/** @param {string} message */
function setInitialState(message) {
  state.textContent = message;
  chartContainer.hidden = true;
}

function showChart() {
  chartContainer.hidden = false;
  state.textContent = "";
}

/** @param {string} message @param {boolean} initial */
function showError(message, initial) {
  if (initial) {
    candles.setData([]);
    volume.setData([]);
    setInitialState(message);
  } else {
    state.textContent = message;
  }
}

/** @param {number | null} before @param {number} version */
async function load(before, version) {
  if (version !== selectionVersion || !activeSelection) return;
  if (loading || (!hasMore && before !== null)) return;
  loading = true;
  if (before === null) setInitialState("Loading chart…");
  try {
    const url = new URL("/api/v1/bars", window.location.origin);
    url.searchParams.set("symbol", activeSelection.symbol);
    url.searchParams.set("timeframe", activeSelection.timeframe);
    if (before !== null) url.searchParams.set("before", String(before));
    const response = await fetch(url);
    if (!response.ok)
      throw new Error(
        (await response.json()).detail || `Server error ${response.status}`,
      );
    const payload = await response.json();
    if (version !== selectionVersion) return;
    const range = chart.timeScale().getVisibleLogicalRange();
    const olderCount = before === null ? 0 : payload.bars.length;
    bars = before === null ? payload.bars : [...payload.bars, ...bars];
    nextBefore = payload.next_before;
    hasMore = payload.has_more;
    redraw();
    if (!initialized && bars.length) {
      chart.timeScale().setVisibleLogicalRange({
        from: Math.max(0, bars.length - 150),
        to: bars.length - 1,
      });
      initialized = true;
    } else if (range && olderCount) {
      chart.timeScale().setVisibleLogicalRange({
        from: range.from + olderCount,
        to: range.to + olderCount,
      });
    }
    if (bars.length) showChart();
    else setInitialState("No bars in this source.");
  } catch (error) {
    if (version !== selectionVersion) return;
    showError(
      `Unable to load bars: ${error instanceof Error ? error.message : String(error)}`,
      before === null,
    );
  } finally {
    if (version === selectionVersion) loading = false;
  }
}

/** @param {string} symbol */
function renderTimeframes(symbol) {
  timeframes.replaceChildren();
  const selected = catalog.find((entry) => entry.symbol === symbol);
  for (const timeframe of selected?.timeframes ?? []) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = timeframe;
    button.setAttribute("aria-pressed", "false");
    button.addEventListener("click", () => selectTimeframe(symbol, timeframe));
    timeframes.append(button);
  }
}

/** @param {string} symbol @param {string} timeframe */
function selectTimeframe(symbol, timeframe) {
  if (
    loading &&
    activeSelection?.symbol === symbol &&
    activeSelection.timeframe === timeframe
  )
    return;
  selectionVersion += 1;
  loading = false;
  activeSelection = { symbol, timeframe };
  bars = [];
  nextBefore = null;
  hasMore = true;
  initialized = false;
  legend.textContent = `${symbol} ${timeframe}`;
  redraw();
  timeframes.querySelectorAll("button").forEach((button) => {
    button.setAttribute(
      "aria-pressed",
      String(button.textContent === timeframe),
    );
  });
  load(null, selectionVersion);
}

/** @type {Array<{symbol:string, timeframes:string[]}>} */
let catalog = [];

async function loadCatalog() {
  setInitialState("Loading available symbols…");
  try {
    const response = await fetch("/api/v1/catalog");
    if (!response.ok)
      throw new Error(
        (await response.json()).detail || `Server error ${response.status}`,
      );
    const payload = await response.json();
    catalog = payload.symbols;
    for (const entry of catalog) {
      const option = document.createElement("option");
      option.value = entry.symbol;
      option.textContent = entry.symbol;
      symbolSelect.append(option);
    }
    symbolSelect.disabled = false;
    setInitialState("Select a symbol and timeframe.");
  } catch (error) {
    setInitialState(
      `Unable to load available symbols: ${error instanceof Error ? error.message : String(error)}`,
    );
  }
}

symbolSelect.addEventListener("change", () => {
  activeSelection = null;
  selectionVersion += 1;
  loading = false;
  renderTimeframes(symbolSelect.value);
  setInitialState(
    symbolSelect.value
      ? "Select a timeframe."
      : "Select a symbol and timeframe.",
  );
});

chart.subscribeCrosshairMove(
  /** @param {any} event */ (event) => {
    const bar = event.seriesData.get(candles);
    if (bar && typeof bar === "object" && "open" in bar) {
      const point =
        /** @type {{open:number, high:number, low:number, close:number}} */ (
          bar
        );
      const time = new Date(Number(event.time) * 1000)
        .toISOString()
        .replace(".000Z", "Z");
      const matching = bars.find((candidate) => candidate.time === event.time);
      legend.textContent = `${time}  O ${point.open} H ${point.high} L ${point.low} C ${point.close} V ${matching?.volume ?? ""}`;
    }
  },
);
chart.timeScale().subscribeVisibleLogicalRangeChange(
  /** @param {any} range */ (range) => {
    if (range && range.from < 100 && hasMore && nextBefore !== null)
      load(nextBefore, selectionVersion);
  },
);
loadCatalog();
