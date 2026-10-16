"""Training script + initial model artifact for the travel-time predictor (task 10).

This script assembles a training set for :class:`backend.travel_time_model.TravelTimeModel`
and persists the fitted artifact to :data:`backend.config.MODEL_PATH`
(``data/delay_model.joblib``). It uses **only free data sources** (BKK GTFS
static + a synthetic bootstrap; no paid APIs), satisfying Requirement 6.5.

Data-assembly strategy
----------------------
The production target is the *actual observed travel time between consecutive
stops*, derived from **historical GTFS-RT vs. schedule** (design "TravelTimeModel"
section). For the initial deliverable no GTFS-RT history has been collected yet,
so the script layers two sources, preferring real data where it exists:

1. **GTFS-static skeleton (real, free data).** We read the static BKK GTFS feed
   and compute the *scheduled* segment duration between every pair of
   consecutive stops on a trip (from ``stop_times`` arrival/departure times).
   These real scheduled durations anchor the ``scheduled_segment_min`` feature
   and the base of the target so the model learns realistic magnitudes per
   route/time-of-day.

2. **Documented synthetic-bootstrap (fallback / augmentation).** Because we have
   no recorded GTFS-RT-vs-schedule deltas yet, the *observed* travel time is
   approximated with a documented synthetic model:

       observed = scheduled + f(recent_delay, weather, hour, mode) + noise

   The perturbation ``f`` encodes plausible, transparent relationships (rush-hour
   and rain add time, buses are more delay-sensitive than metro, etc.). This is a
   *bootstrap* so the shipped artifact predicts sensible values on day one; it is
   intended to be replaced once real GTFS-RT history is available. The same
   synthetic model also fully generates the training set when GTFS static is
   unavailable/corrupt, so training never depends on a network fetch.

Both paths emit exactly :data:`FeatureBuilder.FEATURE_COLUMNS`, so the schema the
model trains on always matches what :class:`FeatureBuilder` produces at inference.

No paid APIs are contacted: GTFS static is read from the local zip
(:data:`config.GTFS_ZIP_PATH`, a free BKK feed) and weather values are synthesized
from Open-Meteo's public WMO code scheme rather than fetched. This keeps the
training step deterministic, offline, and free.

Usage
-----
    python -m backend.train_model                 # default: GTFS + synthetic bootstrap
    python -m backend.train_model --rows 8000      # size of the synthetic bootstrap
    python -m backend.train_model --synthetic-only # skip GTFS, pure synthetic
    python -m backend.train_model --seed 7 --out data/delay_model.joblib
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from backend import config
from backend.features import (
    CATEGORICAL_FEATURES,
    NUMERIC_FEATURES,
    FeatureBuilder,
    _weather_category,
)
from backend.gtfs_loader import GtfsLoader, GtfsLoadError
from backend.models import Mode
from backend.travel_time_model import TravelTimeModel

logger = logging.getLogger(__name__)

# Modes the synthetic bootstrap draws from (matches the app's modeled modes).
_MODES = [m.value for m in Mode]

# A representative sample of WMO weather codes spanning the buckets that
# ``_weather_category`` understands (clear/cloudy/fog/drizzle/rain/snow/storm).
_WEATHER_CODES = [0, 1, 2, 3, 45, 48, 51, 55, 61, 63, 65, 71, 75, 80, 82, 95]

# How strongly each mode's travel time reacts to delay/weather. Metro (grade
# separated) is the least sensitive; surface buses the most. Kept explicit and
# documented so the synthetic relationship is auditable, not a black box.
_MODE_DELAY_SENSITIVITY = {
    Mode.METRO.value: 0.35,
    Mode.TRAM.value: 0.75,
    Mode.TROLLEYBUS.value: 0.90,
    Mode.BUS.value: 1.00,
}


# --------------------------------------------------------------------------- #
# GTFS-static skeleton (real, free data)
# --------------------------------------------------------------------------- #
def _parse_gtfs_time_to_minutes(value: object) -> Optional[float]:
    """Parse a GTFS ``HH:MM:SS`` time into minutes-since-midnight.

    GTFS allows hours >= 24 (service past midnight); this handles that. Returns
    ``None`` for missing/unparseable values.
    """
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    text = str(value).strip()
    if not text:
        return None
    parts = text.split(":")
    if len(parts) != 3:
        return None
    try:
        hh, mm, ss = (int(p) for p in parts)
    except ValueError:
        return None
    return hh * 60.0 + mm + ss / 60.0


def _scheduled_segments_from_gtfs(
    zip_path: Path, *, max_trips: int
) -> pd.DataFrame:
    """Compute scheduled consecutive-stop segment durations from static GTFS.

    Returns a frame with columns ``route_id``, ``stop_id`` (the segment's
    *arrival* stop), ``mode``, ``scheduled_segment_min``, ``hour_of_day``,
    ``day_of_week``. Empty frame when GTFS is unavailable/corrupt (the caller
    then relies solely on the synthetic bootstrap).
    """
    try:
        tables = GtfsLoader().load(zip_path)
    except GtfsLoadError as exc:
        logger.warning(
            "GTFS static unavailable (%s); using synthetic bootstrap only.", exc
        )
        return pd.DataFrame()

    from backend import colors  # local import to avoid a cycle at module load

    routes = tables.routes
    stop_times = tables.stop_times
    trips = tables.trips
    if stop_times.empty or trips.empty or routes.empty:
        return pd.DataFrame()

    # route_id -> mode string (skip routes whose type we do not model).
    route_mode: dict[str, str] = {}
    for _, row in routes.iterrows():
        mode = colors.mode_from_route_type(row.get("route_type"))
        if mode is not None:
            route_mode[str(row["route_id"])] = mode.value
    trip_route = {
        str(r["trip_id"]): str(r["route_id"]) for _, r in trips.iterrows()
    }

    st = stop_times.copy()
    st["trip_id"] = st["trip_id"].astype(str)
    st["stop_id"] = st["stop_id"].astype(str)
    st["stop_sequence"] = pd.to_numeric(st["stop_sequence"], errors="coerce")

    records: list[dict[str, object]] = []
    seen_trips = 0
    for trip_id, group in st.groupby("trip_id", sort=False):
        route_id = trip_route.get(trip_id)
        mode = route_mode.get(route_id) if route_id else None
        if route_id is None or mode is None:
            continue
        group = group.sort_values("stop_sequence")
        dep = group.get("departure_time")
        arr = group.get("arrival_time")
        stops = group["stop_id"].tolist()
        dep_min = [_parse_gtfs_time_to_minutes(v) for v in (dep if dep is not None else [])]
        arr_min = [_parse_gtfs_time_to_minutes(v) for v in (arr if arr is not None else [])]

        for i in range(1, len(stops)):
            start = dep_min[i - 1] if i - 1 < len(dep_min) else None
            end = arr_min[i] if i < len(arr_min) else None
            if start is None or end is None:
                continue
            seg = end - start
            if not (0.0 < seg <= 60.0):  # drop non-positive / absurd segments
                continue
            hour = int((start // 60) % 24)
            records.append(
                {
                    "route_id": route_id,
                    "stop_id": stops[i],
                    "route_mode": mode,
                    "scheduled_segment_min": float(seg),
                    "hour_of_day": float(hour),
                }
            )
        seen_trips += 1
        if seen_trips >= max_trips:
            break

    if not records:
        return pd.DataFrame()
    frame = pd.DataFrame.from_records(records)
    logger.info(
        "Assembled %d scheduled segments from GTFS static across %d trips.",
        len(frame),
        seen_trips,
    )
    return frame


# --------------------------------------------------------------------------- #
# Synthetic bootstrap target + feature completion
# --------------------------------------------------------------------------- #
def _synthesize_rows(rng: np.random.Generator, n: int) -> pd.DataFrame:
    """Generate a fully synthetic skeleton (used as fallback/augmentation)."""
    modes = rng.choice(_MODES, size=n)
    # Scheduled segments: short hops dominate, with a long tail.
    scheduled = np.clip(rng.gamma(shape=2.0, scale=1.1, size=n), 0.5, 30.0)
    hour = rng.integers(0, 24, size=n).astype(float)
    return pd.DataFrame(
        {
            "route_mode": modes,
            "scheduled_segment_min": scheduled,
            "hour_of_day": hour,
        }
    )


def _complete_features_and_target(
    skeleton: pd.DataFrame, rng: np.random.Generator
) -> tuple[pd.DataFrame, pd.Series]:
    """Attach synthetic delay/weather features and a documented target.

    ``skeleton`` must carry ``route_mode``, ``scheduled_segment_min`` and
    ``hour_of_day``. This fills the remaining feature columns and derives the
    observed-travel-time target as a transparent function of them.
    """
    n = len(skeleton)
    df = skeleton.reset_index(drop=True).copy()

    # day-of-week (0=Mon..6=Sun); weekdays weighted a touch heavier.
    df["day_of_week"] = rng.integers(0, 7, size=n).astype(float)

    # Recent observed delay on the route (minutes): mostly small, long right tail.
    recent_delay = np.clip(rng.gamma(shape=1.5, scale=1.2, size=n), 0.0, 25.0)
    df["recent_delay_min"] = recent_delay

    # Weather: sample WMO codes then derive plausible numeric weather features.
    codes = rng.choice(_WEATHER_CODES, size=n)
    temperature = rng.normal(12.0, 9.0, size=n)          # Budapest-ish annual range
    # Precipitation only for wet codes; wind mild-to-gusty.
    wet = np.isin(codes, [51, 55, 61, 63, 65, 80, 82, 71, 75, 95])
    precipitation = np.where(
        wet, np.clip(rng.gamma(2.0, 1.5, size=n), 0.0, 30.0), 0.0
    )
    wind = np.clip(rng.gamma(2.0, 6.0, size=n), 0.0, 80.0)
    df["temperature_c"] = temperature
    df["precipitation_mm"] = precipitation
    df["wind_kph"] = wind
    df["weather_category"] = [
        _weather_category(int(c)) for c in codes
    ]

    # --- Documented synthetic target ------------------------------------- #
    # observed = scheduled
    #          + mode_sensitivity * (rush-hour + delay-carryover + weather)
    #          + gaussian noise
    scheduled = df["scheduled_segment_min"].to_numpy()
    sensitivity = np.array(
        [_MODE_DELAY_SENSITIVITY.get(m, 1.0) for m in df["route_mode"]]
    )
    hour = df["hour_of_day"].to_numpy()
    # Morning (7-9) and evening (16-18) peaks add congestion time.
    rush = np.where(
        ((hour >= 7) & (hour <= 9)) | ((hour >= 16) & (hour <= 18)),
        0.20 * scheduled,
        0.0,
    )
    delay_carryover = 0.35 * recent_delay
    weather_penalty = 0.04 * precipitation + 0.015 * wind
    noise = rng.normal(0.0, 0.4, size=n)

    observed = (
        scheduled
        + sensitivity * (rush + delay_carryover + weather_penalty)
        + noise
    )
    observed = np.clip(observed, 0.1, None)  # travel time is strictly positive

    X = df[FeatureBuilder.FEATURE_COLUMNS].copy()
    for col in NUMERIC_FEATURES:
        X[col] = pd.to_numeric(X[col], errors="coerce")
    for col in CATEGORICAL_FEATURES:
        X[col] = X[col].astype("object")
    y = pd.Series(observed, name="observed_travel_min")
    return X, y


def assemble_training_set(
    *,
    rows: int,
    seed: int,
    synthetic_only: bool,
    gtfs_zip: Optional[Path] = None,
    max_trips: int = 4000,
) -> tuple[pd.DataFrame, pd.Series]:
    """Build ``(X, y)`` for training, preferring real GTFS segments.

    When GTFS static is available (and not ``synthetic_only``) its real
    scheduled segments seed the skeleton; the set is topped up with synthetic
    rows to reach ``rows``. Otherwise the whole set is synthesized. Either way
    the delay/weather features and the target come from the documented synthetic
    bootstrap, since no GTFS-RT history exists yet.
    """
    rng = np.random.default_rng(seed)
    gtfs_zip = gtfs_zip or Path(config.GTFS_ZIP_PATH)

    skeleton_parts: list[pd.DataFrame] = []
    if not synthetic_only:
        gtfs_segments = _scheduled_segments_from_gtfs(gtfs_zip, max_trips=max_trips)
        if not gtfs_segments.empty:
            # Sample real segments (with replacement so we can hit `rows`).
            take = min(rows, len(gtfs_segments))
            sampled = gtfs_segments.sample(
                n=take, random_state=seed, replace=False
            )[["route_mode", "scheduled_segment_min", "hour_of_day"]]
            skeleton_parts.append(sampled)

    have_real = sum(len(p) for p in skeleton_parts)
    remaining = max(rows - have_real, 0)
    if remaining > 0 or not skeleton_parts:
        skeleton_parts.append(_synthesize_rows(rng, remaining or rows))

    skeleton = pd.concat(skeleton_parts, ignore_index=True)
    X, y = _complete_features_and_target(skeleton, rng)
    logger.info(
        "Training set assembled: %d rows (%d from GTFS static, %d synthetic).",
        len(X),
        have_real,
        len(X) - have_real,
    )
    return X, y


def train_and_save(
    *,
    rows: int = 6000,
    seed: int = 42,
    synthetic_only: bool = False,
    out_path: Optional[Path] = None,
) -> Path:
    """Assemble data, fit :class:`TravelTimeModel`, and persist the artifact.

    Returns the path the artifact was written to.
    """
    X, y = assemble_training_set(
        rows=rows, seed=seed, synthetic_only=synthetic_only
    )
    model = TravelTimeModel(model_path=out_path) if out_path else TravelTimeModel()
    model.train(X, y)
    saved = model.save()
    logger.info("Trained on %d rows; wrote artifact to %s", len(X), saved)
    return saved


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rows",
        type=int,
        default=6000,
        help="Number of training rows to assemble (default: 6000).",
    )
    parser.add_argument(
        "--seed", type=int, default=42, help="Random seed (default: 42)."
    )
    parser.add_argument(
        "--synthetic-only",
        action="store_true",
        help="Skip GTFS static and generate a fully synthetic training set.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output artifact path (default: config.MODEL_PATH).",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = _build_arg_parser().parse_args(argv)
    saved = train_and_save(
        rows=args.rows,
        seed=args.seed,
        synthetic_only=args.synthetic_only,
        out_path=args.out,
    )
    print(f"Model artifact written to: {saved}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    raise SystemExit(main())
