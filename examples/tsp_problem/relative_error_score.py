def calculate_relative_error_score(
    result: tuple[list[int], float, float],
    optimal_distance: float,
) -> float:

    total_distance = result[1]

    relative_error = abs(total_distance - optimal_distance) / optimal_distance

    score = max(0.0, 1.0 - relative_error)

    return score
