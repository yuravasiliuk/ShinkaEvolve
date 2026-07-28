from typing import List, Tuple


def calc_complexity_penalty(
    result: Tuple[List[int], float, float]
) -> float:
    """
    Calculates the time complexity penalty of the code.

    Parameters:
        result (Tuple[List[int], float, float]):
            - tour: Sequence of visited cities
            - total_distance: Route length
            - elapsed_time: Execution time in seconds

    Returns:
        float: Penalty score between 0.0 (Fast) and 1.0 (Slow).
    """
    tour, _, elapsed_time = result
    n = len(tour)

    # Invalid state check
    if n == 0 or elapsed_time <= 0:
        return 0.0

    # Base expected processing time coefficient for O(N)
    base_time_per_city = 1e-5

    # Measure execution time growth relative to size N
    normalized_time_ratio = elapsed_time / (n * base_time_per_city)
    raw_value = max(0.0, normalized_time_ratio - 1.0)

    # Normalization formula: penalty = 1 - 1 / (1 + raw_value)
    penalty = 1.0 - (1.0 / (1.0 + raw_value))

    return round(penalty, 4)


if __name__ == "__main__":
    # Local Test 1: Fast code
    mock_fast = (list(range(50)), 120.5, 0.0001)
    print("Fast Penalty:", calc_complexity_penalty(mock_fast))

    # Local Test 2: Slow code
    mock_slow = (list(range(50)), 120.5, 0.1)
    print("Slow Penalty:", calc_complexity_penalty(mock_slow))