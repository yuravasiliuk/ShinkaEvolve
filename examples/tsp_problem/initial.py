# EVOLVE-BLOCK-START
def tsp_problem(dist_matrix: list[list[float]]) -> tuple[list[int], float, float]:
    n: int = len(dist_matrix)

    tour: list[int] = list(range(n))
    
    total_distance: float = 0.0
    for i in range(n):
        u: int = tour[i]
        v: int = tour[(i + 1) % n]
        total_distance += dist_matrix[u][v]

    elapsed = 0
        
    return tour, total_distance, elapsed
# EVOLVE-BLOCK-END