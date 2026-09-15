import csv
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from openpyxl import Workbook

from app import bars as bars_module
from app.bars import BarPage, PagedBarReader, SourceValidationError, load_bars


def write_workbook(
    path: Path,
    rows: list[tuple[object, ...]],
    headers: tuple[str, ...] = ("time", "open", "high", "low", "close", "volume"),
) -> Path:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    workbook.save(path)
    return path


def write_csv(
    path: Path,
    rows: list[tuple[object, ...]],
    headers: tuple[str, ...] = ("time", "open", "high", "low", "close", "volume"),
) -> Path:
    with path.open("w", newline="", encoding="utf-8") as source_file:
        writer = csv.writer(source_file)
        writer.writerow(headers)
        writer.writerows(rows)
    return path


def valid(time: object = "2025-01-01T00:00:00Z") -> tuple[object, ...]:
    return (time, 1, 3, 0, 2, 10)


def test_loads_naive_aware_unix_and_tick_volume(tmp_path: Path) -> None:
    source = write_workbook(
        tmp_path / "bars.xlsx",
        [
            valid("2025-01-01T00:00:00"),
            valid("2025-01-01T00:01:00+00:00"),
            valid(1735689720),
            (1735689780000, 1, 3, 0, 2, 11),
        ],
        ("time", "open", "high", "low", "close", "tick_volume"),
    )
    store = load_bars(source, "UTC")
    assert [bar.time for bar in store.bars] == [1735689600, 1735689660, 1735689720, 1735689780]
    assert store.bars[-1].volume == 11


def test_loads_csv_with_the_same_bar_contract(tmp_path: Path) -> None:
    source = write_csv(tmp_path / "bars.csv", [valid(1735689600), valid(1735689660)])
    assert [bar.time for bar in load_bars(source).bars] == [1735689600, 1735689660]


def test_volume_column_wins_over_tick_volume(tmp_path: Path) -> None:
    source = write_workbook(
        tmp_path / "bars.xlsx",
        [("2025-01-01T00:00:00Z", 1, 3, 0, 2, 10, 99)],
        ("time", "open", "high", "low", "close", "volume", "tick_volume"),
    )
    assert load_bars(source).bars[0].volume == 10


def test_naive_time_uses_configured_timezone(tmp_path: Path) -> None:
    store = load_bars(
        write_workbook(tmp_path / "bars.xlsx", [valid("2025-01-01T00:00:00")]),
        "America/New_York",
    )
    assert store.bars[0].time == 1735707600


def test_nonzero_aware_offset_is_preserved(tmp_path: Path) -> None:
    source = write_workbook(tmp_path / "bars.xlsx", [valid("2025-01-01T03:30:00+03:30")])
    assert load_bars(source).bars[0].time == 1735689600


def test_extreme_aware_timestamp_has_cell_context(tmp_path: Path) -> None:
    source = write_workbook(tmp_path / "extreme.xlsx", [valid("9999-12-31T23:59:59-23:59")])
    with pytest.raises(
        SourceValidationError,
        match=r"extreme.xlsx, spreadsheet row 2, column time, value '9999-12-31T23:59:59-23:59'",
    ):
        load_bars(source)


@pytest.mark.parametrize("bad_time", ["nope", "2025-01-01T00:00:00.1Z", 1735689600123])
def test_rejects_bad_timestamps(tmp_path: Path, bad_time: object) -> None:
    with pytest.raises(SourceValidationError, match="time"):
        load_bars(write_workbook(tmp_path / "bars.xlsx", [valid(bad_time)]))


def test_subsecond_timestamp_keeps_direct_validation_message(tmp_path: Path) -> None:
    with pytest.raises(SourceValidationError, match="loses sub-second precision") as error:
        load_bars(write_workbook(tmp_path / "bars.xlsx", [valid("2025-01-01T00:00:00.1Z")]))
    assert "cannot convert" not in str(error.value)


@pytest.mark.parametrize(
    "column,value", [("open", None), ("close", "x"), ("high", float("inf")), ("volume", -1)]
)
def test_rejects_invalid_numbers(tmp_path: Path, column: str, value: object) -> None:
    row = list(valid())
    row[("time", "open", "high", "low", "close", "volume").index(column)] = value
    with pytest.raises(SourceValidationError, match=column):
        load_bars(write_workbook(tmp_path / "bars.xlsx", [tuple(row)]))


@pytest.mark.parametrize(
    "row",
    [
        ("2025-01-01T00:00:00Z", 3, 2, 0, 2, 1),
        ("2025-01-01T00:00:00Z", 1, 3, 2.5, 2, 1),
        ("2025-01-01T00:00:00Z", 1, 1, 2, 1, 1),
    ],
)
def test_rejects_bad_ohlc_geometry(tmp_path: Path, row: tuple[object, ...]) -> None:
    with pytest.raises(SourceValidationError):
        load_bars(write_workbook(tmp_path / "bars.xlsx", [row]))


def test_source_contract_and_order_errors(tmp_path: Path) -> None:
    with pytest.raises(SourceValidationError, match="does not exist"):
        load_bars(tmp_path / "missing.xlsx")
    with pytest.raises(SourceValidationError, match="missing"):
        load_bars(write_workbook(tmp_path / "headers.xlsx", [valid()], ("time", "open")))
    with pytest.raises(SourceValidationError, match="timezone"):
        load_bars(write_workbook(tmp_path / "zone.xlsx", [valid()]), "No/Such_Zone")
    with pytest.raises(SourceValidationError, match="timezone"):
        load_bars(write_workbook(tmp_path / "bad-zone.xlsx", [valid()]), "bad\\x00zone")
    with pytest.raises(SourceValidationError, match="duplicates"):
        load_bars(write_workbook(tmp_path / "duplicate.xlsx", [valid(), valid()]))
    with pytest.raises(SourceValidationError, match="earlier"):
        load_bars(write_workbook(tmp_path / "order.xlsx", [valid(2), valid(1)]))


def test_empty_source_is_valid(tmp_path: Path) -> None:
    assert load_bars(write_workbook(tmp_path / "empty.xlsx", [])).bars == ()


def test_lazy_worksheet_failure_has_source_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = write_workbook(tmp_path / "broken.xlsx", [valid()])

    class BrokenSheet:
        def iter_rows(self, values_only: bool) -> object:
            del values_only
            raise OSError("bad worksheet XML")

    class BrokenBook:
        active = BrokenSheet()

        def close(self) -> None:
            pass

    monkeypatch.setattr("app.bars.load_workbook", lambda *args, **kwargs: BrokenBook())
    with pytest.raises(SourceValidationError, match="broken.xlsx: cannot read XLSX worksheet"):
        load_bars(source)


def test_parquet_latest_and_older_pages_project_only_requested_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "EURUSD_H1.parquet"
    frame = pd.DataFrame(
        [valid(100 + index) for index in range(2_500)],
        columns=("time", "open", "high", "low", "close", "volume"),
    )
    frame.to_parquet(source, index=False, row_group_size=250)
    projected_rows: list[int] = []
    reader = PagedBarReader(source)
    original_read = reader._read_parquet_rows

    def track_read(start: int, count: int) -> list[tuple[Any, ...]]:
        rows = original_read(start, count)
        projected_rows.append(len(rows))
        return rows

    monkeypatch.setattr(reader, "_read_parquet_rows", track_read)
    latest = reader.page(None, 1_000, 100)
    older = reader.page(latest.display_bars[0].time, 1_000, 100)

    assert [bar.time for bar in latest.display_bars] == list(range(1_600, 2_600))
    assert [bar.time for bar in older.display_bars] == list(range(600, 1_600))
    assert projected_rows == [1_100, 1_100]


def test_single_large_parquet_row_group_projects_only_requested_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "EURUSD_H1.parquet"
    pd.DataFrame(
        [valid(100 + index) for index in range(5_000)],
        columns=("time", "open", "high", "low", "close", "volume"),
    ).to_parquet(source, index=False, row_group_size=5_000)
    projected_rows: list[int] = []
    reader = PagedBarReader(source)
    original_read = reader._read_parquet_rows

    def track_read(start: int, count: int) -> list[tuple[Any, ...]]:
        rows = original_read(start, count)
        projected_rows.append(len(rows))
        return rows

    monkeypatch.setattr(reader, "_read_parquet_rows", track_read)
    page = reader.page(None, 1_000, 100)

    assert len(page.bars) == 1_100
    assert len(page.display_bars) == 1_000
    assert projected_rows == [1_100]


def test_parquet_page_store_is_reused_and_rebuilt_after_source_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "EURUSD_H1.parquet"
    columns = ("time", "open", "high", "low", "close", "volume")
    pd.DataFrame([valid(100 + index) for index in range(20)], columns=columns).to_parquet(
        source, index=False
    )
    decoded_batches = 0
    original_batches = bars_module.pq.ParquetFile.iter_batches

    def track_batches(*args: object, **kwargs: object) -> Any:
        nonlocal decoded_batches
        for batch in original_batches(*args, **kwargs):
            decoded_batches += 1
            yield batch

    monkeypatch.setattr(bars_module.pq.ParquetFile, "iter_batches", track_batches)
    first_reader = PagedBarReader(source)
    first_cache = first_reader._parquet_cache_path
    first_decoded_batches = decoded_batches
    second_reader = PagedBarReader(source)

    assert second_reader._parquet_cache_path == first_cache
    assert decoded_batches == first_decoded_batches

    assert first_cache is not None
    connection = sqlite3.connect(first_cache)
    connection.execute("PRAGMA user_version = 0")
    connection.commit()
    connection.close()
    validated_reader = PagedBarReader(source)
    assert validated_reader._parquet_cache_path == first_cache
    assert decoded_batches > first_decoded_batches
    rebuilt_batch_count = decoded_batches

    pd.DataFrame([valid(200 + index) for index in range(25)], columns=columns).to_parquet(
        source, index=False
    )
    rebuilt_reader = PagedBarReader(source)

    assert rebuilt_reader._parquet_cache_path != first_cache
    assert not first_cache.exists()
    assert decoded_batches > rebuilt_batch_count
    assert [bar.time for bar in rebuilt_reader.page(None, 1_000, 0).display_bars] == list(
        range(200, 225)
    )


def test_concurrent_parquet_page_store_preparation_is_race_safe(tmp_path: Path) -> None:
    source = tmp_path / "EURUSD_H1.parquet"
    pd.DataFrame(
        [valid(100 + index) for index in range(2_000)],
        columns=("time", "open", "high", "low", "close", "volume"),
    ).to_parquet(source, index=False, row_group_size=2_000)

    with ThreadPoolExecutor(max_workers=4) as executor:
        readers = list(executor.map(lambda _: PagedBarReader(source), range(4)))

    assert len({reader._parquet_cache_path for reader in readers}) == 1
    assert all(len(reader.page(None, 1_000, 0).display_bars) == 1_000 for reader in readers)


@pytest.mark.parametrize("time_kind", ["iso", "datetime", "milliseconds"])
def test_parquet_cursor_uses_normalized_timestamp_semantics(tmp_path: Path, time_kind: str) -> None:
    base = 1_735_689_600
    if time_kind == "iso":
        times: list[object] = [
            datetime.fromtimestamp(base + index, UTC).isoformat() for index in range(20)
        ]
    elif time_kind == "datetime":
        times = [datetime.fromtimestamp(base + index, UTC) for index in range(20)]
    else:
        times = [(base + index) * 1_000 for index in range(20)]
    source = tmp_path / f"EURUSD_{time_kind}.parquet"
    pd.DataFrame(
        [(time, 1, 3, 0, 2, 10) for time in times],
        columns=("time", "open", "high", "low", "close", "volume"),
    ).to_parquet(source, index=False, row_group_size=5)

    page = PagedBarReader(source).page(base + 10, 5, 2)

    assert [bar.time for bar in page.display_bars] == list(range(base + 5, base + 10))


def test_arbitrary_xlsx_cursor_uses_index_and_bounded_bar_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = write_workbook(
        tmp_path / "EURUSD_H1.xlsx", [valid(100 + index) for index in range(1_500)]
    )
    parsed_window_sizes: list[int] = []
    original_page_from_rows = PagedBarReader._page_from_rows

    def track_window(
        reader: PagedBarReader,
        rows: list[tuple[object, ...]],
        end: int,
        display_limit: int,
        warm_up: int,
    ) -> BarPage:
        parsed_window_sizes.append(len(rows) - 1)
        return original_page_from_rows(reader, rows, end, display_limit, warm_up)

    monkeypatch.setattr(PagedBarReader, "_page_from_rows", track_window)
    page = PagedBarReader(source).page(1_000, 100, 10)

    assert [bar.time for bar in page.display_bars] == list(range(900, 1_000))
    assert parsed_window_sizes == [110]


@pytest.mark.parametrize("bad_kind", ["duplicate", "out_of_order"])
def test_timestamp_index_rejects_errors_outside_page_and_across_boundary(
    tmp_path: Path, bad_kind: str
) -> None:
    rows = [valid(100 + index) for index in range(1_200)]
    boundary = 200
    bad_time = rows[boundary - 1][0] if bad_kind == "duplicate" else 1
    rows[boundary] = valid(bad_time)
    source = write_csv(tmp_path / "EURUSD_H1.csv", rows)

    with pytest.raises(SourceValidationError, match="duplicates|earlier"):
        PagedBarReader(source).page(None, 1_000, 0)


def test_csv_index_tracks_quoted_multiline_logical_records(tmp_path: Path) -> None:
    source = tmp_path / "EURUSD_H1.csv"
    with source.open("w", newline="", encoding="utf-8") as source_file:
        writer = csv.writer(source_file)
        writer.writerow(("time", "open", "high", "low", "close", "volume", "note"))
        writer.writerow((100, 1, 3, 0, 2, 10, "first line\nsecond line"))
        writer.writerow((101, 1, 3, 0, 2, 10, "ordinary"))

    page = PagedBarReader(source).page(None, 1_000, 0)

    assert [bar.time for bar in page.display_bars] == [100, 101]


def test_deep_page_validation_reports_actual_source_row(tmp_path: Path) -> None:
    rows = [valid(100 + index) for index in range(1_200)]
    rows[1_100] = (1_200, 5, 4, 0, 2, 10)
    source = write_csv(tmp_path / "EURUSD_H1.csv", rows)

    with pytest.raises(SourceValidationError, match="spreadsheet row 1102"):
        PagedBarReader(source).page(None, 100, 0)
