import argparse
from shinka.core import run_shinka_eval

# city is gonna be a matrix
CITIES = [
    "test city 1",
    "test city 2",
    "test city 3",
]

# here we provide the arguments to a generated program in each run for specific generation code, more explanation in def main()
def get_experiment_kwargs(run_idx: int) -> dict[str, object]:
    return {"dist_matrix": CITIES[run_idx]}


# Here we validate our program. In our case we check if the algorithm visits each city exactly once and every city was visited
# It's True and None when everything is ok and False, "Explaining why was it invalid" when it's not
def validate_fn(result: float | int) -> tuple[bool, str | None]:
    if not isinstance(result, (int, float)):
        return False, f"Expected a numeric result, got {type(result)}"
    return True, None


# This is the most important function. Here we write the test to generate the score for current program.
def aggregate_metrics_fn(results: list[float]) -> dict[str, object]:
    this_generation_score = 0

    # place for tests
    # 1. We have to compare the cost of a path to a already solved solution from the internet (cities for the solutions are put into CITIES
        # relative error

    # 2. We have to add time measurment in the initial.py function so it can be returned and then we have to somehow calculate score based on that time
        # There's not a ready solution. We have to come up with a way it combines with lenghts to create a final score.
        # I propose creating another file with a simple "base" algorithm that will run in this test current city and then compare
        # "base time" with the time it took the llm code to run the city

    # 3. We somehow have to test O(n) complexity of the code.
        # I think we can achieve it via time returned from inital.py

    # proposed score
    # combined_score = quality_score * (1 - (0.3 * time_penalty + 0.7 * complexity_penalty))

    return {
        # This is most important metric for code we are testing. We need to somehow create a score
        # that's between 0 and 1. The score is based on all the results from current generation.
        # We can change our scale if we want, it can be to be honest ANY number if its easier for us to generate it that way
        "combined_score":  this_generation_score, 

        # This is what LLM sees as a feedback. We provide data that will help LLM decide what has to be improved
        # besides just a score
        "public": {}, 

        # It's a small object with data stored in metrics.json, metrics are for us, not LLM
        # We put here anything we want to track
        "private": {}, 

        "extra_data": {}, # A little different "private", we don't use it
        "text_feedback": "", # must be enabled, we don't use it
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
        num_runs=3,
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