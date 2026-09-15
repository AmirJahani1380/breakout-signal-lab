from __future__ import annotations

import csv
from bisect import bisect_left
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from math import isfinite
from pathlib import Path
from typing import Any, NoReturn
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from openpyxl import load_workbook

REQUIRED_PRICE_COLUMNS = ("time", "open", "high", "low", "close")
SUPPORTED_SOURCE_SUFFIXES = frozenset({".csv", ".xlsx"})
EXPORT_NAME_SUFFIXES = ("_max_bars", "-max-bars")


class SourceValidationError(ValueError):
    """A source workbook cannot safely be used."""


@dataclass(frozen=True, slots=True)
class Bar:
    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float

    def as_dict(self) -> dict[str, int | float]:
        return {
            "time": self.time,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }


class BarStore:
    def __init__(self, bars: Iterable[Bar]) -> None:
        self.bars = tuple(bars)
        self.times = tuple(bar.time for bar in self.bars)

    def page(self, before: int | None, limit: int) -> tuple[tuple[Bar, ...], bool]:
        end = len(self.bars) if before is None else bisect_left(self.times, before)
        start = max(0, end - limit)
        return self.bars[start:end], start > 0


@dataclass(frozen=True, slots=True)
class DatasetSelection:
    symbol: str
    timeframe: str
    source_path: Path


class SourceCatalog:
    """A filename-only index; bar files are opened only by ``load_bars``."""

    def __init__(self, selections: Iterable[DatasetSelection]) -> None:
        self._selections = {(entry.symbol, entry.timeframe): entry for entry in selections}

    def symbols(self) -> list[dict[str, object]]:
        timeframes_by_symbol: dict[str, list[str]] = {}
        for symbol, timeframe in self._selections:
            timeframes_by_symbol.setdefault(symbol, []).append(timeframe)
        return [
            {"symbol": symbol, "timeframes": sorted(timeframes)}
            for symbol, timeframes in sorted(timeframes_by_symbol.items())
        ]

    def selection(self, symbol: str, timeframe: str) -> DatasetSelection:
        selection = self._selections.get((symbol, timeframe))
        if selection is None:
            raise SourceValidationError(f"{symbol}/{timeframe}: dataset is not available")
        return selection


def discover_source_catalog(data_root: Path) -> SourceCatalog:
    if not data_root.is_dir():
        raise SourceValidationError(f"{data_root}: data root does not exist or is not a directory")
    try:
        files = [path for path in data_root.rglob("*") if path.is_file()]
    except OSError as error:
        raise SourceValidationError(f"{data_root}: cannot enumerate data root: {error}") from error
    selections = [
        _selection_from_filename(path)
        for path in files
        if path.suffix.lower() in SUPPORTED_SOURCE_SUFFIXES
    ]
    catalog = SourceCatalog(selection for selection in selections if selection is not None)
    if not catalog.symbols():
        raise SourceValidationError(f"{data_root}: no CSV or XLSX symbol_timeframe files found")
    if len(catalog._selections) != len(
        [selection for selection in selections if selection is not None]
    ):
        raise SourceValidationError(f"{data_root}: duplicate symbol/timeframe filenames found")
    return catalog


def _selection_from_filename(source_path: Path) -> DatasetSelection | None:
    stem = source_path.stem
    for suffix in EXPORT_NAME_SUFFIXES:
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    for separator in ("_", "-"):
        if separator in stem:
            symbol, timeframe = stem.rsplit(separator, 1)
            if symbol and timeframe:
                return DatasetSelection(symbol, timeframe, source_path)
    return None


def load_bars(source_path: Path, source_timezone: str = "UTC") -> BarStore:
    timezone_info = _source_timezone(source_path, source_timezone)
    if not source_path.is_file():
        raise SourceValidationError(f"{source_path.name}: source file does not exist")
    if source_path.suffix.lower() == ".csv":
        return BarStore(_load_csv(source_path, timezone_info))
    return BarStore(_load_workbook(source_path, timezone_info))


def _load_csv(source_path: Path, source_timezone: ZoneInfo) -> tuple[Bar, ...]:
    try:
        with source_path.open(newline="", encoding="utf-8-sig") as source_file:
            return _parse_rows(source_path, csv.reader(source_file), source_timezone)
    except (OSError, UnicodeError, csv.Error) as error:
        raise SourceValidationError(f"{source_path.name}: cannot read CSV file: {error}") from error


def _load_workbook(source_path: Path, source_timezone: ZoneInfo) -> tuple[Bar, ...]:
    try:
        workbook = load_workbook(source_path, read_only=True, data_only=True)
    except Exception as error:
        raise SourceValidationError(
            f"{source_path.name}: cannot read XLSX workbook: {error}"
        ) from error
    try:
        try:
            worksheet = workbook.active
            bars = _parse_rows(source_path, worksheet.iter_rows(values_only=True), source_timezone)
        except SourceValidationError:
            raise
        except Exception as error:
            raise SourceValidationError(
                f"{source_path.name}: cannot read XLSX worksheet: {error}"
            ) from error
    finally:
        workbook.close()
    return bars


def _parse_rows(
    source_path: Path, rows: Iterable[Sequence[Any]], source_timezone: ZoneInfo
) -> tuple[Bar, ...]:
    row_iterator = iter(rows)
    header = next(row_iterator, None)
    columns = _columns(source_path, header)
    bars = tuple(
        _parse_bar(source_path, row_number, row, columns, source_timezone)
        for row_number, row in enumerate(row_iterator, start=2)
    )
    _validate_order(source_path, bars)
    return bars


def _source_timezone(source_path: Path, source_timezone: str) -> ZoneInfo:
    try:
        return ZoneInfo(source_timezone)
    except (ValueError, ZoneInfoNotFoundError) as error:
        raise SourceValidationError(
            f"{source_path.name}: source timezone {source_timezone!r} is not a valid IANA timezone"
        ) from error


def _columns(source_path: Path, header: Sequence[Any] | None) -> dict[str, int]:
    names = [str(value).strip() if value is not None else "" for value in (header or ())]
    missing = [name for name in REQUIRED_PRICE_COLUMNS if name not in names]
    if missing:
        raise SourceValidationError(
            f"{source_path.name}, spreadsheet row 1, column header: missing {missing}"
        )
    volume_name = (
        "volume" if "volume" in names else "tick_volume" if "tick_volume" in names else None
    )
    if volume_name is None:
        raise SourceValidationError(
            f"{source_path.name}, spreadsheet row 1, column header: missing volume or tick_volume"
        )
    return {name: names.index(name) for name in (*REQUIRED_PRICE_COLUMNS, volume_name)}


def _parse_bar(
    source_path: Path,
    row_number: int,
    row: Sequence[Any],
    columns: dict[str, int],
    source_timezone: ZoneInfo,
) -> Bar:
    timestamp = _timestamp(source_path, row_number, _cell(row, columns["time"]), source_timezone)
    values = {
        name: _number(source_path, row_number, name, _cell(row, column))
        for name, column in columns.items()
        if name != "time"
    }
    volume = values.pop("volume", values.pop("tick_volume", 0.0))
    if volume < 0:
        _error(source_path, row_number, "volume", volume, "must not be negative")
    if values["high"] < max(values["open"], values["close"]):
        _error(source_path, row_number, "high", values["high"], "must be at least open and close")
    if values["low"] > min(values["open"], values["close"]):
        _error(source_path, row_number, "low", values["low"], "must be at most open and close")
    if values["low"] > values["high"]:
        _error(source_path, row_number, "low", values["low"], "must not exceed high")
    return Bar(timestamp, values["open"], values["high"], values["low"], values["close"], volume)


def _cell(row: Sequence[Any], index: int) -> Any:
    return row[index] if index < len(row) else None


def _timestamp(source_path: Path, row: int, value: Any, source_timezone: ZoneInfo) -> int:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.lstrip("+-").isdigit():
        return _unix_timestamp(source_path, row, value, int(value))
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            _error(source_path, row, "time", value, "is not an ISO-8601 timestamp")
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        if not isfinite(float(value)) or int(value) != value:
            _error(
                source_path,
                row,
                "time",
                value,
                "must be an integer Unix seconds or milliseconds value",
            )
        return _unix_timestamp(source_path, row, value, int(value))
    else:
        _error(source_path, row, "time", value, "is not a timestamp")
    try:
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=source_timezone)
        utc_value = parsed.astimezone(UTC)
        if utc_value.microsecond:
            _error(source_path, row, "time", value, "loses sub-second precision")
        return int(utc_value.timestamp())
    except SourceValidationError:
        raise
    except (OverflowError, OSError, ValueError) as error:
        _error(source_path, row, "time", value, f"cannot convert to UTC Unix seconds: {error}")


def _unix_timestamp(source_path: Path, row: int, value: Any, raw: int) -> int:
    if abs(raw) >= 100_000_000_000 and raw % 1000:
        _error(source_path, row, "time", value, "loses sub-second precision")
    return raw // 1000 if abs(raw) >= 100_000_000_000 else raw


def _number(source_path: Path, row: int, column: str, value: Any) -> float:
    if value is None or isinstance(value, bool):
        _error(source_path, row, column, value, "must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError):
        _error(source_path, row, column, value, "must be a finite number")
    if not isfinite(number):
        _error(source_path, row, column, value, "must be a finite number")
    return number


def _validate_order(source_path: Path, bars: tuple[Bar, ...]) -> None:
    for row_number, (previous, current) in enumerate(zip(bars, bars[1:]), start=3):
        if current.time == previous.time:
            _error(
                source_path, row_number, "time", current.time, "duplicates the previous timestamp"
            )
        if current.time < previous.time:
            _error(
                source_path,
                row_number,
                "time",
                current.time,
                "is earlier than the previous timestamp",
            )


def _error(source_path: Path, row: int, column: str, value: Any, reason: str) -> NoReturn:
    raise SourceValidationError(
        f"{source_path.name}, spreadsheet row {row}, column {column}, value {value!r}: {reason}"
    )
