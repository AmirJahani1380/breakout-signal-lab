from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import Page, sync_playwright

from tests.test_bars import write_workbook


def free_port() -> int:
    with socket.socket() as socket_handle:
        socket_handle.bind(("127.0.0.1", 0))
        return int(socket_handle.getsockname()[1])


@pytest.fixture(scope="module")
def viewer_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    root = tmp_path_factory.mktemp("browser") / "market"
    root.mkdir()
    write_workbook(
        root / "EURUSD_H1_max_bars.xlsx",
        [
            (
                1_735_689_600 + index * 60,
                1,
                3,
                0,
                1.5 + (index % 20) / 20,
                10,
            )
            for index in range(1_200)
        ],
    )
    write_workbook(
        root / "EURUSD_M15_max_bars.xlsx",
        [
            (1_735_689_600, 2, 3, 0, 1, 5),
            (1_735_690_500, 1.5, 4, 1, 3.5, 5),
            (1_735_691_400, 3, 4, 2, 3.2, 5),
        ],
    )
    port = free_port()
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port)],
        env={**os.environ, "BARS_DATA_ROOT": str(root)},
    )
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(50):
            try:
                if httpx.get(f"{url}/api/v1/catalog", timeout=0.2).is_success:
                    break
            except httpx.HTTPError:
                time.sleep(0.1)
        else:
            raise RuntimeError("FastAPI server did not start")
        yield url
    finally:
        process.terminate()
        process.wait(timeout=5)


@pytest.fixture
def page(tmp_path: Path) -> Iterator[Page]:
    asset = Path("node_modules/lightweight-charts/dist/lightweight-charts.standalone.production.js")
    if not asset.is_file():
        pytest.skip("run npm ci before browser smoke tests")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        browser_page = browser.new_page(viewport={"width": 1200, "height": 800})
        browser_page.route(
            "**/lightweight-charts@5.2.0/**", lambda route: route.fulfill(path=asset)
        )
        yield browser_page
        browser.close()


def test_symbol_then_timeframe_loads_only_the_selected_chart(page: Page, viewer_url: str) -> None:
    page.goto(viewer_url)
    page.locator("#symbol").select_option("EURUSD")
    assert page.get_by_role("button", name="H1").is_visible()
    assert page.get_by_role("button", name="M15").is_visible()
    page.get_by_role("button", name="H1").click()
    page.locator("#chart canvas").first.wait_for()
    page.wait_for_function("document.querySelector('#chart').dataset.barCount === '1000'")
    page.get_by_role("button", name="M15").click()
    page.wait_for_function("document.querySelector('#chart').dataset.barCount === '3'")
    assert page.get_by_role("button", name="M15").get_attribute("aria-pressed") == "true"


def select_timeframe(page: Page, viewer_url: str, timeframe: str = "H1") -> None:
    page.goto(viewer_url)
    page.locator("#symbol").select_option("EURUSD")
    page.get_by_role("button", name=timeframe).click()


def test_chart_renders_legend_resizes_and_prepends_history(page: Page, viewer_url: str) -> None:
    select_timeframe(page, viewer_url)
    page.locator("#chart canvas").first.wait_for()
    page.wait_for_function("document.querySelector('#chart').dataset.barCount === '1000'")
    canvases = page.locator("#chart canvas")
    assert canvases.count() >= 4
    assert page.evaluate(
        """() => [...document.querySelectorAll('#chart canvas')].some((canvas) => {
            const pixels = canvas.getContext('2d')
                .getImageData(0, 0, canvas.width, canvas.height).data;
            let candle = false; let volume = false;
            for (let index = 0; index < pixels.length; index += 4) {
                const y = Math.floor(index / 4 / canvas.width);
                candle ||= pixels[index] === 38 && pixels[index + 1] === 166
                    && pixels[index + 2] === 154 && y < canvas.height * 0.7;
                volume ||= pixels[index] === 7 && pixels[index + 1] === 134
                    && pixels[index + 2] === 92 && y > canvas.height * 0.7;
            }
            return candle && volume;
        })"""
    )
    page.mouse.move(600, 400)
    page.wait_for_function("document.querySelector('#legend').textContent.includes(' O ')")
    before_width = page.locator("#chart").bounding_box()["width"]
    before_canvas_width = canvases.first.get_attribute("width")
    page.set_viewport_size({"width": 900, "height": 700})
    assert page.locator("#chart").bounding_box()["width"] < before_width
    page.wait_for_function(
        "width => document.querySelector('#chart canvas').getAttribute('width') !== width",
        arg=before_canvas_width,
    )
    page.evaluate("window.__breakoutChart.timeScale().setVisibleLogicalRange({from: 20, to: 70})")
    page.wait_for_function("document.querySelector('#chart').dataset.barCount === '1200'")
    chart_range = page.evaluate("window.__breakoutChart.timeScale().getVisibleLogicalRange()")
    assert chart_range["from"] == pytest.approx(220)
    assert chart_range["to"] == pytest.approx(270)


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"bars": [], "next_before": None, "has_more": False}, "No bars in this source."),
        (None, "Unable to load bars: unavailable"),
    ],
)
def test_selected_timeframe_empty_and_error_replace_chart(
    page: Page, viewer_url: str, payload: dict[str, object] | None, expected: str
) -> None:
    def respond(route: object) -> None:
        if payload is None:
            route.fulfill(
                status=503, content_type="application/json", body='{"detail":"unavailable"}'
            )
        else:
            route.fulfill(content_type="application/json", json=payload)

    page.route("**/api/v1/bars**", respond)
    select_timeframe(page, viewer_url)
    page.get_by_text(expected, exact=True).wait_for()
    assert page.locator("#chart").is_hidden()


def test_stale_selection_response_does_not_replace_new_chart(page: Page, viewer_url: str) -> None:
    page.add_init_script(
        """const originalFetch = window.fetch;
        window.__barResponses = [];
        window.fetch = (input, init) => {
            if (!String(input).includes('/api/v1/bars')) return originalFetch(input, init);
            return new Promise((resolve) => window.__barResponses.push((payload) => {
                resolve(new Response(JSON.stringify(payload), {
                    headers: {'Content-Type': 'application/json'}
                }));
            }));
        };"""
    )
    page.goto(viewer_url)
    page.locator("#symbol").select_option("EURUSD")
    page.get_by_role("button", name="H1").click()
    page.wait_for_function("window.__barResponses.length === 1")
    page.get_by_text("Loading chart…", exact=True).wait_for()
    assert page.locator("#chart").is_hidden()
    page.get_by_role("button", name="H1").click()
    assert page.evaluate("window.__barResponses.length") == 1
    page.get_by_role("button", name="M15").click()
    page.wait_for_function("window.__barResponses.length === 2")
    page.evaluate(
        "payload => window.__barResponses[1](payload)",
        {
            "bars": [
                {
                    "time": 2,
                    "open": 1,
                    "high": 3,
                    "low": 0,
                    "close": 2,
                    "volume": 5,
                }
            ],
            "next_before": None,
            "has_more": False,
        },
    )
    page.wait_for_function("document.querySelector('#chart').dataset.barCount === '1'")
    page.evaluate(
        "payload => window.__barResponses[0](payload)",
        {
            "bars": [
                {
                    "time": 1,
                    "open": 1,
                    "high": 3,
                    "low": 0,
                    "close": 2,
                    "volume": 5,
                },
                {
                    "time": 2,
                    "open": 1,
                    "high": 3,
                    "low": 0,
                    "close": 2,
                    "volume": 5,
                },
            ],
            "next_before": None,
            "has_more": False,
        },
    )
    assert page.locator("#chart").get_attribute("data-bar-count") == "1"
    assert page.get_by_role("button", name="M15").get_attribute("aria-pressed") == "true"


def test_pagination_error_keeps_the_rendered_chart(page: Page, viewer_url: str) -> None:
    bars = [
        {
            "time": 1_735_689_600 + index * 60,
            "open": 1,
            "high": 3,
            "low": 0,
            "close": 2,
            "volume": 5,
        }
        for index in range(120)
    ]
    requests = 0

    def respond(route: object) -> None:
        nonlocal requests
        requests += 1
        if requests == 1:
            route.fulfill(json={"bars": bars, "next_before": bars[0]["time"], "has_more": True})
        else:
            route.fulfill(
                status=503,
                content_type="application/json",
                body='{"detail":"later unavailable"}',
            )

    page.route("**/api/v1/bars**", respond)
    select_timeframe(page, viewer_url)
    page.wait_for_function("document.querySelector('#chart').dataset.barCount === '120'")
    page.evaluate("window.__breakoutChart.timeScale().setVisibleLogicalRange({from: 0, to: 20})")
    page.get_by_text("Unable to load bars: later unavailable", exact=True).wait_for()
    assert not page.locator("#chart").is_hidden()
    assert page.locator("#chart").get_attribute("data-bar-count") == "120"


def test_indicators_tab_applies_hides_and_places_series(page: Page, viewer_url: str) -> None:
    select_timeframe(page, viewer_url)
    page.wait_for_function("document.querySelector('#chart').dataset.barCount === '1000'")
    page.get_by_role("tab", name="Indicators").click()
    panel = page.get_by_role("tabpanel", name="Indicators")
    assert panel.is_visible()

    volume = panel.locator('[data-indicator="volume"]')
    assert volume.locator("[data-indicator-state]").text_content() == "Applied · Visible"
    volume.get_by_role("button", name="Hide").click()
    assert volume.locator("[data-indicator-state]").text_content() == "Applied · Hidden"
    volume.locator("[data-apply]").uncheck()
    assert volume.locator("[data-indicator-state]").text_content() == "Not applied"
    point_counts = json.loads(
        page.locator("#chart").get_attribute("data-indicator-point-counts") or "{}"
    )
    assert point_counts["volume"] == 0

    ema = panel.locator('[data-indicator="ema_20"]')
    ema.locator("[data-apply]").check()
    assert ema.locator("[data-indicator-state]").text_content() == "Applied · Visible"
    page.wait_for_function(
        "JSON.parse(document.querySelector('#chart').dataset.indicatorPointCounts).ema_20 === 1000"
    )
    point_counts = json.loads(
        page.locator("#chart").get_attribute("data-indicator-point-counts") or "{}"
    )
    assert point_counts["ema_20"] == 1000

    rsi = panel.locator('[data-indicator="rsi_14"]')
    rsi.locator("[data-apply]").check()
    assert rsi.locator("[data-indicator-state]").text_content() == "Applied · Visible"
    page.wait_for_function(
        "JSON.parse(document.querySelector('#chart').dataset.indicatorPointCounts).rsi_14 === 1000"
    )
    assert page.evaluate("window.__breakoutChart.panes().length") == 2
    page.wait_for_function("window.__breakoutChart.panes()[1].getHeight() > 0")
    assert page.evaluate("window.__breakoutChart.panes()[1].getHeight()") == 160
    point_counts = json.loads(
        page.locator("#chart").get_attribute("data-indicator-point-counts") or "{}"
    )
    assert point_counts["rsi_14"] == 1000
    page.wait_for_function(
        """() => [[255, 152, 0], [171, 71, 188]].every((color) =>
            [...document.querySelectorAll('#chart canvas')].some((canvas) => {
                const pixels = canvas.getContext('2d').getImageData(
                    0, 0, canvas.width, canvas.height).data;
                for (let index = 0; index < pixels.length; index += 4) {
                    if (pixels[index] === color[0] && pixels[index + 1] === color[1]
                        && pixels[index + 2] === color[2]) return true;
                }
                return false;
            }))"""
    )
    rsi.locator("[data-apply]").uncheck()
    assert page.evaluate("window.__breakoutChart.panes().length") == 1


def test_export_tab_preselects_features_and_downloads_csv(page: Page, viewer_url: str) -> None:
    select_timeframe(page, viewer_url)
    page.wait_for_function("document.querySelector('#chart').dataset.barCount === '1000'")
    page.get_by_role("tab", name="Export").click()
    panel = page.get_by_role("tabpanel", name="Export")
    assert panel.is_visible()
    checkboxes = panel.locator("input[type=checkbox]")
    assert checkboxes.count() > 0
    assert checkboxes.evaluate_all("inputs => inputs.every(input => input.checked)")
    with page.expect_download() as downloaded:
        panel.get_by_role("button", name="Download CSV").click()
    assert downloaded.value.suggested_filename == "bar_features_csv.zip"


def test_feature_labels_and_engulfing_candle_color_toggle(page: Page, viewer_url: str) -> None:
    select_timeframe(page, viewer_url, "M15")
    page.wait_for_function("document.querySelector('#chart').dataset.barCount === '3'")
    page.get_by_role("tab", name="Indicators").click()
    panel = page.get_by_role("tabpanel", name="Indicators")
    assert panel.locator('[data-indicator="ema_20"] small').text_content().startswith("line ·")
    assert panel.locator('[data-indicator="rsi_14"] small').text_content().startswith("pane ·")
    assert (
        panel.locator('[data-indicator="candle_range"] small')
        .text_content()
        .startswith("crosshair ·")
    )
    engulfing = panel.locator('[data-indicator="is_engulfing"]')
    assert engulfing.locator("small").text_content().startswith("candle color ·")
    engulfing.locator("[data-apply]").check()
    page.wait_for_function("document.querySelector('#chart').dataset.engulfingCount === '1'")
    page.wait_for_function(
        """() => [...document.querySelectorAll('#chart canvas')].some((canvas) => {
            const pixels = canvas.getContext('2d')
                .getImageData(0, 0, canvas.width, canvas.height).data;
            for (let index = 0; index < pixels.length; index += 4)
                if (pixels[index] === 255 && pixels[index + 1] === 214
                    && pixels[index + 2] === 0) return true;
            return false;
        })"""
    )
    engulfing.locator("[data-apply]").uncheck()
    assert page.locator("#chart").get_attribute("data-engulfing-count") == "0"

    candle_range = panel.locator('[data-indicator="candle_range"]')
    main_series_count = page.evaluate("window.__breakoutChart.panes()[0].getSeries().length")
    candle_range.locator("[data-apply]").check()
    page.wait_for_function(
        """() => window.__breakoutChart.panes()[0].getSeries().length === 2
            && document.querySelector('[data-indicator="candle_range"] [data-indicator-state]')
                .textContent.includes('Applied')"""
    )
    assert main_series_count == 2
    page.evaluate(
        """() => window.__breakoutChart.setCrosshairPosition(
            3.5, 1735690500, window.__breakoutChart.panes()[0].getSeries()[0])"""
    )
    page.wait_for_function("document.querySelector('#legend').textContent.includes('Range 3')")
    candle_range.locator("[data-apply]").uncheck()
    assert "Range" not in page.locator("#legend").text_content()


def test_server_metadata_adds_an_unknown_indicator_without_frontend_changes(
    page: Page, viewer_url: str
) -> None:
    console_errors: list[str] = []
    page.on(
        "console",
        lambda message: console_errors.append(message.text) if message.type == "error" else None,
    )
    bars = [
        {
            "time": 1_735_689_600 + index * 900,
            "open": 1,
            "high": 3,
            "low": 0,
            "close": 2,
            "volume": 5,
        }
        for index in range(3)
    ]
    definition = {
        "id": "custom_signal",
        "label": "Custom Signal",
        "description": "Server-defined test pane",
        "series_type": "LineSeries",
        "pane": "separate",
        "default_applied": False,
        "default_visible": True,
        "series_options": {"color": "#00ffff", "lineWidth": 2},
        "price_scale_options": {},
        "pane_height": 120,
        "scale_range": [0, 10],
        "reference_lines": [],
        "points": [{"time": bar["time"], "value": index + 2} for index, bar in enumerate(bars)],
    }
    invalid_definition = {
        **definition,
        "id": "broken_signal",
        "label": "Broken Signal",
        "series_type": "MissingSeries",
        "default_applied": True,
    }
    page.route(
        "**/api/v1/bars**",
        lambda route: route.fulfill(
            json={
                "bars": bars,
                "indicators": [invalid_definition, definition],
                "next_before": None,
                "has_more": False,
            }
        ),
    )
    select_timeframe(page, viewer_url, "M15")
    page.wait_for_function("document.querySelector('#chart').dataset.barCount === '3'")
    page.get_by_role("tab", name="Indicators").click()
    panel = page.get_by_role("tabpanel", name="Indicators")
    assert (
        panel.locator('[data-indicator="broken_signal"] [data-indicator-state]').text_content()
        == "Not applied"
    )
    assert any("Unable to create indicator broken_signal" in message for message in console_errors)
    custom = panel.locator('[data-indicator="custom_signal"]')
    assert custom.get_by_text("Server-defined test pane").is_visible()
    custom.locator("[data-apply]").check()
    page.wait_for_function("window.__breakoutChart.panes()[1]?.getHeight() > 0")
    assert page.evaluate("window.__breakoutChart.panes()[1].getHeight()") == 120
    page.wait_for_function(
        """() => [...document.querySelectorAll('#chart canvas')].some((canvas) => {
            const pixels = canvas.getContext('2d').getImageData(
                0, 0, canvas.width, canvas.height).data;
            for (let index = 0; index < pixels.length; index += 4)
                if (pixels[index] === 0 && pixels[index + 1] === 255
                    && pixels[index + 2] === 255) return true;
            return false;
        })"""
    )


def test_marker_renderer_draws_markers_without_adding_a_line_series(
    page: Page, viewer_url: str
) -> None:
    times = [1_735_689_600 + index * 900 for index in range(3)]
    bars = [
        {"time": time, "open": 1, "high": 3, "low": 0, "close": 2, "volume": 5} for time in times
    ]
    marker = {
        "id": "entry_marker",
        "feature_name": "entry_price",
        "label": "Entry",
        "description": "Entry marker",
        "renderer": "marker",
        "series_type": None,
        "pane": "main",
        "show_in_crosshair": True,
        "default_applied": True,
        "default_visible": True,
        "series_options": {
            "color": "#00ffff",
            "shape": "square",
            "position": "atPriceMiddle",
            "size": 2,
        },
        "price_scale_options": {},
        "pane_height": None,
        "scale_range": None,
        "reference_lines": [],
        "points": [{"time": time, "value": 2} for time in times],
        "values": [{"time": time, "value": 2} for time in times],
    }
    page.route(
        "**/api/v1/bars**",
        lambda route: route.fulfill(
            json={
                "bars": bars,
                "indicators": [marker],
                "next_before": None,
                "has_more": False,
            }
        ),
    )
    select_timeframe(page, viewer_url, "M15")
    page.wait_for_function("document.querySelector('#chart').dataset.markerCount === '3'")
    page.locator("#chart canvas").first.wait_for()
    assert page.evaluate("window.__breakoutChart.panes()[0].getSeries().length") == 1
    page.wait_for_function(
        """() => [...document.querySelectorAll('#chart canvas')].some((canvas) => {
            if (!canvas.width || !canvas.height) return false;
            const pixels = canvas.getContext('2d').getImageData(
                0, 0, canvas.width, canvas.height).data;
            for (let index = 0; index < pixels.length; index += 4)
                if (pixels[index] === 0 && pixels[index + 1] === 255
                    && pixels[index + 2] === 255) return true;
            return false;
        })"""
    )
