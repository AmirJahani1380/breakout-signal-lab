from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import Page, sync_playwright

from tests.test_bars import valid, write_workbook


def free_port() -> int:
    with socket.socket() as socket_handle:
        socket_handle.bind(("127.0.0.1", 0))
        return int(socket_handle.getsockname()[1])


@pytest.fixture(scope="module")
def viewer_url(tmp_path_factory: pytest.TempPathFactory) -> str:
    root = tmp_path_factory.mktemp("browser") / "market"
    root.mkdir()
    write_workbook(
        root / "EURUSD_H1_max_bars.xlsx",
        [valid(1_735_689_600 + index * 60) for index in range(1_200)],
    )
    write_workbook(
        root / "EURUSD_M15_max_bars.xlsx",
        [valid(1_735_689_600 + index * 900) for index in range(3)],
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
def page(tmp_path: Path) -> Page:
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
            "bars": [{"time": 2, "open": 1, "high": 3, "low": 0, "close": 2, "volume": 5}],
            "next_before": None,
            "has_more": False,
        },
    )
    page.wait_for_function("document.querySelector('#chart').dataset.barCount === '1'")
    page.evaluate(
        "payload => window.__barResponses[0](payload)",
        {
            "bars": [
                {"time": 1, "open": 1, "high": 3, "low": 0, "close": 2, "volume": 5},
                {"time": 2, "open": 1, "high": 3, "low": 0, "close": 2, "volume": 5},
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
