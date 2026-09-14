from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import bars as bars_module
from app.main import Settings, create_app
from tests.test_bars import valid, write_csv, write_workbook


def market_root(tmp_path: Path, rows: list[tuple[object, ...]]) -> Path:
    root = tmp_path / "market"
    root.mkdir()
    write_workbook(root / "EURUSD_H1_max_bars.xlsx", rows)
    return root


def test_catalog_lists_filenames_without_loading_bars(
    tmp_path: Path,
) -> None:
    root = tmp_path / "market"
    root.mkdir()
    malformed_source = write_csv(root / "EURUSD_H1_max_bars.csv", [("not-a-time",)], ("bad",))
    with TestClient(create_app(Settings(root))) as client:
        response = client.get("/api/v1/catalog")
    assert response.json() == {"symbols": [{"symbol": "EURUSD", "timeframes": ["H1"]}]}
    with pytest.raises(bars_module.SourceValidationError):
        bars_module.load_bars(malformed_source)


def test_selected_dataset_pages_without_loading_another_dataset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = market_root(tmp_path, [valid(100 + index) for index in range(6)])
    write_workbook(root / "GBPUSD_H1.xlsx", [valid(1_000)])
    loaded_paths: list[Path] = []

    def track_load(source_path: Path, source_timezone: str) -> object:
        loaded_paths.append(source_path)
        return bars_module.load_bars(source_path, source_timezone)

    monkeypatch.setattr("app.main.load_bars", track_load)
    with TestClient(create_app(Settings(root))) as client:
        first = client.get("/api/v1/bars?symbol=EURUSD&timeframe=H1&limit=2").json()
        second = client.get(
            f"/api/v1/bars?symbol=EURUSD&timeframe=H1&limit=2&before={first['next_before']}"
        ).json()
    assert [[bar["time"] for bar in page["bars"]] for page in (first, second)] == [
        [104, 105],
        [102, 103],
    ]
    assert loaded_paths == [
        root / "EURUSD_H1_max_bars.xlsx",
        root / "EURUSD_H1_max_bars.xlsx",
    ]


def test_missing_root_invalid_selection_and_malformed_selection_are_actionable(
    tmp_path: Path,
) -> None:
    with TestClient(create_app(Settings(tmp_path / "gone"))) as client:
        assert client.get("/api/v1/catalog").status_code == 503
    root = market_root(tmp_path, [])
    with TestClient(create_app(Settings(root))) as client:
        response = client.get("/api/v1/bars?symbol=EURUSD&timeframe=M5")
    assert response.status_code == 422 and "not available" in response.json()["detail"]

    write_workbook(root / "EURUSD_M5.xlsx", [valid(1)], ("time", "open"))
    with TestClient(create_app(Settings(root))) as client:
        response = client.get("/api/v1/bars?symbol=EURUSD&timeframe=M5")
    assert response.status_code == 422 and "missing" in response.json()["detail"]
