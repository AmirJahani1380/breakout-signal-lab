// @ts-check
const LightweightCharts = /** @type {any} */ (window).LightweightCharts;

const chartElement = /** @type {HTMLElement | null} */ (
  document.querySelector("#chart")
);
const stateElement = document.querySelector("#state");
const legendElement = document.querySelector("#legend");
const symbolElement = document.querySelector("#symbol");
const timeframesElement = document.querySelector("#timeframes");
const indicatorsTabElement = document.querySelector("#indicators-tab");
const indicatorsPanelElement = document.querySelector("#indicators-panel");
if (
  !(chartElement instanceof HTMLElement) ||
  !stateElement ||
  !legendElement ||
  !(symbolElement instanceof HTMLSelectElement) ||
  !(timeframesElement instanceof HTMLElement) ||
  !(indicatorsTabElement instanceof HTMLButtonElement) ||
  !(indicatorsPanelElement instanceof HTMLElement)
)
  throw new Error("Missing chart UI");
const chartContainer = chartElement;
const state = stateElement;
const legend = legendElement;
const symbolSelect = symbolElement;
const timeframes = timeframesElement;
const indicatorsTab = indicatorsTabElement;
const indicatorsPanel = indicatorsPanelElement;

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

/**
 * @typedef {{time:number, value:number, color?:string}} IndicatorPoint
 * @typedef {{id:string, label:string, description:string, series_type:string,
 * pane:"main"|"separate", default_applied:boolean, default_visible:boolean,
 * series_options:Record<string, any>, price_scale_options:Record<string, any>,
 * pane_height:number|null, scale_range:[number, number]|null,
 * reference_lines:Array<Record<string, any>>, points:IndicatorPoint[]}} IndicatorDefinition
 */

/** @type {IndicatorDefinition[]} */
let indicatorDefinitions = [];
/** @type {Map<string, {applied:boolean, visible:boolean}>} */
const indicatorStates = new Map();
/** @type {Map<string, IndicatorPoint[]>} */
const indicatorPoints = new Map();
/** @type {Map<string, any>} */
const indicatorSeries = new Map();

/** @param {IndicatorDefinition} definition */
function createIndicatorSeries(definition) {
  const state = indicatorStates.get(definition.id);
  if (!state) return;
  const options = /** @type {Record<string, any>} */ ({
    ...definition.series_options,
    visible: state.visible,
  });
  if (definition.scale_range)
    options.autoscaleInfoProvider = () => ({
      priceRange: {
        minValue: definition.scale_range?.[0],
        maxValue: definition.scale_range?.[1],
      },
    });
  const paneIndex = definition.pane === "main" ? 0 : chart.panes().length;
  /** @type {any | null} */
  let series = null;
  try {
    const seriesType = LightweightCharts[definition.series_type];
    series = chart.addSeries(seriesType, options, paneIndex);
    if (Object.keys(definition.price_scale_options).length)
      series.priceScale().applyOptions(definition.price_scale_options);
    for (const line of definition.reference_lines) series.createPriceLine(line);
    if (definition.pane_height !== null)
      chart.panes()[paneIndex]?.setHeight(definition.pane_height);
    indicatorSeries.set(definition.id, series);
  } catch (error) {
    if (series)
      try {
        chart.removeSeries(series);
      } catch (removeError) {
        console.error(
          `Unable to remove indicator ${definition.id}`,
          removeError,
        );
      }
    state.applied = false;
    console.error(`Unable to create indicator ${definition.id}`, error);
  }
}

function updateIndicatorSeries() {
  /** @type {Record<string, number>} */
  const pointCounts = {};
  for (const definition of indicatorDefinitions) {
    const state = indicatorStates.get(definition.id);
    const points = indicatorPoints.get(definition.id) ?? [];
    indicatorSeries.get(definition.id)?.setData(points);
    pointCounts[definition.id] = state?.applied ? points.length : 0;
  }
  chartContainer.dataset.indicatorPointCounts = JSON.stringify(pointCounts);
}

/** @param {string} identifier */
function renderIndicatorState(identifier) {
  const row = indicatorsPanel.querySelector(`[data-indicator="${identifier}"]`);
  const state = indicatorStates.get(identifier);
  if (!(row instanceof HTMLElement) || !state) return;
  const visibility = row.querySelector("[data-visibility]");
  const status = row.querySelector("[data-indicator-state]");
  if (!(visibility instanceof HTMLButtonElement) || !status) return;
  visibility.disabled = !state.applied;
  visibility.textContent = state.visible ? "Hide" : "Show";
  visibility.setAttribute("aria-pressed", String(state.visible));
  status.textContent = state.applied
    ? `Applied · ${state.visible ? "Visible" : "Hidden"}`
    : "Not applied";
}

/** @param {string} identifier @param {boolean} applied */
function setIndicatorApplied(identifier, applied) {
  const state = indicatorStates.get(identifier);
  const definition = indicatorDefinitions.find(
    (candidate) => candidate.id === identifier,
  );
  if (!state || !definition) return;
  state.applied = applied;
  const series = indicatorSeries.get(identifier);
  if (applied && !series) createIndicatorSeries(definition);
  if (!applied && series) {
    chart.removeSeries(series);
    indicatorSeries.delete(identifier);
  }
  updateIndicatorSeries();
  renderIndicatorState(identifier);
}

/** @param {string} identifier */
function toggleIndicatorVisibility(identifier) {
  const state = indicatorStates.get(identifier);
  if (!state?.applied) return;
  state.visible = !state.visible;
  indicatorSeries.get(identifier)?.applyOptions({ visible: state.visible });
  renderIndicatorState(identifier);
}

function renderIndicatorList() {
  indicatorsPanel.replaceChildren();
  if (!indicatorDefinitions.length) {
    indicatorsPanel.textContent = "No indicators available.";
    return;
  }
  for (const definition of indicatorDefinitions) {
    const row = document.createElement("div");
    row.className = "indicator";
    row.dataset.indicator = definition.id;
    const heading = document.createElement("div");
    const label = document.createElement("strong");
    const description = document.createElement("small");
    label.textContent = definition.label;
    description.textContent = definition.description;
    heading.append(label, description);
    const applyLabel = document.createElement("label");
    const apply = document.createElement("input");
    apply.type = "checkbox";
    apply.dataset.apply = "";
    apply.checked = indicatorStates.get(definition.id)?.applied ?? false;
    apply.addEventListener("change", () =>
      setIndicatorApplied(definition.id, apply.checked),
    );
    applyLabel.append(apply, " Applied");
    const visibility = document.createElement("button");
    visibility.type = "button";
    visibility.dataset.visibility = "";
    visibility.addEventListener("click", () =>
      toggleIndicatorVisibility(definition.id),
    );
    const status = document.createElement("span");
    status.dataset.indicatorState = "";
    row.append(heading, applyLabel, visibility, status);
    indicatorsPanel.append(row);
    renderIndicatorState(definition.id);
  }
}

/** @param {IndicatorDefinition[]} definitions @param {boolean} prepend */
function receiveIndicators(definitions, prepend) {
  if (!prepend) {
    const identifiers = new Set(definitions.map((definition) => definition.id));
    for (const [identifier, series] of indicatorSeries)
      if (!identifiers.has(identifier)) {
        chart.removeSeries(series);
        indicatorSeries.delete(identifier);
      }
    indicatorDefinitions = definitions;
    for (const definition of definitions) {
      if (!indicatorStates.has(definition.id))
        indicatorStates.set(definition.id, {
          applied: definition.default_applied,
          visible: definition.default_visible,
        });
      indicatorPoints.set(definition.id, definition.points);
      if (
        indicatorStates.get(definition.id)?.applied &&
        !indicatorSeries.has(definition.id)
      )
        createIndicatorSeries(definition);
    }
    renderIndicatorList();
  } else {
    for (const definition of definitions)
      indicatorPoints.set(definition.id, [
        ...definition.points,
        ...(indicatorPoints.get(definition.id) ?? []),
      ]);
  }
  updateIndicatorSeries();
}

/** @type {Array<{time:number, open:number, high:number, low:number, close:number}>} */
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
  updateIndicatorSeries();
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
    bars = [];
    for (const definition of indicatorDefinitions)
      indicatorPoints.set(definition.id, []);
    updateIndicatorSeries();
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
    receiveIndicators(payload.indicators ?? [], before !== null);
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
  for (const definition of indicatorDefinitions)
    indicatorPoints.set(definition.id, []);
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

indicatorsTab.addEventListener("click", () => {
  const opening = indicatorsPanel.hidden;
  indicatorsPanel.hidden = !opening;
  indicatorsTab.setAttribute("aria-selected", String(opening));
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
      const indicatorValues = indicatorDefinitions.flatMap((definition) => {
        const series = indicatorSeries.get(definition.id);
        const indicatorPoint = series && event.seriesData.get(series);
        return indicatorPoint &&
          typeof indicatorPoint === "object" &&
          "value" in indicatorPoint
          ? [`${definition.label} ${indicatorPoint.value}`]
          : [];
      });
      legend.textContent = `${time}  O ${point.open} H ${point.high} L ${point.low} C ${point.close}${indicatorValues.length ? `  ${indicatorValues.join("  ")}` : ""}`;
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
