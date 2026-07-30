import argparse
import time

from shinka.core import run_shinka_eval

# shinka_run only copies evaluate.py into results_dir (not sibling files), so
# every helper this script needs must live in this one file.

OPTIMAL_DISTANCES = [
    80.0,
    26.0,
]

CITY_0 = [
    [0, 10, 15, 20],
    [10, 0, 35, 25],
    [15, 35, 0, 30],
    [20, 25, 30, 0],
]

CITY_1 = [
    [0, 2, 9, 10, 7],
    [2, 0, 6, 4, 3],
    [9, 6, 0, 8, 5],
    [10, 4, 8, 0, 6],
    [7, 3, 5, 6, 0],
]

CITIES = [
    CITY_0,
    CITY_1,
]


def calculate_relative_error_score(
    result: tuple[list[int], float, float],
    optimal_distance: float,
) -> float:

    total_distance = result[1]

    relative_error = abs(total_distance - optimal_distance) / optimal_distance

    score = max(0.0, 1.0 - relative_error)

    return score


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


# Here we validate our program. In our case we check if the algorithm visits each city exactly once and every city was visited
# It's True and None when everything is ok and False, "Explaining why was it invalid" when it's not
def validate_fn(
    result: tuple[list[int], float, float],
) -> tuple[bool, str | None]:
    tour = result[0]

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

    return True, None


def evaluate_run(run_idx, result):
    t_pen = calculate_efficiency_penalty(result, run_idx)
    c_pen = calculate_complexity_penalty(result)
    r_err = calculate_relative_error_score(result, OPTIMAL_DISTANCES[run_idx])
    score = r_err * (1 - (0.3 * t_pen + 0.7 * c_pen))
    return score, r_err, t_pen, c_pen


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
        relative_error_scores,
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
            "relative_error_scores": relative_error_scores,
            "time_penalties": time_penalties,
            "complexity_penalties": complexity_penalties,
        },
        # It's a small object with data stored in metrics.json, metrics are for us, not LLM
        # We put here anything we want to track
        "private": {
            "relative_error_scores": relative_error_scores,
        },
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
        # This is the number the particular instance of the code will run. So ex. code from initial.py in generation 0 will run 3 times
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
