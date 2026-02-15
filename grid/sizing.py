"""
Order sizing for grid rungs.
"""

from __future__ import annotations


def inventory_scale(side: str, inv_ratio: float, gamma: float) -> float:
    """Scale size based on inventory skew."""
    inv_ratio = max(-1.0, min(1.0, inv_ratio))
    if side.upper() == "BUY":
        scale = 1.0 - gamma * inv_ratio
    else:
        scale = 1.0 + gamma * inv_ratio
    return max(0.2, min(2.0, scale))


def depth_taper(level_index: int, eta: float) -> float:
    """Reduce size as we go further from center."""
    depth = max(1, abs(level_index))
    return 1.0 / (1.0 + eta * (depth - 1))


def size_for_rung(
    side: str,
    level_index: int,
    base_size: float,
    inv_ratio: float,
    gamma: float,
    eta: float,
) -> float:
    scale = inventory_scale(side, inv_ratio, gamma)
    taper = depth_taper(level_index, eta)
    return max(0.0, base_size * scale * taper)
