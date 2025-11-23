# evals/cli.py

import argparse
import asyncio
from .pipeline import run_evaluation
from src.utils.config import read_config


def parse_args():
    parser = argparse.ArgumentParser(description="RAG evaluation runner")
    parser.add_argument(
        "--config",
        type=str,
        required=True,
        help="Path to evals_config.yaml"
    )
    parser.add_argument(
        "--output-type",
        type=str,
        default=None,
        help="Comma-separated list of outputs to generate (html,json,csv). "
             "Overrides evals_config.outputs.types if provided."
    )
    parser.add_argument(
        "--run-judge-validation",
        action="store_true",
        help="If set, run judge-validation in addition to main eval."
    )
    return parser.parse_args()


def main():
    args = parse_args()
    config = read_config(args.config)
    
    # Apply defaults (previously in load_config)
    config.setdefault("outputs", {})
    config["outputs"].setdefault("types", ["html", "json", "csv"])
    config.setdefault("run", {})
    config["run"].setdefault("max_concurrency", 10)
    
    # CLI override for output types
    if args.output_type:
        config["outputs"]["types"] = [
            s.strip() for s in args.output_type.split(",") if s.strip()
        ]
    
    asyncio.run(
        run_evaluation(
            config=config,
            run_judge_validation=args.run_judge_validation,
        )
    )


if __name__ == "__main__":
    main()

