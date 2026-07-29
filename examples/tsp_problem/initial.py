import time


# EVOLVE-BLOCK-START
def solve_tsp(dist_matrix: list[list[float]]) -> tuple[list[int], float]:
    n: int = len(dist_matrix)

    tour: list[int] = list(range(n)) + [0]

    total_distance: float = 0.0
    for i in range(n):
        u: int = tour[i]
        v: int = tour[i + 1]
        total_distance += dist_matrix[u][v]

    return tour, total_distance


# EVOLVE-BLOCK-END


def tsp_problem(dist_matrix: list[list[float]]) -> tuple[list[int], float, float]:
    start = time.perf_counter()
    tour, total_distance = solve_tsp(dist_matrix)
    elapsed = time.perf_counter() - start

    return tour, total_distance, elapsed
