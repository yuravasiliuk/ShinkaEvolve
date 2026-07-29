"""
Metric functions used to evaluate TSP solutions.
"""

from typing import Tuple


def calculate_relative_error_score(
    result: Tuple[list[int], float, float],
    optimal_distance: float,
) -> float:
    """
    Calculates a quality score between 0 and 1 based on
    the relative error of the tour distance.

    A perfect solution receives a score of 1.0.
    Larger errors reduce the score.
    """

    _, total_distance, _ = result

    if optimal_distance <= 0:
        raise ValueError("optimal_distance must be positive")

    relative_error = abs(total_distance - optimal_distance) / optimal_distance

    score = max(0.0, 1.0 - relative_error)

    return score