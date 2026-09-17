from __future__ import annotations

import base64
import json
from pathlib import Path
from uuid import uuid4

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.main import Settings, create_app
from app.stored_data import StoredDataError, load_stored_dataset

FIXTURES = Path(__file__).parent / "fixtures"


def test_stored_pages_use_exported_values_without_calculating(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden_calculation(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("stored mode calculated a feature")

    monkeypatch.setattr("app.main.calculate_requested", forbidden_calculation)
    with TestClient(create_app(Settings(tmp_path, stored_data_root=FIXTURES))) as client:
        catalog = client.get("/api/v1/catalog", params={"mode": "stored"})
        assert catalog.json() == {"symbols": [{"symbol": "XAUUSDzero", "timeframes": ["D1"]}]}
        selected = {"mode": "stored", "symbol": "XAUUSDzero", "timeframe": "D1", "limit": 2}
        newest = client.get("/api/v1/bars", params=selected)
        assert newest.status_code == 200, newest.text
        payload = newest.json()
        assert [bar["time"] for bar in payload["bars"]] == [1306281600, 1306368000]
        assert payload["bars"][0]["close"] == 1525.7
        assert payload["stored_values"]["research_score"] == [
            {"time": 1306281600, "value": 0.0},
            {"time": 1306368000, "value": 4.25},
        ]
        assert payload["stored_metadata"]["features"][2]["version"] == "7"
        assert "research_score" not in {view["id"] for view in payload["indicators"]}
        assert [
            point["value"]
            for view in payload["indicators"]
            if view["id"] == "ema_20"
            for point in view["points"]
        ] == [1505.6017645079428, 1506.8777869357577]
        older = client.get(
            "/api/v1/bars", params={**selected, "before": payload["next_before"], "features": ""}
        )
        assert older.status_code == 200
        assert older.json()["stored_values"]["research_score"] == [
            {"time": 1306195200, "value": None}
        ]
        toggled = client.get("/api/v1/bars", params={**selected, "features": "ema_20"})
        assert toggled.json()["stored_values"] == payload["stored_values"]


def test_stored_metadata_and_alignment_errors(tmp_path: Path) -> None:
    source = FIXTURES / "stored_export.csv"
    target = tmp_path / source.name
    target.write_bytes(source.read_bytes())
    with pytest.raises(StoredDataError, match="missing metadata sidecar"):
        load_stored_dataset(target)
    metadata = json.loads((FIXTURES / "stored_export.csv.json").read_text())
    metadata["dataset_id"] = "wrong/D1"
    target.with_name(target.name + ".json").write_text(json.dumps(metadata))
    with pytest.raises(StoredDataError, match="dataset_id must match"):
        load_stored_dataset(target)
    metadata["dataset_id"] = "XAUUSDzero/D1"
    target.with_name(target.name + ".json").write_text(json.dumps(metadata))
    target.write_text(source.read_text().replace("1306281600", "1306195200"))
    with pytest.raises(StoredDataError, match="strictly increasing"):
        load_stored_dataset(target)


def test_parquet_export_matches_csv_values(tmp_path: Path) -> None:
    csv_path = FIXTURES / "stored_export.csv"
    parquet_path = tmp_path / "stored_export.parquet"
    frame = pd.read_csv(csv_path, float_precision="round_trip")
    frame.to_parquet(parquet_path, index=False)
    parquet_path.with_name(parquet_path.name + ".json").write_bytes(
        (FIXTURES / "stored_export.csv.json").read_bytes()
    )
    csv = load_stored_dataset(csv_path)
    parquet = load_stored_dataset(parquet_path)
    assert parquet.bars.bars == csv.bars.bars
    assert parquet.page(None, 3, ())["stored_values"] == csv.page(None, 3, ())["stored_values"]


def test_parquet_rejects_feature_types_incompatible_with_sidecar(tmp_path: Path) -> None:
    source = FIXTURES / "stored_export.csv"
    frame = pd.read_csv(source, float_precision="round_trip")
    metadata = json.loads((FIXTURES / "stored_export.csv.json").read_text())
    parquet_path = tmp_path / "stored_export.parquet"
    sidecar = parquet_path.with_name(parquet_path.name + ".json")

    frame["research_score"] = pd.Series([True, False, True], dtype="boolean")
    frame.to_parquet(parquet_path, index=False)
    sidecar.write_text(json.dumps(metadata))
    with pytest.raises(StoredDataError, match="research_score Parquet type .*metadata Float64"):
        load_stored_dataset(parquet_path)

    frame["research_score"] = pd.Series([1.0, 2.0, 3.0], dtype="float64")
    frame.to_parquet(parquet_path, index=False)
    metadata["features"][-1]["dtype"] = "Int64"
    metadata["dtypes"]["research_score"] = "Int64"
    sidecar.write_text(json.dumps(metadata))
    with pytest.raises(StoredDataError, match="research_score Parquet type .*metadata Int64"):
        load_stored_dataset(parquet_path)

    frame["research_score"] = pd.Series([1, 2, 3], dtype="int32")
    frame.to_parquet(parquet_path, index=False)
    with pytest.raises(StoredDataError, match="research_score Parquet type int32.*metadata Int64"):
        load_stored_dataset(parquet_path)

    frame["research_score"] = pd.Series([1, 2, 3], dtype="uint64")
    frame.to_parquet(parquet_path, index=False)
    with pytest.raises(StoredDataError, match="research_score Parquet type uint64.*metadata Int64"):
        load_stored_dataset(parquet_path)

    metadata["features"][-1]["dtype"] = "Float64"
    metadata["dtypes"]["research_score"] = "Float64"
    sidecar.write_text(json.dumps(metadata))
    frame["research_score"] = pd.Series([1.0, 2.0, 3.0], dtype="float32")
    frame.to_parquet(parquet_path, index=False)
    with pytest.raises(
        StoredDataError, match="research_score Parquet type float32.*metadata Float64"
    ):
        load_stored_dataset(parquet_path)


def test_parquet_accepts_nullable_64_bit_feature_types(tmp_path: Path) -> None:
    frame = pd.read_csv(FIXTURES / "stored_export.csv", float_precision="round_trip")
    frame["ema_20"] = frame["ema_20"].astype("Float64")
    frame["atr_20"] = frame["atr_20"].astype("Float64")
    frame["research_score"] = pd.Series([1, pd.NA, 3], dtype="Int64")
    path = tmp_path / "stored_export.parquet"
    frame.to_parquet(path, index=False)
    metadata = json.loads((FIXTURES / "stored_export.csv.json").read_text())
    metadata["features"][-1]["dtype"] = "Int64"
    metadata["dtypes"]["research_score"] = "Int64"
    path.with_name(path.name + ".json").write_text(json.dumps(metadata))
    values = load_stored_dataset(path).page(None, 3, ())["stored_values"]
    assert [entry["value"] for entry in values["research_score"]] == [1, None, 3]


def test_large_integer_feature_is_exact_in_inspector_payload(tmp_path: Path) -> None:
    source = FIXTURES / "stored_export.csv"
    target = tmp_path / source.name
    lines = source.read_text().splitlines()
    lines[0] += ",trade_identifier"
    lines[1] += ",9007199254740993"
    lines[2] += ","
    lines[3] += ",9007199254740995"
    target.write_text("\n".join(lines) + "\n")
    metadata = json.loads((FIXTURES / "stored_export.csv.json").read_text())
    metadata["features"].append({"name": "trade_identifier", "dtype": "Int64", "version": "2"})
    metadata["dtypes"]["trade_identifier"] = "Int64"
    target.with_name(target.name + ".json").write_text(json.dumps(metadata))
    values = load_stored_dataset(target).page(None, 3, ())["stored_values"]
    assert values["trade_identifier"] == [
        {"time": 1306195200, "value": "9007199254740993"},
        {"time": 1306281600, "value": None},
        {"time": 1306368000, "value": "9007199254740995"},
    ]
    target.write_text(target.read_text().replace("9007199254740993", "3.5"))
    with pytest.raises(StoredDataError, match="expected integer or null"):
        load_stored_dataset(target)


def test_unrelated_invalid_export_does_not_block_selection(tmp_path: Path) -> None:
    source = FIXTURES / "stored_export.csv"
    selected = tmp_path / source.name
    selected.write_bytes(source.read_bytes())
    selected.with_name(selected.name + ".json").write_bytes(
        (FIXTURES / "stored_export.csv.json").read_bytes()
    )
    unrelated = tmp_path / "aaa_bad.csv"
    unrelated.write_text("bad export")
    unrelated_metadata = {"asset": "OTHER", "timeframe": "H1"}
    unrelated.with_name(unrelated.name + ".json").write_text(json.dumps(unrelated_metadata))
    with TestClient(create_app(Settings(tmp_path, stored_data_root=tmp_path))) as client:
        response = client.get(
            "/api/v1/bars",
            params={"mode": "stored", "symbol": "XAUUSDzero", "timeframe": "D1"},
        )
    assert response.status_code == 200, response.text
    assert len(response.json()["bars"]) == 3


def test_browser_import_groups_metadata_identity_and_isolates_invalid_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.main.calculate_requested",
        lambda *_args, **_kwargs: pytest.fail("stored import calculated features"),
    )
    source = (FIXTURES / "stored_export.csv").read_text()
    metadata = json.loads((FIXTURES / "stored_export.csv.json").read_text())
    session = str(uuid4())

    def import_export(
        client: TestClient, filename: str, asset: str, timeframe: str, malformed: bool = False
    ) -> object:
        export_text = source.replace("XAUUSDzero", asset).replace(",D1,", f",{timeframe},")
        sidecar = {
            **metadata,
            "asset": asset,
            "timeframe": timeframe,
            "dataset_id": f"{asset}/{timeframe}",
        }
        return client.post(
            "/api/v1/stored/import",
            json={
                "session_id": session,
                "filename": filename,
                "content_base64": base64.b64encode(export_text.encode()).decode(),
                "metadata_json": "{}" if malformed else json.dumps(sidecar),
            },
        )

    with TestClient(create_app(Settings(tmp_path))) as client:
        assert client.get(
            "/api/v1/catalog", params={"mode": "stored", "import_session": session}
        ).json() == {"symbols": []}
        assert import_export(client, "first.csv", "XAUUSDzero", "D1").status_code == 200
        assert import_export(client, "second.csv", "XAUUSDzero", "M5").status_code == 200
        assert import_export(client, "third.csv", "BRENT", "H1").status_code == 200
        bad = import_export(client, "bad.csv", "BAD", "D1", malformed=True)
        assert bad.status_code == 422
        assert "missing metadata" in bad.json()["detail"]
        catalog = client.get(
            "/api/v1/catalog", params={"mode": "stored", "import_session": session}
        )
        assert catalog.json() == {
            "symbols": [
                {"symbol": "BRENT", "timeframes": ["H1"]},
                {"symbol": "XAUUSDzero", "timeframes": ["D1", "M5"]},
            ]
        }
        selected = client.get(
            "/api/v1/bars",
            params={
                "mode": "stored",
                "import_session": session,
                "symbol": "XAUUSDzero",
                "timeframe": "M5",
            },
        )
        assert selected.status_code == 200
        assert selected.json()["stored_metadata"]["dataset_id"] == "XAUUSDzero/M5"
        assert selected.json()["stored_values"]["ema_20"][0]["value"] == 1503.4861607719367
