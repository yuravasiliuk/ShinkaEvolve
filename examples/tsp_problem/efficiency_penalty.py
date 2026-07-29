import time

from cities import CITIES


# A base, greedy and naive tsp problem function. Its time to perform algorithm serves as a basline time for LLM solution
def naive_tsp(dist_matrix: list[list[float]]) -> float:
    n = len(dist_matrix)
    start = time.perf_counter()

    visited = [False] * n
    visited[0] = True
    current = 0
    total_distance = 0.0

    for _ in range(n - 1):
        nearest_city = -1
        nearest_distance = float("inf")
        for city in range(n):
            if not visited[city] and dist_matrix[current][city] < nearest_distance:
                nearest_city = city
                nearest_distance = dist_matrix[current][city]
        visited[nearest_city] = True
        total_distance += nearest_distance
        current = nearest_city

    total_distance += dist_matrix[current][0]

    elapsed = time.perf_counter() - start
    return elapsed


# Function that returns penalty for a particular result from 0 to 1
def calculate_efficiency_penalty(
    result: tuple[list[int], float, float], run_idx: int
) -> float:
    dist_matrix = CITIES[run_idx]

    llm_elapsed = result[2]
    greedy_elapsed = naive_tsp(dist_matrix)

    if greedy_elapsed <= 0:
        return 0.0

    ratio = llm_elapsed / greedy_elapsed  # comparision to greedy algorithm

    if ratio <= 1 / 3:  # 3x faster -> no penalty
        return 0.0
    if (
        ratio <= 1.0
    ):  # from 3x faster to the same speed as greedy -> penalty from 0.0 to 0.8
        return 0.8 * (ratio - 1 / 3) / (1 - 1 / 3)
    if (
        ratio <= 1.5
    ):  # from the same speed as greedy to 50% slower -> penalty from 0.8 to 1
        return 0.8 + 0.2 * (ratio - 1.0)
    return 1.0  # more than 50% slower than greedy
