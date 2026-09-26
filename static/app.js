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
const exportTabElement = document.querySelector("#export-tab");
const exportPanelElement = document.querySelector("#export-panel");
const exportFeaturesElement = document.querySelector("#export-features");
const exportStatusElement = document.querySelector("#export-status");
const exportCsvElement = document.querySelector("#export-csv");
const exportParquetElement = document.querySelector("#export-parquet");
const eventsTab = /** @type {HTMLButtonElement} */ (
  document.querySelector("#events-tab")
);
const eventsPanel = /** @type {HTMLElement} */ (
  document.querySelector("#events-panel")
);
const eventDetectors = /** @type {HTMLElement} */ (
  document.querySelector("#event-detectors")
);
const eventDetails = /** @type {HTMLElement} */ (
  document.querySelector("#event-details")
);
const eventList = /** @type {HTMLElement} */ (
  document.querySelector("#event-list")
);
const exportEventDetectors = /** @type {HTMLElement} */ (
  document.querySelector("#export-event-detectors")
);
const swingLeft = /** @type {HTMLInputElement} */ (
  document.querySelector("#swing-left")
);
const swingRight = /** @type {HTMLInputElement} */ (
  document.querySelector("#swing-right")
);
const eventBuffer = /** @type {HTMLInputElement} */ (
  document.querySelector("#event-buffer")
);
const previousEvent = /** @type {HTMLButtonElement} */ (
  document.querySelector("#previous-event")
);
const nextEvent = /** @type {HTMLButtonElement} */ (
  document.querySelector("#next-event")
);
const exportEventsCsv = /** @type {HTMLButtonElement} */ (
  document.querySelector("#export-events-csv")
);
const exportEventsParquet = /** @type {HTMLButtonElement} */ (
  document.querySelector("#export-events-parquet")
);
const dataModeElement = document.querySelector("#data-mode");
const datasetVersionElement = document.querySelector("#dataset-version");
const valueInspectorElement = document.querySelector("#value-inspector");
const inspectTimeElement = document.querySelector("#inspect-time");
const inspectValuesElement = document.querySelector("#inspect-values");
const storedImportElement = document.querySelector("#stored-import");
const storedFilesElement = document.querySelector("#stored-files");
const storedImportStatusElement = document.querySelector(
  "#stored-import-status",
);
const storedImportResultsElement = document.querySelector(
  "#stored-import-results",
);
if (
  !(chartElement instanceof HTMLElement) ||
  !stateElement ||
  !legendElement ||
  !(symbolElement instanceof HTMLSelectElement) ||
  !(timeframesElement instanceof HTMLElement) ||
  !(indicatorsTabElement instanceof HTMLButtonElement) ||
  !(indicatorsPanelElement instanceof HTMLElement) ||
  !(exportTabElement instanceof HTMLButtonElement) ||
  !(exportPanelElement instanceof HTMLElement) ||
  !(exportFeaturesElement instanceof HTMLElement) ||
  !(exportStatusElement instanceof HTMLElement) ||
  !(exportCsvElement instanceof HTMLButtonElement) ||
  !(exportParquetElement instanceof HTMLButtonElement) ||
  !(dataModeElement instanceof HTMLSelectElement) ||
  !(datasetVersionElement instanceof HTMLElement) ||
  !(valueInspectorElement instanceof HTMLElement) ||
  !(inspectTimeElement instanceof HTMLSelectElement) ||
  !(inspectValuesElement instanceof HTMLElement) ||
  !(storedImportElement instanceof HTMLElement) ||
  !(storedFilesElement instanceof HTMLInputElement) ||
  !(storedImportStatusElement instanceof HTMLElement) ||
  !(storedImportResultsElement instanceof HTMLElement)
)
  throw new Error("Missing chart UI");
const chartContainer = chartElement;
const state = stateElement;
const legend = legendElement;
const symbolSelect = symbolElement;
const timeframes = timeframesElement;
const indicatorsTab = indicatorsTabElement;
const indicatorsPanel = indicatorsPanelElement;
const exportTab = exportTabElement;
const exportPanel = exportPanelElement;
const exportFeatures = exportFeaturesElement;
/** @type {HTMLInputElement[]} */
let settingInputs = [];
/** @type {Map<string, string>} */
const selectedPeriods = new Map();
const exportStatus = exportStatusElement;
const exportCsv = exportCsvElement;
const exportParquet = exportParquetElement;
const dataMode = dataModeElement;
const datasetVersion = datasetVersionElement;
const valueInspector = valueInspectorElement;
const inspectTime = inspectTimeElement;
const inspectValues = inspectValuesElement;
const storedImport = storedImportElement;
const storedFiles = storedFilesElement;
const storedImportStatus = storedImportStatusElement;
const storedImportResults = storedImportResultsElement;
const importSession =
  sessionStorage.getItem("storedImportSession") ?? crypto.randomUUID();
sessionStorage.setItem("storedImportSession", importSession);
/** @type {Record<string, Array<{time:number,value:number|string|boolean|null}>>} */
let storedValues = {};
/** @type {Array<{name:string,version:string}>} */
let storedFeatures = [];

function renderStoredInspector() {
  if (dataMode.value !== "stored") return;
  const selected = Number(inspectTime.value);
  inspectValues.replaceChildren();
  for (const feature of storedFeatures) {
    const detail = storedValues[feature.name]?.find(
      (entry) => entry.time === selected,
    );
    const row = document.createElement("p");
    row.textContent = `${feature.name} (v${feature.version}): ${detail?.value == null ? "missing" : String(detail.value)}`;
    inspectValues.append(row);
  }
}
inspectTime.addEventListener("change", renderStoredInspector);

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
/** @typedef {{id:string,detector:string,configuration:Record<string, number>,direction:string,setup_id:string,signal_time:number,availability_time:number,broken_level:number,breakout_price:number,reason:string}} BreakoutEvent */
/** @type {BreakoutEvent[]} */
let breakoutEvents = [];
/** @type {Array<{direction:string,pivot_time:number,availability_time:number,level:number}>} */
let confirmedSwings = [];
/** @type {string | null} */
let selectedEventId = null;
/** @type {any} */
let selectedLevelLine = null;
const breakoutMarkers = LightweightCharts.createSeriesMarkers(candles, []);

function selectedDetectorIds() {
  return Array.from(
    eventDetectors.querySelectorAll("input:checked"),
    (input) => /** @type {HTMLInputElement} */ (input).value,
  );
}

/** @param {URL} url */
function addEventSettings(url) {
  url.searchParams.set("detectors", selectedDetectorIds().join(","));
  url.searchParams.set("swing_left", swingLeft.value);
  url.searchParams.set("swing_right", swingRight.value);
  url.searchParams.set("buffer", eventBuffer.value);
}

function renderEventMarkers() {
  const visibleTimes = new Set(bars.map((bar) => bar.time));
  const markers = [
    ...confirmedSwings
      .filter((swing) => visibleTimes.has(swing.pivot_time))
      .map((swing) => ({
        time: swing.pivot_time,
        position: "atPriceMiddle",
        price: swing.level,
        shape: "circle",
        color: "#ef5350",
        size: 1,
        text: `Confirmed ${swing.direction}`,
      })),
    ...breakoutEvents
      .filter(
        (event) =>
          visibleTimes.has(event.signal_time) &&
          selectedDetectorIds().includes(event.detector),
      )
      .map((event) => ({
        time: event.signal_time,
        position:
          event.direction === "bullish" ? "atPriceBottom" : "atPriceTop",
        price: event.breakout_price,
        shape: event.direction === "bullish" ? "arrowUp" : "arrowDown",
        color: event.direction === "bullish" ? "#26a69a" : "#ef5350",
        text: String(event.breakout_price),
      })),
  ].sort((left, right) => left.time - right.time);
  breakoutMarkers.setMarkers(markers);
  chartContainer.dataset.eventMarkerCount = String(markers.length);
}

function renderEventList() {
  const visible = breakoutEvents.filter((event) =>
    selectedDetectorIds().includes(event.detector),
  );
  eventList.replaceChildren();
  for (const event of visible) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = `${new Date(event.signal_time * 1000).toISOString()} ${event.detector} ${event.direction} ${event.breakout_price}`;
    button.dataset.eventId = event.id;
    button.addEventListener("click", () => selectEvent(event.id));
    eventList.append(button);
  }
  const selected = visible.find((event) => event.id === selectedEventId);
  if (selectedLevelLine) candles.removePriceLine(selectedLevelLine);
  selectedLevelLine = null;
  eventDetails.textContent = selected
    ? `${selected.reason}; setup ${selected.setup_id}; broken level ${selected.broken_level}; signal ${new Date(selected.signal_time * 1000).toISOString()}; available ${new Date(selected.availability_time * 1000).toISOString()}`
    : `${visible.length} loaded events`;
  if (selected) {
    selectedLevelLine = candles.createPriceLine({
      price: selected.broken_level,
      color: selected.direction === "bullish" ? "#26a69a" : "#ef5350",
      lineWidth: 1,
      axisLabelVisible: true,
      title: selected.setup_id,
    });
    const index = bars.findIndex((bar) => bar.time === selected.signal_time);
    if (index >= 0)
      chart.timeScale().setVisibleLogicalRange({
        from: Math.max(0, index - 25),
        to: Math.min(bars.length - 1, index + 25),
      });
  }
  chartContainer.dataset.selectedEventId = selected?.id ?? "";
  chartContainer.dataset.selectedBrokenLevel = selected
    ? String(selected.broken_level)
    : "";
  renderEventMarkers();
}

/** @param {string} identifier */
function selectEvent(identifier) {
  selectedEventId = identifier;
  renderEventList();
}

/** @param {number} step */
function navigateEvent(step) {
  const visible = breakoutEvents.filter((event) =>
    selectedDetectorIds().includes(event.detector),
  );
  if (!visible.length) return;
  const position = visible.findIndex((event) => event.id === selectedEventId);
  const next =
    position < 0
      ? step > 0
        ? 0
        : visible.length - 1
      : Math.max(0, Math.min(visible.length - 1, position + step));
  selectEvent(visible[next].id);
}
previousEvent.addEventListener("click", () => navigateEvent(-1));
nextEvent.addEventListener("click", () => navigateEvent(1));
for (const input of [swingLeft, swingRight, eventBuffer])
  input.addEventListener("change", () => {
    if (!input.reportValidity()) return;
    if (activeSelection)
      selectTimeframe(activeSelection.symbol, activeSelection.timeframe);
  });

/**
 * @typedef {{time:number, value:number, color?:string}} IndicatorPoint
 * @typedef {{applied:boolean, visible:boolean}} IndicatorState
 * @typedef {{id:string, selection_key?:string, feature_name:string, source:"computed"|"imported", label:string, description:string,
 * visualization:string, candle_color?:string|null,
 * renderer:"line"|"histogram"|"marker"|null, series_type:string|null,
 * pane:"main"|"separate", default_applied:boolean, default_visible:boolean,
 * show_in_crosshair:boolean,
 * series_options:Record<string, any>, price_scale_options:Record<string, any>,
 * pane_height:number|null, scale_range:[number, number]|null,
 * reference_lines:Array<Record<string, any>>, points:IndicatorPoint[],
 * values:Array<{time:number,value:number|boolean}>,
 * settings:Array<{key:string,label:string,default:number,minimum:number,maximum:number}>}} IndicatorDefinition
 */

/** @type {IndicatorDefinition[]} */
let indicatorDefinitions = [];
/** @type {Map<string, IndicatorState>} */
const indicatorStates = new Map();
/** @type {Map<string, IndicatorState>} */
const pendingPeriodStates = new Map();
/** @type {Map<string, IndicatorPoint[]>} */
const indicatorPoints = new Map();
/** @type {Map<string, any>} */
const indicatorSeries = new Map();
/** @type {Map<string, any>} */
const indicatorMarkers = new Map();

/** @param {IndicatorDefinition} definition */
function markerPoints(definition) {
  const options = definition.series_options;
  const position = String(options.position ?? "atPriceMiddle");
  const points = indicatorPoints.get(definition.id) ?? definition.points;
  return points.map((point) => ({
    time: point.time,
    color: String(options.color ?? "#2962ff"),
    position,
    shape: String(options.shape ?? "circle"),
    ...(options.text === undefined ? {} : { text: String(options.text) }),
    ...(options.size === undefined ? {} : { size: Number(options.size) }),
    ...(position.startsWith("atPrice") ? { price: point.value } : {}),
  }));
}

/** @param {IndicatorDefinition} definition */
function createIndicatorSeries(definition) {
  const state = indicatorStates.get(definition.id);
  if (!state || definition.renderer === null) return;
  if (definition.renderer === "marker") {
    indicatorMarkers.set(
      definition.id,
      LightweightCharts.createSeriesMarkers(
        candles,
        state.visible ? markerPoints(definition) : [],
      ),
    );
    return;
  }
  if (definition.series_type === null) return;
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
    indicatorMarkers
      .get(definition.id)
      ?.setMarkers(
        state?.applied && state.visible ? markerPoints(definition) : [],
      );
    pointCounts[definition.id] = state?.applied ? points.length : 0;
  }
  chartContainer.dataset.indicatorPointCounts = JSON.stringify(pointCounts);
  chartContainer.dataset.markerCount = String(
    [...indicatorMarkers.keys()].reduce(
      (total, identifier) =>
        total + (indicatorPoints.get(identifier)?.length ?? 0),
      0,
    ),
  );
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
  const markers = indicatorMarkers.get(identifier);
  if (applied && !series && !markers) createIndicatorSeries(definition);
  if (!applied && series) {
    chart.removeSeries(series);
    indicatorSeries.delete(identifier);
  }
  if (!applied && markers) {
    markers.setMarkers([]);
    indicatorMarkers.delete(identifier);
  }
  updateIndicatorSeries();
  renderIndicatorState(identifier);
  if (!applied && activeSelection)
    legend.textContent = `${activeSelection.symbol} ${activeSelection.timeframe}`;
  if (activeSelection && dataMode.value !== "stored")
    void refreshIndicators(selectionVersion);
  redraw();
}

/** @param {string} identifier */
function toggleIndicatorVisibility(identifier) {
  const state = indicatorStates.get(identifier);
  const definition = indicatorDefinitions.find(
    (candidate) => candidate.id === identifier,
  );
  if (!state?.applied || !definition) return;
  state.visible = !state.visible;
  indicatorSeries.get(identifier)?.applyOptions({ visible: state.visible });
  indicatorMarkers
    .get(identifier)
    ?.setMarkers(state.visible ? markerPoints(definition) : []);
  renderIndicatorState(identifier);
}

function renderIndicatorList() {
  settingInputs = [];
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
    description.textContent = `${definition.visualization ?? "indicator"} · ${definition.description}`;
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
    const settings = document.createElement("div");
    settings.className = "indicator-settings";
    for (const setting of definition.settings ?? []) {
      const settingLabel = document.createElement("label");
      settingLabel.textContent = setting.label;
      const input = document.createElement("input");
      input.type = "number";
      input.min = String(setting.minimum);
      input.max = String(setting.maximum);
      input.step = "1";
      if (!selectedPeriods.has(setting.key))
        selectedPeriods.set(setting.key, String(setting.default));
      input.value = selectedPeriods.get(setting.key) ?? String(setting.default);
      input.dataset.period = setting.key;
      input.addEventListener("change", onSettingChange);
      settingLabel.append(input);
      settings.append(settingLabel);
      settingInputs.push(input);
    }
    row.append(heading, applyLabel, visibility, status);
    if (settings.childElementCount) row.append(settings);
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
    for (const [identifier, markers] of indicatorMarkers)
      if (!identifiers.has(identifier)) {
        markers.setMarkers([]);
        indicatorMarkers.delete(identifier);
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
        definition.renderer !== null &&
        indicatorStates.get(definition.id)?.applied &&
        !indicatorSeries.has(definition.id) &&
        !indicatorMarkers.has(definition.id)
      )
        createIndicatorSeries(definition);
    }
    renderIndicatorList();
  } else {
    for (const definition of definitions) {
      const currentPoints = indicatorPoints.get(definition.id) ?? [];
      const currentTimes = new Set(currentPoints.map((point) => point.time));
      indicatorPoints.set(definition.id, [
        ...definition.points.filter((point) => !currentTimes.has(point.time)),
        ...currentPoints,
      ]);
      const current = indicatorDefinitions.find(
        (candidate) => candidate.id === definition.id,
      );
      if (current) {
        const currentValueTimes = new Set(
          (current.values ?? []).map((value) => value.time),
        );
        current.values = [
          ...definition.values.filter(
            (value) => !currentValueTimes.has(value.time),
          ),
          ...(current.values ?? []),
        ];
      }
    }
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
/** @type {{symbol:string, timeframe:string} | null} */
let pendingPeriodSelection = null;
let selectionVersion = 0;
let featureRefreshVersion = 0;
/** @type {(number | null)[]} */
let loadedBefores = [];

/** @param {URL} url */
function addEnabledFeatures(url) {
  const enabled = [...indicatorStates]
    .filter(([, featureState]) => featureState.applied)
    .map(([identifier]) => identifier);
  if (indicatorStates.size) url.searchParams.set("features", enabled.join(","));
}

function addPeriods(url) {
  for (const [key, value] of selectedPeriods) url.searchParams.set(key, value);
}

/** @param {number} version */
async function refreshIndicators(version) {
  if (dataMode.value === "stored") return;
  if (!activeSelection || version !== selectionVersion || !loadedBefores.length)
    return;
  const refreshVersion = ++featureRefreshVersion;
  try {
    let prepend = false;
    for (const before of loadedBefores) {
      const url = new URL("/api/v1/bars", window.location.origin);
      url.searchParams.set("symbol", activeSelection.symbol);
      url.searchParams.set("timeframe", activeSelection.timeframe);
      addPeriods(url);
      if (before !== null) url.searchParams.set("before", String(before));
      addEnabledFeatures(url);
      const response = await fetch(url);
      if (!response.ok)
        throw new Error(
          (await response.json()).detail || `Server error ${response.status}`,
        );
      const payload = await response.json();
      if (
        version !== selectionVersion ||
        refreshVersion !== featureRefreshVersion
      )
        return;
      receiveIndicators(payload.indicators ?? [], prepend);
      prepend = true;
    }
    redraw();
  } catch (error) {
    if (
      version === selectionVersion &&
      refreshVersion === featureRefreshVersion
    )
      state.textContent = `Unable to load features: ${error instanceof Error ? error.message : String(error)}`;
  }
}

function redraw() {
  const candleColors = new Map();
  for (const definition of indicatorDefinitions) {
    if (
      !definition.candle_color ||
      !indicatorStates.get(definition.id)?.applied
    )
      continue;
    for (const detail of definition.values)
      if (detail.value === true)
        candleColors.set(detail.time, definition.candle_color);
  }
  candles.setData(
    bars.map((bar) => {
      const color = candleColors.get(bar.time);
      return color
        ? { ...bar, color, wickColor: color, borderColor: color }
        : bar;
    }),
  );
  chartContainer.dataset.candleColorCount = String(candleColors.size);
  updateIndicatorSeries();
  chartContainer.dataset.barCount = String(bars.length);
  renderEventMarkers();
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
    url.searchParams.set("mode", dataMode.value);
    if (dataMode.value === "stored")
      url.searchParams.set("import_session", importSession);
    url.searchParams.set("symbol", activeSelection.symbol);
    url.searchParams.set("timeframe", activeSelection.timeframe);
    addPeriods(url);
    if (before !== null) url.searchParams.set("before", String(before));
    addEnabledFeatures(url);
    const response = await fetch(url);
    if (!response.ok)
      throw new Error(
        (await response.json()).detail || `Server error ${response.status}`,
      );
    const payload = await response.json();
    if (version !== selectionVersion) return;
    if (dataMode.value === "source") {
      const eventsUrl = new URL("/api/v1/events", window.location.origin);
      eventsUrl.searchParams.set("symbol", activeSelection.symbol);
      eventsUrl.searchParams.set("timeframe", activeSelection.timeframe);
      if (before !== null) eventsUrl.searchParams.set("before", String(before));
      addPeriods(eventsUrl);
      addEventSettings(eventsUrl);
      const eventsResponse = await fetch(eventsUrl);
      if (!eventsResponse.ok)
        throw new Error((await eventsResponse.json()).detail);
      const eventPayload = await eventsResponse.json();
      if (version !== selectionVersion) return;
      breakoutEvents =
        before === null
          ? eventPayload.events
          : [...eventPayload.events, ...breakoutEvents];
      confirmedSwings =
        before === null
          ? eventPayload.swings
          : [...eventPayload.swings, ...confirmedSwings];
      renderEventList();
    }
    const range = chart.timeScale().getVisibleLogicalRange();
    const existingTimes = new Set(bars.map((bar) => bar.time));
    const olderBars =
      before === null
        ? payload.bars
        : payload.bars.filter((bar) => !existingTimes.has(bar.time));
    const olderCount = before === null ? 0 : olderBars.length;
    bars = before === null ? olderBars : [...olderBars, ...bars];
    if (dataMode.value === "stored") {
      if (!payload.stored_metadata?.features || !payload.stored_values)
        throw new Error(
          "Stored response is missing feature metadata; reload the export and sidecar.",
        );
      storedFeatures = payload.stored_metadata.features;
      datasetVersion.textContent = `Stored data · ${payload.stored_metadata.dataset_id} · dataset version (source SHA-256) ${payload.stored_metadata.dataset_version} · feature versions ${storedFeatures.map((feature) => `${feature.name}:v${feature.version}`).join(", ")}`;
      for (const [name, entries] of Object.entries(payload.stored_values)) {
        const previous = before === null ? [] : (storedValues[name] ?? []);
        storedValues[name] = [...entries, ...previous];
      }
      const previousTime = inspectTime.value;
      inspectTime.replaceChildren(
        ...bars.map((bar) => {
          const option = document.createElement("option");
          option.value = String(bar.time);
          option.textContent = new Date(bar.time * 1000).toISOString();
          return option;
        }),
      );
      if (bars.some((bar) => String(bar.time) === previousTime))
        inspectTime.value = previousTime;
      else if (bars.length)
        inspectTime.value = String(bars[bars.length - 1].time);
      renderStoredInspector();
    }
    receiveIndicators(payload.indicators ?? [], before !== null);
    if (!loadedBefores.includes(before)) loadedBefores.push(before);
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
  pendingPeriodSelection = null;
  selectionVersion += 1;
  loading = false;
  activeSelection = { symbol, timeframe };
  bars = [];
  breakoutEvents = [];
  confirmedSwings = [];
  selectedEventId = null;
  for (const definition of indicatorDefinitions)
    indicatorPoints.set(definition.id, []);
  nextBefore = null;
  hasMore = true;
  initialized = false;
  loadedBefores = [];
  storedValues = {};
  datasetVersion.textContent =
    dataMode.value === "stored" ? "Stored data" : "Calculated data";
  valueInspector.hidden = dataMode.value !== "stored";
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

/** @param {Map<string, IndicatorState>} [previousStates] @returns {Promise<boolean>} */
async function loadCatalog(previousStates = new Map()) {
  const requestVersion = selectionVersion;
  const requestedMode = dataMode.value;
  setInitialState("Loading available symbols…");
  try {
    const featureUrl = new URL("/api/v1/features", window.location.origin);
    addPeriods(featureUrl);
    const featureResponse = await fetch(featureUrl);
    if (!featureResponse.ok)
      throw new Error(
        (await featureResponse.json()).detail ||
          `Server error ${featureResponse.status}`,
      );
    const featurePayload = await featureResponse.json();
    if (requestVersion !== selectionVersion || requestedMode !== dataMode.value)
      return false;
    for (const definition of featurePayload.indicators ?? []) {
      const previousState = previousStates.get(
        definition.selection_key ?? definition.id,
      );
      if (previousState)
        indicatorStates.set(definition.id, { ...previousState });
    }
    receiveIndicators(featurePayload.indicators ?? [], false);
    const previouslyChecked = new Set(
      Array.from(
        eventDetectors.querySelectorAll("input:checked"),
        (input) => /** @type {HTMLInputElement} */ (input).value,
      ),
    );
    const previouslyExported = new Set(
      Array.from(
        exportEventDetectors.querySelectorAll("input:checked"),
        (input) => /** @type {HTMLInputElement} */ (input).value,
      ),
    );
    const hadDetectorChoices = eventDetectors.querySelector("input") !== null;
    eventDetectors.replaceChildren();
    exportEventDetectors.replaceChildren();
    for (const detector of featurePayload.event_detectors ?? []) {
      for (const container of [eventDetectors, exportEventDetectors]) {
        const label = document.createElement("label");
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.value = detector.id;
        checkbox.checked =
          !hadDetectorChoices ||
          (container === eventDetectors
            ? previouslyChecked
            : previouslyExported
          ).has(detector.id);
        if (container === eventDetectors)
          checkbox.addEventListener("change", () => {
            if (activeSelection)
              selectTimeframe(
                activeSelection.symbol,
                activeSelection.timeframe,
              );
          });
        label.append(checkbox, ` ${detector.label}`);
        container.append(label);
      }
    }
    const hadExportFeatures = exportFeatures.querySelector("input") !== null;
    const selectedExportFeatures = new Set(
      Array.from(
        exportFeatures.querySelectorAll("input:checked"),
        (input) => /** @type {HTMLInputElement} */ (input).dataset.selectionKey,
      ),
    );
    exportFeatures.replaceChildren();
    for (const feature of featurePayload.export_features ?? []) {
      const label = document.createElement("label");
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.value = feature.name;
      checkbox.dataset.selectionKey = feature.selection_key ?? feature.name;
      checkbox.checked =
        !hadExportFeatures ||
        selectedExportFeatures.has(checkbox.dataset.selectionKey);
      label.append(checkbox, ` ${feature.name}`);
      exportFeatures.append(label);
    }
    const catalogUrl = new URL("/api/v1/catalog", window.location.origin);
    catalogUrl.searchParams.set("mode", requestedMode);
    if (requestedMode === "stored")
      catalogUrl.searchParams.set("import_session", importSession);
    const response = await fetch(catalogUrl);
    if (!response.ok)
      throw new Error(
        (await response.json()).detail || `Server error ${response.status}`,
      );
    const payload = await response.json();
    if (requestVersion !== selectionVersion || requestedMode !== dataMode.value)
      return false;
    catalog = payload.symbols;
    symbolSelect.replaceChildren(new Option("Select a symbol", ""));
    for (const entry of catalog) {
      const option = document.createElement("option");
      option.value = entry.symbol;
      option.textContent = entry.symbol;
      symbolSelect.append(option);
    }
    symbolSelect.disabled = false;
    setInitialState(
      requestedMode === "stored" && !catalog.length
        ? "Choose exports and matching metadata files to begin."
        : "Select a symbol and timeframe.",
    );
    return true;
  } catch (error) {
    if (requestVersion !== selectionVersion || requestedMode !== dataMode.value)
      return false;
    setInitialState(
      `Unable to load available symbols: ${error instanceof Error ? error.message : String(error)}`,
    );
    return false;
  }
}

dataMode.addEventListener("change", () => {
  activeSelection = null;
  pendingPeriodSelection = null;
  pendingPeriodStates.clear();
  selectionVersion += 1;
  loading = false;
  bars = [];
  breakoutEvents = [];
  confirmedSwings = [];
  selectedEventId = null;
  storedValues = {};
  storedFeatures = [];
  datasetVersion.textContent =
    dataMode.value === "stored" ? "Stored data" : "Calculated data";
  valueInspector.hidden = dataMode.value !== "stored";
  storedImport.hidden = dataMode.value !== "stored";
  exportTab.disabled = dataMode.value === "stored";
  eventsTab.disabled = dataMode.value === "stored";
  eventsPanel.hidden = true;
  eventsTab.setAttribute("aria-selected", "false");
  exportPanel.hidden = true;
  exportTab.setAttribute("aria-selected", "false");
  timeframes.replaceChildren();
  symbolSelect.disabled = true;
  void loadCatalog();
});

/** @param {File} file */
function fileBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1]);
    reader.onerror = () =>
      reject(reader.error ?? new Error(`Cannot read ${file.name}`));
    reader.readAsDataURL(file);
  });
}

let importingStoredFiles = false;
storedFiles.addEventListener("change", async () => {
  if (importingStoredFiles) return;
  const selected = Array.from(storedFiles.files ?? []);
  const sidecars = new Map(
    selected
      .filter((file) => file.name.endsWith(".json"))
      .map((file) => [file.name, file]),
  );
  const tables = selected.filter((file) => /\.(csv|parquet)$/i.test(file.name));
  storedImportResults.replaceChildren();
  if (!tables.length) {
    storedImportStatus.textContent =
      "Choose at least one CSV or Parquet export with its matching .json sidecar.";
    return;
  }
  importingStoredFiles = true;
  storedFiles.disabled = true;
  try {
    storedImportStatus.textContent = `Importing ${tables.length} export${tables.length === 1 ? "" : "s"}…`;
    let importedCount = 0;
    for (const table of tables) {
      const result = document.createElement("li");
      const sidecar = sidecars.get(`${table.name}.json`);
      if (!sidecar) {
        result.textContent = `${table.name}: missing ${table.name}.json metadata. Select both files together.`;
      } else {
        try {
          const response = await fetch("/api/v1/stored/import", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              session_id: importSession,
              filename: table.name,
              content_base64: await fileBase64(table),
              metadata_json: await sidecar.text(),
            }),
          });
          if (!response.ok)
            throw new Error(
              (await response.json()).detail ??
                `Server error ${response.status}`,
            );
          const imported = await response.json();
          importedCount += 1;
          result.textContent = `${table.name}: imported ${imported.dataset_id} (${imported.rows} bars).`;
        } catch (error) {
          result.textContent = `${table.name}: ${error instanceof Error ? error.message : String(error)}`;
        }
      }
      storedImportResults.append(result);
    }
    storedImportStatus.textContent = importedCount
      ? "Import finished. Choose a symbol and timeframe below."
      : "No exports imported. Review the file errors above.";
    if (importedCount && dataMode.value === "stored") {
      activeSelection = null;
      selectionVersion += 1;
      loading = false;
      bars = [];
      nextBefore = null;
      hasMore = true;
      initialized = false;
      loadedBefores = [];
      storedValues = {};
      storedFeatures = [];
      inspectTime.replaceChildren();
      inspectValues.replaceChildren();
      datasetVersion.textContent = "Stored data";
      legend.textContent = "Select a symbol and timeframe.";
      symbolSelect.value = "";
      timeframes.replaceChildren();
      receiveIndicators([], false);
      redraw();
      void loadCatalog();
    }
  } finally {
    storedFiles.value = "";
    storedFiles.disabled = false;
    importingStoredFiles = false;
  }
});

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
  exportPanel.hidden = true;
  exportTab.setAttribute("aria-selected", "false");
  eventsPanel.hidden = true;
  eventsTab.setAttribute("aria-selected", "false");
});

eventsTab.addEventListener("click", () => {
  const opening = eventsPanel.hidden;
  eventsPanel.hidden = !opening;
  eventsTab.setAttribute("aria-selected", String(opening));
  indicatorsPanel.hidden = true;
  indicatorsTab.setAttribute("aria-selected", "false");
  exportPanel.hidden = true;
  exportTab.setAttribute("aria-selected", "false");
});

exportTab.addEventListener("click", () => {
  const opening = exportPanel.hidden;
  exportPanel.hidden = !opening;
  exportTab.setAttribute("aria-selected", String(opening));
  indicatorsPanel.hidden = true;
  indicatorsTab.setAttribute("aria-selected", "false");
  eventsPanel.hidden = true;
  eventsTab.setAttribute("aria-selected", "false");
});

/** @param {"csv"|"parquet"} format */
async function downloadEvents(format) {
  if (!activeSelection) {
    exportStatus.textContent = "Select a dataset first.";
    return;
  }
  const detectors = Array.from(
    exportEventDetectors.querySelectorAll("input:checked"),
    (input) => /** @type {HTMLInputElement} */ (input).value,
  );
  if (!detectors.length) {
    exportStatus.textContent = "Select at least one event detector.";
    return;
  }
  const features = Array.from(
    exportFeatures.querySelectorAll("input:checked"),
    (input) => /** @type {HTMLInputElement} */ (input).value,
  );
  exportStatus.textContent = "Preparing encountered events export…";
  try {
    const url = new URL("/api/v1/events/export", location.origin);
    url.searchParams.set("symbol", activeSelection.symbol);
    url.searchParams.set("timeframe", activeSelection.timeframe);
    url.searchParams.set("detectors", detectors.join(","));
    url.searchParams.set("features", features.join(","));
    url.searchParams.set("format", format);
    addPeriods(url);
    url.searchParams.set("swing_left", swingLeft.value);
    url.searchParams.set("swing_right", swingRight.value);
    url.searchParams.set("buffer", eventBuffer.value);
    const response = await fetch(url);
    if (!response.ok) throw new Error((await response.json()).detail);
    const address = URL.createObjectURL(await response.blob());
    const link = document.createElement("a");
    link.href = address;
    link.download = `encountered_events_${format}.zip`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(address), 1000);
    exportStatus.textContent = "Encountered events export downloaded.";
  } catch (error) {
    exportStatus.textContent = `Event export failed: ${error instanceof Error ? error.message : String(error)}`;
  }
}
exportEventsCsv.addEventListener("click", () => void downloadEvents("csv"));
exportEventsParquet.addEventListener(
  "click",
  () => void downloadEvents("parquet"),
);

/** @param {"csv"|"parquet"} format */
async function downloadFeatures(format) {
  if (dataMode.value === "stored") return;
  if (!activeSelection) {
    exportStatus.textContent = "Select a dataset first.";
    return;
  }
  const selected = Array.from(
    exportFeatures.querySelectorAll("input:checked"),
  ).map((checkbox) => /** @type {HTMLInputElement} */ (checkbox).value);
  if (!selected.length) {
    exportStatus.textContent = "Select at least one feature column.";
    return;
  }
  exportStatus.textContent = "Preparing export…";
  try {
    const url = new URL("/api/v1/export", location.origin);
    url.searchParams.set("symbol", activeSelection.symbol);
    url.searchParams.set("timeframe", activeSelection.timeframe);
    url.searchParams.set("features", selected.join(","));
    url.searchParams.set("format", format);
    addPeriods(url);
    const response = await fetch(url);
    if (!response.ok) throw new Error((await response.json()).detail);
    const address = URL.createObjectURL(await response.blob());
    const link = document.createElement("a");
    link.href = address;
    link.download = `bar_features_${format}.zip`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(address), 1000);
    exportStatus.textContent = "Export downloaded with metadata.";
  } catch (error) {
    exportStatus.textContent = `Export failed: ${error instanceof Error ? error.message : String(error)}`;
  }
}
exportCsv.addEventListener("click", () => void downloadFeatures("csv"));
exportParquet.addEventListener("click", () => void downloadFeatures("parquet"));

/** @param {Event} event */
function onSettingChange(event) {
  const changed = /** @type {HTMLInputElement} */ (event.currentTarget);
  if (!changed.reportValidity()) return;
  selectedPeriods.set(changed.dataset.period, changed.value);
  for (const input of settingInputs)
    if (input.dataset.period === changed.dataset.period)
      input.value = changed.value;
  const previous = activeSelection ?? pendingPeriodSelection;
  pendingPeriodSelection = previous;
  /** @type {Map<string, IndicatorState>} */
  const previousStates = new Map(pendingPeriodStates);
  for (const [identifier, state] of indicatorStates) {
    const definition = indicatorDefinitions.find(
      (entry) => entry.id === identifier,
    );
    previousStates.set(definition?.selection_key ?? identifier, { ...state });
  }
  pendingPeriodStates.clear();
  for (const [identifier, state] of previousStates)
    pendingPeriodStates.set(identifier, { ...state });
  for (const series of indicatorSeries.values()) chart.removeSeries(series);
  indicatorSeries.clear();
  for (const markers of indicatorMarkers.values()) markers.setMarkers([]);
  indicatorMarkers.clear();
  indicatorPoints.clear();
  indicatorStates.clear();
  activeSelection = null;
  const requestVersion = ++selectionVersion;
  loading = false;
  void loadCatalog(previousStates).then((loaded) => {
    if (!loaded || requestVersion !== selectionVersion) return;
    pendingPeriodSelection = null;
    pendingPeriodStates.clear();
    if (previous) {
      symbolSelect.value = previous.symbol;
      renderTimeframes(previous.symbol);
      selectTimeframe(previous.symbol, previous.timeframe);
    }
  });
}

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
        if (
          !definition.show_in_crosshair ||
          !indicatorStates.get(definition.id)?.applied
        )
          return [];
        const series = indicatorSeries.get(definition.id);
        const indicatorPoint = series && event.seriesData.get(series);
        const value =
          indicatorPoint &&
          typeof indicatorPoint === "object" &&
          "value" in indicatorPoint
            ? indicatorPoint.value
            : definition.values.find((detail) => detail.time === event.time)
                ?.value;
        return value === undefined ? [] : [`${definition.label} ${value}`];
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
