def calc_complexity_penalty(result: tuple[list[int], float, float]) -> float:
    """
    Calculates the time complexity penalty (Complexity Penalty) of the code 
    based on the number of cities (N) and the elapsed execution time.

    Parameters:
        result (tuple[list[int], float, float]): 
            - tour: The sequence of visited cities (length represents city count N)
            - total_distance: Total distance of the route
            - elapsed_time: Time taken for the code to execute (in seconds)

    Returns:
        float: A penalty score between 0.0 (Optimal Big-O / Very Fast) 
               and 1.0 (Poor Big-O / Extremely Slow).
    """
    tour, _, elapsed_time = result
    n = len(tour)

    # Invalid state check (if no cities or negative execution time, return 0 penalty)
    if n == 0 or elapsed_time <= 0:
        return 0.0

    # Base expected processing time coefficient for O(N) (seconds per city)
    # ~0.001s (1 ms) for 100 cities is a reasonable O(N) / O(N log N) baseline expectation.
    base_time_per_city = 1e-5  # 10 microseconds per city

    # Measure how much the execution time strays relative to problem size N
    # Penalizes exponential or quadratic time growth (N^2, N^3) as N grows.
    normalized_time_ratio = elapsed_time / (n * base_time_per_city)

    # No extra penalty if execution time is less than or equal to baseline expectation
    raw_value = max(0.0, normalized_time_ratio - 1.0)

    # Recommended normalization formula: penalty = 1 - 1 / (1 + raw_value)
    # When raw_value = 0 -> penalty = 0.0
    # As raw_value increases -> penalty asymptotically approaches 1.0.
    penalty = 1.0 - (1.0 / (1.0 + raw_value))

    return round(penalty, 4)


# --- LOCAL TEST / EXAMPLE USAGE ---
if __name__ == "__main__":
    # Test 1: Super fast code for 50 cities (0.0001 sec)
    mock_result_fast = (list(range(50)), 120.5, 0.0001)
    print(f"Fast Code Penalty (N=50): {calc_complexity_penalty(mock_result_fast)}")  # Expected: ~0.0

    # Test 2: Slow code for 50 cities (0.1 sec)
    mock_result_slow = (list(range(50)), 120.5, 0.1)
    print(f"Slow Code Penalty (N=50): {calc_complexity_penalty(mock_result_slow)}")  # Expected: High penalty (~0.99)

    # Test 3: Reasonable execution time for 500 cities (0.005 sec)
    mock_result_medium = (list(range(500)), 450.0, 0.005)
    print(f"Medium Code Penalty (N=500): {calc_complexity_penalty(mock_result_medium)}")