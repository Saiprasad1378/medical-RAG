"""evaluate.py — Run pipeline over test set and score with RAGAS."""

import json
from pathlib import Path

import pandas as pd
from datasets import Dataset
from ragas import evaluate as ragas_evaluate
from ragas.metrics import answer_relevancy, context_precision, context_recall, faithfulness

from rag_pipeline import MedicalRAG

TEST_FILE = Path("test_questions.json")
OUTPUT_CSV = Path("eval_results.csv")


def load_test_set() -> list:
    """Load the 30-question evaluation set."""
    with open(TEST_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def main() -> None:
    """Run RAG over all questions, compute RAGAS metrics, save results."""
    rag = MedicalRAG()
    if rag.kb_count == 0:
        print("❌ Knowledge base is empty. Run `python ingest.py` first.")
        return

    questions = load_test_set()
    rows = []
    print(f"Running pipeline on {len(questions)} questions...\n")
    for item in questions:
        result = rag.generate(item["question"])
        rows.append({
            "question": item["question"],
            "answer": result["answer"],
            "ground_truth": item["ground_truth"],
            "contexts": [d.page_content for d in rag.retrieve(item["question"], k=4)],
            "expected_source": item.get("expected_source", ""),
        })
        print(f"  ✓ {item['question'][:60]}... ({result['latency']}s)")

    ds = Dataset.from_list(rows)
    print("\nComputing RAGAS metrics (faithfulness, answer_relevancy, "
          "context_precision, context_recall)...\n")
    scores = ragas_evaluate(ds, metrics=[faithfulness, answer_relevancy,
                                         context_precision, context_recall])

    df = pd.DataFrame([scores.to_pandas().mean(numeric_only=True)])
    df["expected_sources_matched"] = sum(
        1 for r in rows if r["expected_source"] and r["expected_source"] in str(r["contexts"])
    )
    print("\n📊 SUMMARY")
    print(df.T.rename(columns={0: "score"}).to_string())
    df.to_csv(OUTPUT_CSV, index=False)
    print(f"\nSaved detailed results → {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
