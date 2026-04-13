"""
Anomaly detection stub using IsolationForest.

This module is **optional** and not used in the default live pipeline
(see ``rules.py``).  It serves as a starting point for ML-based anomaly
detection once enough session data has been collected.

Usage:
    detector = AnomalyDetector()
    detector.fit(training_feature_matrix)
    is_anomaly, score = detector.predict(feature_vector)
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

logger = logging.getLogger("live_analytics.anomaly")

try:
    from sklearn.ensemble import IsolationForest  # type: ignore[import-untyped]

    _HAS_SKLEARN = True
except ImportError:
    _HAS_SKLEARN = False
    logger.info("scikit-learn not installed – anomaly detection disabled.")


class AnomalyDetector:
    """Thin wrapper around ``sklearn.ensemble.IsolationForest``."""

    def __init__(self, contamination: float = 0.05, random_state: int = 42) -> None:
        self._model: Any = None
        self._contamination = contamination
        self._random_state = random_state
        self._fitted = False

    @property
    def available(self) -> bool:
        return _HAS_SKLEARN

    @property
    def fitted(self) -> bool:
        return self._fitted

    def fit(self, X: np.ndarray) -> None:  # noqa: N803
        """Fit the Isolation Forest on feature matrix *X* (n_samples × n_features)."""
        if not _HAS_SKLEARN:
            logger.warning("Cannot fit – scikit-learn not installed.")
            return
        self._model = IsolationForest(
            contamination=self._contamination,
            random_state=self._random_state,
        )
        self._model.fit(X)
        self._fitted = True
        logger.info("AnomalyDetector fitted on %d samples.", X.shape[0])

    def predict(self, x: np.ndarray) -> tuple[bool, float]:
        """
        Predict whether a single feature vector *x* (1-D) is anomalous.

        Returns (is_anomaly, anomaly_score).
        """
        if not self._fitted or self._model is None:
            return False, 0.0
        x_2d = x.reshape(1, -1)
        label = self._model.predict(x_2d)[0]  # 1 = normal, -1 = anomaly
        score = float(self._model.decision_function(x_2d)[0])
        return label == -1, score
