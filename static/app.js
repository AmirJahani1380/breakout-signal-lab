// @ts-check
const LightweightCharts = /** @type {any} */ (window).LightweightCharts;

const chartElement = /** @type {HTMLElement | null} */ (
  document.querySelector("#chart")
);
const stateElement = document.querySelector("#state");
const legendElement = document.querySelector("#legend");
if (!(chartElement instanceof HTMLElement) || !stateElement || !legendElement)
  throw new Error("Missing chart UI");

const chart = LightweightCharts.createChart(chartElement, {
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
let nextBefore = null;
let hasMore = true;
let loading = false;
let initialized = false;

function redraw() {
  candles.setData(bars);
  volume.setData(
    bars.map((bar) => ({
      time: bar.time,
      value: bar.volume,
      color: bar.close >= bar.open ? "#00d08499" : "#ff4d6d99",
    })),
  );
  chartElement.dataset.barCount = String(bars.length);
}

function setInitialState(message) {
  stateElement.textContent = message;
  chartElement.hidden = true;
}

function showChart() {
  chartElement.hidden = false;
  stateElement.textContent = "";
}

function showError(message, initial) {
  if (initial) {
    candles.setData([]);
    volume.setData([]);
    setInitialState(message);
  } else {
    stateElement.textContent = message;
  }
}

async function load(before = null) {
  if (loading || (!hasMore && before !== null)) return;
  loading = true;
  if (before === null) setInitialState("Loading chart…");
  try {
    const url = new URL("/api/v1/bars", window.location.origin);
    if (before !== null) url.searchParams.set("before", String(before));
    const response = await fetch(url);
    if (!response.ok)
      throw new Error(
        (await response.json()).detail || `Server error ${response.status}`,
      );
    const payload = await response.json();
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
    showError(
      `Unable to load bars: ${error instanceof Error ? error.message : String(error)}`,
      before === null,
    );
  } finally {
    loading = false;
  }
}

chart.subscribeCrosshairMove((event) => {
  const bar = event.seriesData.get(candles);
  if (bar && typeof bar === "object" && "open" in bar) {
    const point =
      /** @type {{open:number, high:number, low:number, close:number}} */ (bar);
    const time = new Date(Number(event.time) * 1000)
      .toISOString()
      .replace(".000Z", "Z");
    const matching = bars.find((candidate) => candidate.time === event.time);
    legendElement.textContent = `${time}  O ${point.open} H ${point.high} L ${point.low} C ${point.close} V ${matching?.volume ?? ""}`;
  }
});
chart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
  if (range && range.from < 100 && hasMore && nextBefore !== null)
    load(nextBefore);
});
load();
