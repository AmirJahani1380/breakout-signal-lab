from __future__ import annotations

import csv
from bisect import bisect_left
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import chain
from math import isfinite
from pathlib import Path
from typing import Any, NoReturn
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd

REQUIRED_PRICE_COLUMNS = ("time", "open", "high", "low", "close")
SUPPORTED_SOURCE_SUFFIXES = frozenset({".csv", ".parquet", ".xlsx"})
EXPORT_NAME_SUFFIXES = ("_max_bars", "-max-bars")


class SourceValidationError(ValueError):
    """A market-data source cannot safely be used."""


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
    def __init__(
        self,
        bars: Iterable[Bar],
        imported_features: pd.DataFrame | None = None,
        dataset_id: str = "",
    ) -> None:
        self.bars = tuple(bars)
        self.times = tuple(bar.time for bar in self.bars)
        self.dataset_id = dataset_id
        self.imported_features = _validated_imported_features(
            imported_features, self.times, dataset_id
        )

    @property
    def canonical_frame(self) -> pd.DataFrame:
        return pd.DataFrame(
            [bar.as_dict() for bar in self.bars],
            columns=("time", "open", "high", "low", "close", "volume"),
        ).set_index("time")

    def page(self, before: int | None, display_limit: int, warm_up: int = 0) -> BarPage:
        end = len(self.bars) if before is None else bisect_left(self.times, before)
        display_start = max(0, end - display_limit)
        calculation_start = max(0, display_start - warm_up)
        return BarPage(
            self.bars[calculation_start:end],
            display_start - calculation_start,
            display_start > 0,
        )


@dataclass(frozen=True, slots=True)
class BarPage:
    bars: tuple[Bar, ...]
    display_start: int
    has_more: bool

    @property
    def display_bars(self) -> tuple[Bar, ...]:
        return self.bars[self.display_start :]


@dataclass(frozen=True, slots=True)
class DatasetSelection:
    symbol: str
    timeframe: str
    source_path: Path


class SourceCatalog:
    """A filename-only index; sources are opened only after selection."""

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
        selection
        for path in files
        if path.suffix.lower() in SUPPORTED_SOURCE_SUFFIXES
        if (selection := _selection_from_filename(path)) is not None
    ]
    catalog = SourceCatalog(selections)
    if not catalog.symbols():
        raise SourceValidationError(
            f"{data_root}: no CSV, Parquet, or XLSX symbol_timeframe files found"
        )
    if len(catalog._selections) != len(selections):
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


def load_bars(
    source_path: Path, source_timezone: str = "UTC", dataset_id: str | None = None
) -> BarStore:
    timezone_info = _source_timezone(source_path, source_timezone)
    if not source_path.is_file():
        raise SourceValidationError(f"{source_path.name}: source file does not exist")
    readers: dict[str, tuple[str, Callable[[Path], pd.DataFrame]]] = {
        ".csv": ("CSV", pd.read_csv),
        ".parquet": ("Parquet", pd.read_parquet),
        ".xlsx": ("XLSX", pd.read_excel),
    }
    suffix = source_path.suffix.lower()
    source_reader = readers.get(suffix)
    if source_reader is None:
        raise SourceValidationError(f"{source_path.name}: unsupported source format")
    if suffix == ".csv":
        _validate_csv_shape(source_path)
    format_name, reader = source_reader
    frame = _read_tabular(source_path, format_name, reader)
    return _store_from_frame(source_path, frame, timezone_info, dataset_id)


def _read_tabular(
    source_path: Path, format_name: str, reader: Callable[[Path], pd.DataFrame]
) -> pd.DataFrame:
    try:
        frame = reader(source_path)
    except Exception as error:
        raise SourceValidationError(
            f"{source_path.name}: cannot read {format_name} file: {error}"
        ) from error
    if not isinstance(frame, pd.DataFrame):
        raise SourceValidationError(f"{source_path.name}: {format_name} reader returned no table")
    return frame


def _validate_csv_shape(source_path: Path) -> None:
    """Keep pandas from silently accepting duplicate headers or ragged records."""
    try:
        with source_path.open(newline="", encoding="utf-8-sig") as source_file:
            rows = csv.reader(source_file)
            header = next(rows, [])
            if len(header) != len(set(header)):
                raise SourceValidationError(
                    f"{source_path.name}: duplicate column names are not allowed"
                )
            for row_number, row in enumerate(rows, start=2):
                if len(row) != len(header):
                    raise SourceValidationError(
                        f"{source_path.name}, CSV row {row_number}: expected "
                        f"{len(header)} fields, found {len(row)}"
                    )
    except SourceValidationError:
        raise
    except (OSError, UnicodeError, csv.Error) as error:
        raise SourceValidationError(f"{source_path.name}: cannot read CSV file: {error}") from error


def _store_from_frame(
    source_path: Path,
    frame: pd.DataFrame,
    source_timezone: ZoneInfo,
    dataset_id: str | None,
) -> BarStore:
    if not frame.columns.is_unique:
        raise SourceValidationError(f"{source_path.name}: duplicate column names are not allowed")
    names = [str(column).strip() for column in frame.columns]
    if len(names) != len(set(names)):
        raise SourceValidationError(f"{source_path.name}: duplicate column names are not allowed")
    normalized_frame = frame.copy()
    normalized_frame.columns = pd.Index(names)
    bars = _parse_rows(
        source_path,
        chain((tuple(names),), normalized_frame.itertuples(index=False, name=None)),
        source_timezone,
    )
    imported_names = [
        column
        for column in names
        if column not in {*REQUIRED_PRICE_COLUMNS, "volume", "tick_volume"}
    ]
    imported = normalized_frame.loc[:, imported_names].copy()
    imported.index = pd.Index(
        [
            _timestamp(source_path, row, value, source_timezone)
            for row, value in enumerate(normalized_frame["time"], start=2)
        ],
        dtype="int64",
        name="time",
    )
    return BarStore(bars, imported, dataset_id or _dataset_id(source_path))


def _dataset_id(source_path: Path) -> str:
    selection = _selection_from_filename(source_path)
    return (
        f"{selection.symbol}/{selection.timeframe}" if selection is not None else source_path.stem
    )


def _validated_imported_features(
    frame: pd.DataFrame | None, timestamps: tuple[int, ...], dataset_id: str
) -> pd.DataFrame:
    if frame is None:
        return pd.DataFrame(index=pd.Index(timestamps, dtype="int64", name="time"))
    if not frame.columns.is_unique:
        raise SourceValidationError(f"{dataset_id}: imported feature names must be unique")
    if tuple(frame.index) != timestamps:
        raise SourceValidationError(
            f"{dataset_id}: imported feature timestamps must align exactly with canonical bars"
        )
    if any(
        str(column) in REQUIRED_PRICE_COLUMNS or str(column) in {"volume", "tick_volume"}
        for column in frame.columns
    ):
        raise SourceValidationError(
            f"{dataset_id}: imported features must not overwrite canonical columns"
        )
    validated = frame.copy()
    validated.index = pd.Index(timestamps, dtype="int64", name="time")
    validated.attrs["dataset_id"] = dataset_id
    validated.attrs["feature_sources"] = {str(column): "imported" for column in validated.columns}
    return validated


def _parse_rows(
    source_path: Path, rows: Iterable[Sequence[Any]], source_timezone: ZoneInfo
) -> tuple[Bar, ...]:
    row_iterator = iter(rows)
    columns = _columns(source_path, next(row_iterator, None))
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
