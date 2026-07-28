"""Complexity penalty module for Traveling Salesperson Problem (TSP)."""

from typing import List, Tuple


def calc_complexity_penalty(
    result: Tuple[List[int], float, float],
) -> float:
    """Calculate time complexity penalty based on city count and execution time.

    Args:
        result (Tuple[List[int], float, float]): A tuple containing:
            - tour (List[int]): Sequence of visited city IDs.
            - total_distance (float): Total route distance.
            - elapsed_time (float): Execution time in seconds.

    Returns:
        float: Penalty score between 0.0 (Fast/Optimal) and 1.0 (Slow/Poor).
    """
    tour, _, elapsed_time = result
    n = len(tour)

    # Invalid state check (if no cities or non-positive time, return 0.0)
    if n == 0 or elapsed_time <= 0:
        return 0.0

    # Base expected processing time coefficient for O(N) (10 microseconds/city)
    base_time_per_city = 1e-5

    # Ratio comparing actual execution time against baseline O(N) expectation
    normalized_time_ratio = elapsed_time / (n * base_time_per_city)
    raw_value = max(0.0, normalized_time_ratio - 1.0)

    # Normalization formula: penalty = 1 - 1 / (1 + raw_value)
    penalty = 1.0 - (1.0 / (1.0 + raw_value))

    return round(penalty, 4)


if __name__ == "__main__":
    # Local Test 1: Fast execution for 50 cities
    mock_fast = (list(range(50)), 120.5, 0.0001)
    print("Fast Penalty:", calc_complexity_penalty(mock_fast))

    # Local Test 2: Slow execution for 50 cities
    mock_slow = (list(range(50)), 120.5, 0.1)
    print("Slow Penalty:", calc_complexity_penalty(mock_slow))