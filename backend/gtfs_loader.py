"""Static GTFS parsing for the Budapest Transit Monitor backend.

The BKK GTFS static feed is a zip of CSV files. :class:`GtfsLoader` reads the
handful of tables the application needs into pandas DataFrames and returns them
in a :class:`GtfsTables` dataclass. This module is pure I/O + parsing; querying
the tables is the job of ``GtfsRepository`` (task 4).

Relevant files/columns (see design "Data Sources and Formats"):
  routes.txt      -> route_id, route_short_name, route_type, route_color
  trips.txt       -> trip_id, route_id, service_id, direction_id, shape_id
  stops.txt       -> stop_id, stop_name, stop_lat, stop_lon
  stop_times.txt  -> trip_id, stop_id, arrival_time, departure_time, stop_sequence
  calendar.txt    -> service_id, weekday flags, start_date, end_date
                     (falls back to calendar_dates.txt when calendar.txt is absent)

If the zip is missing or corrupt, :class:`GtfsLoadError` is raised so the
repository can catch it and fall back to a mock catalog without crashing
(Requirements 9.1, 9.3).
"""
from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Union

import pandas as pd

# GTFS files we parse. ``calendar`` is special-cased below because feeds may
# ship calendar.txt, calendar_dates.txt, or both.
_CORE_FILES = ("routes", "trips", "stops", "stop_times")

# Numeric columns to coerce per table so downstream math is not tripped up by
# string types coming out of the CSV parser.
_NUMERIC_COLUMNS = {
    "routes": ("route_type",),
    "stops": ("stop_lat", "stop_lon"),
    "stop_times": ("stop_sequence",),
    "trips": ("direction_id",),
}


class GtfsLoadError(Exception):
    """Raised when the GTFS zip is missing, unreadable, or corrupt.

    ``GtfsRepository`` catches this to fall back to a mock catalog instead of
    crashing the backend (Requirement 9.3).
    """


@dataclass
class GtfsTables:
    """Static GTFS tables held as pandas DataFrames.

    ``calendar`` may originate from either ``calendar.txt`` or
    ``calendar_dates.txt`` depending on the feed; it is empty if neither is
    present.
    """

    routes: pd.DataFrame
    trips: pd.DataFrame
    stops: pd.DataFrame
    stop_times: pd.DataFrame
    calendar: pd.DataFrame


class GtfsLoader:
    """Reads a static GTFS zip into :class:`GtfsTables`."""

    def load(self, zip_path: Union[str, Path]) -> GtfsTables:
        """Load and parse the GTFS zip at ``zip_path``.

        Raises:
            GtfsLoadError: if the file is missing, not a valid zip, or a
                required table cannot be parsed.
        """
        path = Path(zip_path)
        if not path.exists():
            raise GtfsLoadError(f"GTFS zip not found: {path}")
        if not path.is_file():
            raise GtfsLoadError(f"GTFS zip path is not a file: {path}")

        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise GtfsLoadError(f"Could not read GTFS zip {path}: {exc}") from exc

        return self.load_bytes(raw, source=str(path))

    def load_bytes(self, raw: bytes, *, source: str = "<bytes>") -> GtfsTables:
        """Load and parse GTFS tables from raw zip ``bytes``."""
        if not zipfile.is_zipfile(io.BytesIO(raw)):
            raise GtfsLoadError(f"GTFS source is not a valid zip: {source}")

        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                names = set(zf.namelist())
                tables = {
                    name: self._read_table(zf, f"{name}.txt", names)
                    for name in _CORE_FILES
                }
                tables["calendar"] = self._read_calendar(zf, names)
        except zipfile.BadZipFile as exc:
            raise GtfsLoadError(f"Corrupt GTFS zip {source}: {exc}") from exc
        except pd.errors.ParserError as exc:
            raise GtfsLoadError(f"Could not parse GTFS table in {source}: {exc}") from exc

        return GtfsTables(
            routes=tables["routes"],
            trips=tables["trips"],
            stops=tables["stops"],
            stop_times=tables["stop_times"],
            calendar=tables["calendar"],
        )

    def _read_table(
        self, zf: zipfile.ZipFile, filename: str, names: set[str]
    ) -> pd.DataFrame:
        """Read one CSV member into a DataFrame, coercing known numeric columns.

        A required core table that is absent from the archive is treated as a
        corrupt/incomplete feed and raises :class:`GtfsLoadError`.
        """
        table_name = filename.removesuffix(".txt")
        if filename not in names:
            raise GtfsLoadError(f"Required GTFS file missing: {filename}")

        with zf.open(filename) as fh:
            # Read everything as string first so id columns keep leading zeros;
            # numeric columns are coerced explicitly below.
            df = pd.read_csv(fh, dtype=str, low_memory=False)

        for col in _NUMERIC_COLUMNS.get(table_name, ()):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        return df

    def _read_calendar(self, zf: zipfile.ZipFile, names: set[str]) -> pd.DataFrame:
        """Read calendar.txt if present, else calendar_dates.txt, else empty.

        GTFS feeds may define service via ``calendar.txt`` (weekday flags) or
        ``calendar_dates.txt`` (per-date exceptions), or both. Either satisfies
        the "calendar" table the design lists; neither is treated as an empty
        (but valid) table rather than a corrupt feed.
        """
        for filename in ("calendar.txt", "calendar_dates.txt"):
            if filename in names:
                with zf.open(filename) as fh:
                    return pd.read_csv(fh, dtype=str, low_memory=False)
        return pd.DataFrame()


__all__ = ["GtfsLoader", "GtfsLoadError", "GtfsTables"]
