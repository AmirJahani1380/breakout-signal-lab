from __future__ import annotations

import csv
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import Page, sync_playwright

from tests.test_bars import write_csv, write_workbook


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
            (1_735_689_600 + index * 60, 1, 3, 0, 1.5 + (index % 20) / 20, 10)
            for index in range(1_200)
        ],
    )
    stored_root = tmp_path_factory.mktemp("stored-browser")
    fixture_root = Path(__file__).parent / "fixtures"
    for name in ("stored_export.csv", "stored_export.csv.json"):
        shutil.copyfile(fixture_root / name, stored_root / name)
    large_export = stored_root / "history.csv"
    with large_export.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "asset",
                "timeframe",
                "time",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "ema_20",
                "atr_20",
                "research_score",
            ]
        )
        for index in range(1002):
            writer.writerow(
                [
                    "XAUUSDzero",
                    "H1",
                    1_500_000_000 + index * 3600,
                    1,
                    3,
                    0,
                    2,
                    5,
                    1000 + index,
                    3,
                    "" if index == 0 else index,
                ]
            )
    metadata = json.loads((fixture_root / "stored_export.csv.json").read_text())
    metadata.update(dataset_id="XAUUSDzero/H1", timeframe="H1")
    large_export.with_name(large_export.name + ".json").write_text(json.dumps(metadata))
    write_workbook(
        root / "EURUSD_M15_max_bars.xlsx",
        [
            (1_735_689_600, 2, 3, 0, 1, 5),
            (1_735_690_500, 1.5, 4, 1, 3.5, 5),
            (1_735_691_400, 3, 4, 2, 3.2, 5),
        ],
    )
    pivot_closes = [6, 8, 10, 12, 10, 8, 6, 4, 6, 8, 6]
    write_csv(
        root / "BREAKOUT_H1.csv",
        [
            (1_735_700_000 + index * 60, opening, high, low, close, 1)
            for index, (opening, high, low, close) in enumerate(
                [
                    (8, 9, 7, 8),
                    (10, 12, 9, 10),
                    (8, 10, 7, 8),
                    (8, 14, 8, 13),
                    (11, 12, 7, 10),
                    (11, 13, 9, 11),
                    (11, 12, 6, 6),
                ]
            )
        ],
    )
    write_csv(
        root / "PIVOT_H1.csv",
        [
            (1_735_689_600 + index * 60, close, close + 1, close - 1, close, 1)
            for index, close in enumerate(pivot_closes)
        ],
    )
    write_csv(
        root / "CANDLES_H1.csv",
        [
            (1_735_689_600 + index * 60, opening, closing + 1, opening - 1, closing, 1)
            for index, (opening, closing) in enumerate(
                [(1, 2), (2, 3), (3, 4), (4, 4), (4, 5), (5, 6), (6, 7)]
            )
        ],
    )
    port = free_port()
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port)],
        env={**os.environ, "BARS_DATA_ROOT": str(root), "STORED_DATA_ROOT": str(stored_root)},
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
