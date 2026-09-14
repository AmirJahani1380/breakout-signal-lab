from pathlib import Path

from fastapi.testclient import TestClient

from app.main import Settings, create_app
from tests.test_bars import valid, write_workbook


def client_for(tmp_path: Path, rows: list[tuple[object, ...]]) -> TestClient:
    return TestClient(create_app(Settings(write_workbook(tmp_path / "bars.xlsx", rows))))


def test_api_pages_newest_to_oldest_while_each_page_ascends(tmp_path: Path) -> None:
    rows = [valid(100 + index) for index in range(6)]
    with client_for(tmp_path, rows) as client:
        first = client.get("/api/v1/bars?limit=2").json()
        second = client.get(f"/api/v1/bars?limit=2&before={first['next_before']}").json()
        third = client.get(f"/api/v1/bars?limit=2&before={second['next_before']}").json()
    assert [[bar["time"] for bar in page["bars"]] for page in (first, second, third)] == [
        [104, 105],
        [102, 103],
        [100, 101],
    ]
    assert third["has_more"] is False and third["next_before"] is None


def test_api_validation_empty_and_source_errors(tmp_path: Path) -> None:
    with client_for(tmp_path, []) as client:
        assert client.get("/api/v1/bars").json() == {
            "bars": [],
            "next_before": None,
            "has_more": False,
        }
        assert client.get("/api/v1/bars?limit=0").status_code == 422
        assert client.get("/api/v1/bars?limit=5001").status_code == 422
    with TestClient(create_app(Settings(tmp_path / "gone.xlsx"))) as client:
        response = client.get("/api/v1/bars")
    assert response.status_code == 503 and "gone.xlsx" in response.json()["detail"]
