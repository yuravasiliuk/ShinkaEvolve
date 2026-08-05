import argparse
import math
import time

import numpy as np

from shinka.core import run_shinka_eval

# shinka_run only copies evaluate.py into results_dir (not sibling files), so
# every helper this script needs must live in this one file.

CITY_SIZES = [5, 50, 500, 1000, 2000]
RADIUS = 1000.0

# How far above the true optimum a tour may be and still validate/score.
# Loosened from "must exactly match" so heuristics that get close but don't
# guarantee optimality (GA, Or-opt, simulated annealing, ...) have room to
# survive selection instead of being zeroed the first time they land on a
# non-optimal tour.
OPTIMALITY_TOLERANCE = 0.05  # 5% above the known optimal tour length


# Points placed on a circle are always in convex position, so the shortest
# tour is guaranteed to be the one visiting them in angular order around the
# center (any crossing tour can be shortened by uncrossing it, and angular
# order is the only crossing-free tour). That's what lets us generate city
# sets of any size and still know the true optimal tour length up front.
def generate_convex_city(
    num_cities: int, seed: int
) -> tuple[list[list[float]], float]:
    rng = np.random.default_rng(seed)
    angles = rng.uniform(0.0, 2 * math.pi, size=num_cities)
    points = np.column_stack((RADIUS * np.cos(angles), RADIUS * np.sin(angles)))
    rng.shuffle(points)  # so city index order isn't already the optimal order

    dist_matrix = np.linalg.norm(points[:, None, :] - points[None, :, :], axis=-1)

    angular_order = np.argsort(np.arctan2(points[:, 1], points[:, 0]))
    next_in_order = np.roll(angular_order, -1)
    optimal_distance = float(dist_matrix[angular_order, next_in_order].sum())

    return dist_matrix.tolist(), optimal_distance


CITIES = []
OPTIMAL_DISTANCES = []
for size in CITY_SIZES:
    city_matrix, city_optimal_distance = generate_convex_city(size, seed=size)
    CITIES.append(city_matrix)
    OPTIMAL_DISTANCES.append(city_optimal_distance)

CITY_OPTIMAL_DISTANCE_BY_SIZE = {
    len(dist_matrix): optimal_distance
    for dist_matrix, optimal_distance in zip(CITIES, OPTIMAL_DISTANCES)
}


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


# Penalizes total_distance for being above the known optimum: 0 at exactly
# optimal, scaling up to 1 right at the OPTIMALITY_TOLERANCE boundary (the
# validate_fn cutoff), so tours within the tolerance window are scored by how
# close to optimal they are instead of all being treated as equally correct.
def calculate_distance_penalty(
    result: tuple[list[int], float, float], run_idx: int
) -> float:
    _, total_distance, _ = result
    optimal_distance = OPTIMAL_DISTANCES[run_idx]

    if optimal_distance <= 0:
        return 0.0

    excess_ratio = max(0.0, (total_distance - optimal_distance) / optimal_distance)
    penalty = min(1.0, excess_ratio / OPTIMALITY_TOLERANCE)

    return round(penalty, 4)


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


# here we provide the arguments to a generated program in each run for specific generation code, more explanation in def main()
def get_experiment_kwargs(run_idx: int) -> dict[str, object]:
    return {"dist_matrix": CITIES[run_idx]}


# Here we validate our program. We check if the algorithm visits each city exactly once, every city was
# visited, and the tour distance matches the known optimal distance for that city set.
# It's True and None when everything is ok and False, "Explaining why was it invalid" when it's not
def validate_fn(
    result: tuple[list[int], float, float],
) -> tuple[bool, str | None]:
    tour, total_distance, _ = result

    # The path must return to the starting city: it has to appear twice,
    # once at the start and once at the end. Otherwise it's not a closed tour.
    if len(tour) < 2 or tour[0] != tour[-1]:
        first = tour[0] if tour else None
        last = tour[-1] if tour else None
        return False, (
            f"Tour must return to the starting city (first={first!r}, "
            f"last={last!r})"
        )

    visited_cities = tour[:-1]
    city_places_count = len(visited_cities)

    # Excluding the repeated closing city, a valid tour visits every city
    # exactly once (a permutation of 0..n-1).
    if sorted(visited_cities) != list(range(city_places_count)):
        missing = sorted(set(range(city_places_count)) - set(visited_cities))
        duplicates = sorted(
            {city for city in visited_cities if visited_cities.count(city) > 1}
        )
        details = []
        if missing:
            details.append(f"missing cities {missing}")
        if duplicates:
            details.append(f"duplicated cities {duplicates}")
        return False, f"Tour must visit each city exactly once ({', '.join(details)})"

    optimal_distance = CITY_OPTIMAL_DISTANCE_BY_SIZE[city_places_count]
    max_allowed_distance = optimal_distance * (1 + OPTIMALITY_TOLERANCE)

    # rel_tol guards against float summation noise (up to 2000 cities) pushing
    # a tour right at the tolerance boundary just over the line.
    if total_distance > max_allowed_distance and not math.isclose(
        total_distance, max_allowed_distance, rel_tol=1e-6
    ):
        return False, (
            f"Tour distance {total_distance} is more than "
            f"{OPTIMALITY_TOLERANCE:.0%} above the known optimal "
            f"({optimal_distance}, max allowed {max_allowed_distance})"
        )

    return True, None


def evaluate_run(run_idx, result):
    d_pen = calculate_distance_penalty(result, run_idx)
    t_pen = calculate_efficiency_penalty(result, run_idx)
    c_pen = calculate_complexity_penalty(result)
    score = 1 - (0.5 * d_pen + 0.15 * t_pen + 0.35 * c_pen)
    return score, d_pen, t_pen, c_pen


# This is the most important function. Here we write the test to generate the score for current program.
def aggregate_metrics_fn(
    results: list[tuple[list[int], float, float]],
) -> dict[str, object]:

    number_of_runs = len(results)
    evaluations = [
        evaluate_run(run_idx, result) for run_idx, result in enumerate(results)
    ]

    (
        this_generation_score,
        distance_penalties,
        time_penalties,
        complexity_penalties,
    ) = [sum(metric) / number_of_runs for metric in zip(*evaluations)]

    return {
        # This is most important metric for code we are testing. We need to somehow create a score
        # that's between 0 and 1. The score is based on all the results from current generation.
        # We can change our scale if we want, it can be to be honest ANY number if its easier for us to generate it that way
        "combined_score": this_generation_score,
        # This is what LLM sees as a feedback. We provide data that will help LLM decide what has to be improved
        # besides just a score
        "public": {
            "evaluations": evaluations,
            "this_generation_score": this_generation_score,
            "distance_penalties": distance_penalties,
            "time_penalties": time_penalties,
            "complexity_penalties": complexity_penalties,
        },
        # It's a small object with data stored in metrics.json, metrics are for us, not LLM
        # We put here anything we want to track
        "private": {},
        "extra_data": {},  # A little different "private", we don't use it
        "text_feedback": "",  # must be enabled, we don't use it
    }


# Here in Main Shinka runs the evolutions. We provide inside functions so they can be called when the code will be generated.
def main(program_path: str, results_dir: str) -> None:
    # metrics, correct, error_msg = run_shinka_eval(
    run_shinka_eval(
        program_path=program_path,
        results_dir=results_dir,
        experiment_fn_name="tsp_problem",
        # This is the number the particular instance of the code will run. So ex. code from initial.py in generation 0 will run 5 times
        # This way we can in each different run provide a different city to test in a get_experiment_kwargs function.
        num_runs=len(CITIES),
        get_experiment_kwargs=get_experiment_kwargs,
        aggregate_metrics_fn=aggregate_metrics_fn,
        validate_fn=validate_fn,
    )
    # print("correct:", correct, "error:", error_msg)
    # print("metrics:", metrics)


# Shinka runs this file providing path for current generation program (our initial.py and its later evolutins)
# and path for result so this file can generate there a results.json and correct.json (Basically how good is program and if it's validate)
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evaluator for custom_problem")
    parser.add_argument("--program_path", type=str, required=True)
    parser.add_argument("--results_dir", type=str, required=True)
    args = parser.parse_args()
    main(args.program_path, args.results_dir)
