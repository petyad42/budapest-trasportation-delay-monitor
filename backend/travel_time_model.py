"""scikit-learn travel-time regressor (weather-adjusted, not weather-dependent).

:class:`TravelTimeModel` wraps an imputation-friendly scikit-learn pipeline that
predicts expected travel time (minutes) for a stop segment from the features
produced by :class:`backend.features.FeatureBuilder`.

Model choice (per design): a :class:`~sklearn.ensemble.HistGradientBoostingRegressor`,
which handles missing numeric values natively. That native NaN handling is
exactly what makes the prediction *weather-adjusted but not weather-dependent*:
when weather is missing the FeatureBuilder emits ``NaN`` weather columns and the
model still returns a value (Requirements 5.5, 6.4) rather than failing.

Categorical features (route mode, weather category) are ordinal-encoded with an
``OrdinalEncoder`` configured to map unseen / missing categories to a dedicated
code, so an ``"unknown"`` bucket never raises at inference time.

The trained pipeline is persisted with :mod:`joblib` to
:data:`backend.config.MODEL_PATH` (``data/delay_model.joblib``).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Union

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OrdinalEncoder

from backend import config
from backend.features import CATEGORICAL_FEATURES, NUMERIC_FEATURES, FeatureBuilder

logger = logging.getLogger(__name__)

PathLike = Union[str, Path]


def _build_pipeline(random_state: int = 42) -> Pipeline:
    """Construct the untrained model pipeline.

    Numeric columns pass through untouched (NaNs preserved for the estimator's
    native missing-value support). Categorical columns are ordinal-encoded with
    unknown/missing values folded into a single extra code so inference never
    raises on an ``"unknown"`` bucket.
    """
    categorical = OrdinalEncoder(
        handle_unknown="use_encoded_value",
        unknown_value=-1,
        encoded_missing_value=-1,
    )
    pre = ColumnTransformer(
        transformers=[
            ("num", "passthrough", NUMERIC_FEATURES),
            ("cat", categorical, CATEGORICAL_FEATURES),
        ],
        remainder="drop",
    )
    # The ordinal-encoded categorical columns are the last len(CATEGORICAL_FEATURES)
    # columns after the ColumnTransformer; tell the estimator they are categorical.
    n_num = len(NUMERIC_FEATURES)
    cat_indices = list(range(n_num, n_num + len(CATEGORICAL_FEATURES)))
    estimator = HistGradientBoostingRegressor(
        random_state=random_state,
        categorical_features=cat_indices,
    )
    return Pipeline([("pre", pre), ("model", estimator)])


class TravelTimeModel:
    """Train / predict / persist the travel-time regressor.

    Parameters
    ----------
    model_path:
        Where :meth:`save` writes and :meth:`load` reads the joblib artifact.
        Defaults to :data:`config.MODEL_PATH`.
    """

    def __init__(self, model_path: Optional[PathLike] = None) -> None:
        self.model_path = Path(model_path) if model_path else Path(config.MODEL_PATH)
        self._pipeline: Optional[Pipeline] = None

    # -- public API ---------------------------------------------------------

    @property
    def is_trained(self) -> bool:
        """``True`` once :meth:`train` or :meth:`load` has populated a pipeline."""
        return self._pipeline is not None

    def train(self, X: pd.DataFrame, y: pd.Series) -> None:
        """Fit the pipeline on feature frame ``X`` and target ``y`` (minutes).

        ``X`` must contain the columns declared by
        :data:`FeatureBuilder.FEATURE_COLUMNS`; extra columns are ignored by the
        column transformer.
        """
        missing = [c for c in FeatureBuilder.FEATURE_COLUMNS if c not in X.columns]
        if missing:
            raise ValueError(f"training frame is missing feature columns: {missing}")
        pipeline = _build_pipeline()
        pipeline.fit(X[FeatureBuilder.FEATURE_COLUMNS], np.asarray(y, dtype=float))
        self._pipeline = pipeline

    def predict(self, X: pd.DataFrame) -> float:
        """Predict expected travel time (minutes) for the first row of ``X``.

        The design's signature returns a single float, matching the single-row
        feature frame the :class:`FeatureBuilder` produces per request. Never
        requires weather: rows with ``NaN`` weather columns still predict
        (R5.5, R6.4).
        """
        if self._pipeline is None:
            raise RuntimeError(
                "TravelTimeModel is not trained; call train() or load() first."
            )
        if X.empty:
            raise ValueError("cannot predict on an empty feature frame")
        preds = self._pipeline.predict(X[FeatureBuilder.FEATURE_COLUMNS])
        return float(preds[0])

    def save(self, path: Optional[PathLike] = None) -> Path:
        """Persist the trained pipeline to ``path`` (default :data:`model_path`)."""
        if self._pipeline is None:
            raise RuntimeError("cannot save an untrained TravelTimeModel")
        target = Path(path) if path else self.model_path
        target.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(self._pipeline, target)
        logger.info("Saved TravelTimeModel to %s", target)
        return target

    def load(self, path: Optional[PathLike] = None) -> None:
        """Load a previously persisted pipeline from ``path`` (default path)."""
        source = Path(path) if path else self.model_path
        if not source.exists():
            raise FileNotFoundError(f"no model artifact at {source}")
        self._pipeline = joblib.load(source)
        logger.info("Loaded TravelTimeModel from %s", source)


__all__ = ["TravelTimeModel"]
