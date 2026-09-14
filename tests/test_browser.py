from __future__ import annotations

import json
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
    source = write_workbook(
        tmp_path_factory.mktemp("browser") / "bars.xlsx",
        [valid(1_735_689_600 + index * 60) for index in range(1_200)],
    )
    port = free_port()
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port)],
        env={**__import__("os").environ, "BARS_XLSX_PATH": str(source)},
    )
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(50):
            try:
                if httpx.get(f"{url}/api/v1/bars", timeout=0.2).is_success:
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
def page(viewer_url: str, tmp_path: Path) -> Page:
    asset = Path("node_modules/lightweight-charts/dist/lightweight-charts.standalone.production.js")
    if not asset.is_file():
        pytest.skip("run npm ci before browser smoke tests")
    workspace_debug_log = Path("debug.log")
    workspace_debug_log.unlink(missing_ok=True)
    with sync_playwright() as playwright:
        browser_log = tmp_path / "chromium.log"
        browser = playwright.chromium.launch(
            args=[f"--log-file={browser_log}", "--enable-logging=stderr"],
            env={**os.environ, "CHROME_LOG_FILE": str(browser_log)},
        )
        browser_page = browser.new_page(viewport={"width": 1200, "height": 800})
        browser_page.route(
            "**/lightweight-charts@5.2.0/**", lambda route: route.fulfill(path=asset)
        )
        yield browser_page
        browser.close()
    workspace_debug_log.unlink(missing_ok=True)


def test_chart_renders_legends_resizes_and_prepends_history(page: Page, viewer_url: str) -> None:
    page.goto(viewer_url)
    page.locator("#chart canvas").first.wait_for()
    page.wait_for_function("document.querySelector('#chart').dataset.barCount === '1000'")
    canvases = page.locator("#chart canvas")
    assert canvases.count() >= 4  # pane and price-scale canvases show both chart regions.
    assert page.evaluate(
        """() => [...document.querySelectorAll('#chart canvas')].some((canvas) => {
            const context = canvas.getContext('2d');
            const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
            let candle = false; let volume = false;
            for (let index = 0; index < pixels.length; index += 4) {
                if (pixels[index] === 38 && pixels[index + 1] === 166
                    && pixels[index + 2] === 154) {
                    const y = Math.floor(index / 4 / canvas.width);
                    candle ||= y < canvas.height * 0.7;
                }
                if (pixels[index] === 7 && pixels[index + 1] === 134
                    && pixels[index + 2] === 92) {
                    const y = Math.floor(index / 4 / canvas.width);
                    volume ||= y > canvas.height * 0.7;
                }
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
    assert canvases.first.get_attribute("width") != before_canvas_width
    page.evaluate("window.__breakoutChart.timeScale().setVisibleLogicalRange({from: 20, to: 70})")
    page.wait_for_function("document.querySelector('#chart').dataset.barCount === '1200'")
    after_range = page.evaluate("window.__breakoutChart.timeScale().getVisibleLogicalRange()")
    assert after_range["from"] == pytest.approx(20 + 200)
    assert after_range["to"] == pytest.approx(70 + 200)


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"bars": [], "next_before": None, "has_more": False}, "No bars in this source."),
        (None, "Unable to load bars: unavailable"),
    ],
)
def test_initial_empty_and_error_replace_chart(
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
    page.goto(viewer_url)
    page.locator("#state").get_by_text(expected).wait_for()
    assert page.locator("#chart").is_hidden()


def test_delayed_initial_load_replaces_chart_until_response(page: Page, viewer_url: str) -> None:
    payload = {
        "bars": [{"time": 1_735_689_600, "open": 1, "high": 3, "low": 0, "close": 2, "volume": 5}],
        "next_before": None,
        "has_more": False,
    }

    page.add_init_script(
        f"""window.fetch = () => new Promise((resolve) => {{
            window.__resolveBars = () => resolve(new Response({json.dumps(json.dumps(payload))}, {{
                status: 200, headers: {{'Content-Type': 'application/json'}}
            }}));
        }});"""
    )
    page.goto(viewer_url, wait_until="domcontentloaded")
    page.wait_for_function("document.querySelector('#chart').hidden")
    page.locator("#state").get_by_text("Loading chart…").wait_for()
    assert page.locator("#chart").is_hidden()
    page.evaluate("window.__resolveBars()")
    page.locator("#chart canvas").first.wait_for()
    assert not page.locator("#chart").is_hidden()


def test_pagination_error_keeps_loaded_chart(page: Page, viewer_url: str) -> None:
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
                status=503, content_type="application/json", body='{"detail":"later unavailable"}'
            )

    page.route("**/api/v1/bars**", respond)
    page.goto(viewer_url)
    page.wait_for_function("document.querySelector('#chart').dataset.barCount === '120'")
    page.evaluate("window.__breakoutChart.timeScale().setVisibleLogicalRange({from: 0, to: 20})")
    page.locator("#state").get_by_text("Unable to load bars: later unavailable").wait_for()
    assert not page.locator("#chart").is_hidden()
    assert page.locator("#chart").get_attribute("data-bar-count") == "120"
