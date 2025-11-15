"""RAGAS evaluation script for RAG pipeline."""

import os
import sys
from pathlib import Path

import pandas as pd
from datasets import Dataset
from ragas import evaluate
from ragas.metrics import answer_accuracy, context_precision, context_recall

from .pipeline import run_rag

# Get project root directory (evals is at root level)
_project_root = Path(__file__).parent.parent
_data_dir = _project_root / "data"


def build_dataset(csv_path: str) -> Dataset:
    """
    Build evaluation dataset from CSV file.

    Args:
        csv_path: Path to CSV file with columns: question, reference_answer

    Returns:
        Dataset ready for RAGAS evaluation
    """
    df = pd.read_csv(csv_path)
    records = []

    for _, row in df.iterrows():
        question = row["question"]
        reference = row["reference_answer"]

        # Run RAG pipeline
        answer, contexts = run_rag(question)

        records.append(
            {
                "question": question,
                "answer": answer,
                "contexts": contexts,
                "ground_truth": reference,
            }
        )

    return Dataset.from_list(records)


def main():
    """Main evaluation function."""
    # Ensure data directory exists
    _data_dir.mkdir(exist_ok=True)

    # Input and output paths
    input_csv = _data_dir / "eval_questions.csv"
    output_csv = _data_dir / "eval_results.csv"

    if not input_csv.exists():
        print(f"Error: Evaluation questions file not found at {input_csv}")
        print("Please create data/eval_questions.csv with columns: question, reference_answer")
        sys.exit(1)

    print(f"Building dataset from {input_csv}...")
    ds = build_dataset(str(input_csv))

    print("Running RAGAS evaluation...")
    result = evaluate(
        ds,
        metrics=[answer_accuracy, context_precision, context_recall],
        # For production you should pass a Bedrock-backed Ragas LLM wrapper
        # but Ragas can also use its default if configured.
    )

    print("\nEvaluation Results:")
    print(result)

    # Save to CSV
    df = result.to_pandas()
    df.to_csv(output_csv, index=False)
    print(f"\nResults saved to {output_csv}")


if __name__ == "__main__":
    main()

