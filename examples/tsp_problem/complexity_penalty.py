def calculate_complexity_penalty(
    result: tuple[list[int], float, float],
) -> float:
    tour, _, elapsed_time = result
    # tour is a closed loop (tour[0] == tour[-1]), so the number of distinct
    # cities is one less than the number of elements in it.
    city_places_count = len(tour) - 1

    if city_places_count <= 0 or elapsed_time <= 0:
        return 0.0

    # Base expected processing time coefficient for O(N) (10 microseconds/city)
    base_time_per_city = 1e-5

    # Ratio comparing actual execution time against baseline O(N) expectation
    normalized_time_ratio = elapsed_time / (city_places_count * base_time_per_city)
    raw_value = max(0.0, normalized_time_ratio - 1.0)

    penalty = 1.0 - (1.0 / (1.0 + raw_value))

    return round(penalty, 4)


if __name__ == "__main__":
    # Local Test 1: Fast execution for 50 cities
    mock_fast = (list(range(50)) + [0], 120.5, 0.0001)
    print("Fast Penalty:", calculate_complexity_penalty(mock_fast))

    # Local Test 2: Slow execution for 50 cities
    mock_slow = (list(range(50)) + [0], 120.5, 0.1)
    print("Slow Penalty:", calculate_complexity_penalty(mock_slow))
