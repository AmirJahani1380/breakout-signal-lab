// The indicator renderer currently emits number inputs for every setting.
// Render the two 0/1 absolute settings as checkboxes while retaining its change handler.
const indicators = document.getElementById("indicators-panel");
const absoluteSettings = new Set(["ema_distance_absolute", "ema_slope_absolute"]);

function renderAbsoluteCheckboxes() {
  for (const input of indicators.querySelectorAll("input[data-period]")) {
    if (!absoluteSettings.has(input.dataset.period) || input.type === "checkbox") continue;
    const enabled = input.value === "1";
    input.type = "checkbox";
    input.checked = enabled;
    input.value = enabled ? "1" : "0";
    input.removeAttribute("min");
    input.removeAttribute("max");
    input.removeAttribute("step");
  }
}

indicators.addEventListener("change", (event) => {
  const input = event.target;
  if (input instanceof HTMLInputElement && absoluteSettings.has(input.dataset.period)) {
    input.value = input.checked ? "1" : "0";
  }
}, true);
new MutationObserver(renderAbsoluteCheckboxes).observe(indicators, { childList: true, subtree: true });
renderAbsoluteCheckboxes();
