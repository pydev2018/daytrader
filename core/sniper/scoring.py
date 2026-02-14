"""
Scoring for M15 sniper setups.
"""

from __future__ import annotations

import config as cfg


def _weighted_score(weights: dict[str, int], components: dict[str, float]) -> float:
    score = 0.0
    for k, w in weights.items():
        score += max(0.0, min(1.0, components.get(k, 0.0))) * w
    return max(0.0, min(100.0, score))


def score_tpr(components: dict[str, float]) -> tuple[float, dict[str, float]]:
    score = _weighted_score(cfg.TPR_SCORE_WEIGHTS, components)
    return score, components


def score_rbh(components: dict[str, float]) -> tuple[float, dict[str, float]]:
    score = _weighted_score(cfg.RBH_SCORE_WEIGHTS, components)
    return score, components


def score_ecr(components: dict[str, float]) -> tuple[float, dict[str, float]]:
    score = _weighted_score(cfg.ECR_SCORE_WEIGHTS, components)
    return score, components
