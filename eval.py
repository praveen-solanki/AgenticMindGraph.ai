"""
Evaluation Script
-----------------
Inputs:
  - GT file    : fields used -> user_input, reference, reference_contexts
  - Other file : fields used -> question, answer, reasoning

Metrics computed:
  1. ROUGE-1, ROUGE-2, ROUGE-L      (answer vs reference)
  2. BERTScore                       (answer vs reference)
  3. LLM-as-judge: Answer Correctness (answer vs reference)
  4. LLM-as-judge: Reasoning Validity (reasoning vs reference)
  5. LLM-as-judge: Faithfulness       (answer vs reference_contexts)
  6. LLM-as-judge: Context Recall     (reasoning vs reference_contexts)

vLLM server:
  Model : Qwen/Qwen2.5-32B-Instruct-AWQ
  Port  : 8011
"""

import json
import argparse
import time
from typing import Optional
from openai import OpenAI
from rouge_score import rouge_scorer
from bert_score import score as bert_score_fn

# ---------------------------------------------------------------------------
# vLLM client (OpenAI-compatible)
# ---------------------------------------------------------------------------
VLLM_BASE_URL = "http://localhost:8011/v1"
VLLM_MODEL    = "Qwen/Qwen2.5-32B-Instruct-AWQ"

client = OpenAI(
    base_url=VLLM_BASE_URL,
    api_key="token-abc123",          # vllm accepts any non-empty string
)


def call_llm(system_prompt: str, user_prompt: str, max_tokens: int = 512) -> str:
    """Call the vLLM server and return the response text."""
    for attempt in range(3):
        try:
            response = client.chat.completions.create(
                model=VLLM_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_prompt},
                ],
                max_tokens=max_tokens,
                temperature=0.0,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            print(f"  [LLM call failed attempt {attempt+1}]: {e}")
            time.sleep(2)
    return ""


# ---------------------------------------------------------------------------
# LLM-as-judge prompts
# ---------------------------------------------------------------------------

def judge_answer_correctness(answer: str, reference: str) -> dict:
    """Score how correct and complete the answer is vs the GT reference."""
    system = (
        "You are an expert evaluator. Given a predicted answer and a reference answer, "
        "score the predicted answer on two dimensions:\n"
        "1. Correctness  (0-5): Is the predicted answer factually correct w.r.t. the reference?\n"
        "2. Completeness (0-5): Does it cover all key points in the reference?\n\n"
        "Respond ONLY in this exact JSON format (no extra text):\n"
        '{"correctness": <int>, "completeness": <int>, "reason": "<one line>"}'
    )
    user = (
        f"Reference Answer:\n{reference}\n\n"
        f"Predicted Answer:\n{answer}"
    )
    raw = call_llm(system, user)
    try:
        return json.loads(raw)
    except Exception:
        return {"correctness": None, "completeness": None, "reason": raw}


def judge_reasoning_validity(reasoning: str, reference: str) -> dict:
    """Score whether the reasoning logically supports the GT reference answer."""
    system = (
        "You are an expert evaluator. Given a chain-of-thought reasoning and a reference answer, "
        "score the reasoning on:\n"
        "1. Logical Validity (0-5): Does the reasoning logically lead to the reference answer?\n"
        "2. Relevance        (0-5): Is the reasoning relevant to the reference answer?\n\n"
        "Respond ONLY in this exact JSON format (no extra text):\n"
        '{"logical_validity": <int>, "relevance": <int>, "reason": "<one line>"}'
    )
    user = (
        f"Reference Answer:\n{reference}\n\n"
        f"Reasoning:\n{reasoning}"
    )
    raw = call_llm(system, user)
    try:
        return json.loads(raw)
    except Exception:
        return {"logical_validity": None, "relevance": None, "reason": raw}


def judge_faithfulness(answer: str, reference_contexts: list) -> dict:
    """Score whether the answer is grounded in the GT reference contexts."""
    contexts_str = "\n\n---\n\n".join(reference_contexts)
    system = (
        "You are an expert evaluator. Given a predicted answer and a set of reference contexts, "
        "score the answer on:\n"
        "1. Faithfulness (0-5): Is every claim in the answer supported by the reference contexts?\n\n"
        "Respond ONLY in this exact JSON format (no extra text):\n"
        '{"faithfulness": <int>, "reason": "<one line>"}'
    )
    user = (
        f"Reference Contexts:\n{contexts_str}\n\n"
        f"Predicted Answer:\n{answer}"
    )
    raw = call_llm(system, user)
    try:
        return json.loads(raw)
    except Exception:
        return {"faithfulness": None, "reason": raw}


def judge_context_recall(reasoning: str, reference_contexts: list) -> dict:
    """Score whether the reasoning uses information present in the GT contexts."""
    contexts_str = "\n\n---\n\n".join(reference_contexts)
    system = (
        "You are an expert evaluator. Given a chain-of-thought reasoning and a set of reference contexts, "
        "score the reasoning on:\n"
        "1. Context Recall (0-5): Does the reasoning use / cite information present in the reference contexts?\n\n"
        "Respond ONLY in this exact JSON format (no extra text):\n"
        '{"context_recall": <int>, "reason": "<one line>"}'
    )
    user = (
        f"Reference Contexts:\n{contexts_str}\n\n"
        f"Reasoning:\n{reasoning}"
    )
    raw = call_llm(system, user)
    try:
        return json.loads(raw)
    except Exception:
        return {"context_recall": None, "reason": raw}


# ---------------------------------------------------------------------------
# ROUGE
# ---------------------------------------------------------------------------

def compute_rouge(prediction: str, reference: str) -> dict:
    scorer = rouge_scorer.RougeScorer(["rouge1", "rouge2", "rougeL"], use_stemmer=True)
    scores = scorer.score(reference, prediction)
    return {
        "rouge1_f": round(scores["rouge1"].fmeasure, 4),
        "rouge2_f": round(scores["rouge2"].fmeasure, 4),
        "rougeL_f": round(scores["rougeL"].fmeasure, 4),
    }


# ---------------------------------------------------------------------------
# BERTScore
# ---------------------------------------------------------------------------

def compute_bertscore(predictions: list, references: list) -> list:
    """Batch BERTScore computation. Returns list of F1 scores."""
    P, R, F1 = bert_score_fn(
        predictions,
        references,
        lang="en",
        rescale_with_baseline=True,
        verbose=False,
    )
    return [round(f.item(), 4) for f in F1]


# ---------------------------------------------------------------------------
# Matching: join input file to GT by question == user_input
# ---------------------------------------------------------------------------

def match_records(gt_records: list, input_records: list) -> list:
    """Match input records to GT records by question string."""
    gt_map = {rec["user_input"].strip(): rec for rec in gt_records}
    matched = []
    unmatched = 0
    for rec in input_records:
        question = rec.get("question", "").strip()
        gt = gt_map.get(question)
        if gt is None:
            print(f"  [WARNING] No GT match found for question: {question[:80]}...")
            unmatched += 1
            continue
        matched.append({"input": rec, "gt": gt})
    print(f"  Matched: {len(matched)} | Unmatched: {unmatched}")
    return matched


# ---------------------------------------------------------------------------
# Main evaluation
# ---------------------------------------------------------------------------

def evaluate(gt_path: str, input_path: str, output_path: str):
    print(f"\n{'='*60}")
    print(f"GT file    : {gt_path}")
    print(f"Input file : {input_path}")
    print(f"Output     : {output_path}")
    print(f"{'='*60}\n")

    # Load files
    with open(gt_path, "r") as f:
        gt_records = json.load(f)
    with open(input_path, "r") as f:
        input_records = json.load(f)

    # Filter input records with errors
    clean_inputs = [r for r in input_records if not r.get("errors")]
    skipped = len(input_records) - len(clean_inputs)
    if skipped:
        print(f"  Skipped {skipped} input records due to errors.\n")

    # Match records
    print("Matching records by question == user_input ...")
    matched = match_records(gt_records, clean_inputs)

    if not matched:
        print("No matched records found. Exiting.")
        return

    # Collect predictions and references for batch BERTScore
    predictions = [m["input"]["answer"] for m in matched]
    references  = [m["gt"]["reference"]  for m in matched]

    print("\nComputing BERTScore (batch) ...")
    bert_scores = compute_bertscore(predictions, references)

    results = []
    for i, m in enumerate(matched):
        inp = m["input"]
        gt  = m["gt"]

        question           = inp.get("question", "")
        answer             = inp.get("answer", "")
        reasoning          = inp.get("reasoning", "")
        reference          = gt.get("reference", "")
        reference_contexts = gt.get("reference_contexts", [])

        print(f"\n[{i+1}/{len(matched)}] Evaluating: {question[:70]}...")

        # 1. ROUGE
        print("  Computing ROUGE ...")
        rouge = compute_rouge(answer, reference)

        # 2. BERTScore (already computed in batch)
        bertscore_f1 = bert_scores[i]

        # 3. LLM-as-judge: Answer Correctness
        print("  LLM judge: Answer Correctness ...")
        answer_correctness = judge_answer_correctness(answer, reference)

        # 4. LLM-as-judge: Reasoning Validity
        print("  LLM judge: Reasoning Validity ...")
        reasoning_validity = judge_reasoning_validity(reasoning, reference)

        # 5. LLM-as-judge: Faithfulness
        print("  LLM judge: Faithfulness ...")
        faithfulness = judge_faithfulness(answer, reference_contexts)

        # 6. LLM-as-judge: Context Recall
        print("  LLM judge: Context Recall ...")
        context_recall = judge_context_recall(reasoning, reference_contexts)

        result = {
            "id"                  : gt.get("id", ""),
            "question"            : question,
            # --- ROUGE ---
            "rouge1_f"            : rouge["rouge1_f"],
            "rouge2_f"            : rouge["rouge2_f"],
            "rougeL_f"            : rouge["rougeL_f"],
            # --- BERTScore ---
            "bertscore_f1"        : bertscore_f1,
            # --- LLM judges ---
            "answer_correctness"  : answer_correctness,
            "reasoning_validity"  : reasoning_validity,
            "faithfulness"        : faithfulness,
            "context_recall"      : context_recall,
        }
        results.append(result)
        print(f"  ROUGE-L={rouge['rougeL_f']} | BERTScore={bertscore_f1} | "
              f"Correctness={answer_correctness.get('correctness')} | "
              f"Faithfulness={faithfulness.get('faithfulness')}")

    # Summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")

    def safe_avg(key, subkey=None):
        vals = []
        for r in results:
            v = r[key] if subkey is None else r[key].get(subkey)
            if v is not None:
                try:
                    vals.append(float(v))
                except Exception:
                    pass
        return round(sum(vals) / len(vals), 4) if vals else None

    print(f"  Avg ROUGE-1        : {safe_avg('rouge1_f')}")
    print(f"  Avg ROUGE-2        : {safe_avg('rouge2_f')}")
    print(f"  Avg ROUGE-L        : {safe_avg('rougeL_f')}")
    print(f"  Avg BERTScore F1   : {safe_avg('bertscore_f1')}")
    print(f"  Avg Correctness    : {safe_avg('answer_correctness',  'correctness')}")
    print(f"  Avg Completeness   : {safe_avg('answer_correctness',  'completeness')}")
    print(f"  Avg Logic Validity : {safe_avg('reasoning_validity',  'logical_validity')}")
    print(f"  Avg Relevance      : {safe_avg('reasoning_validity',  'relevance')}")
    print(f"  Avg Faithfulness   : {safe_avg('faithfulness',        'faithfulness')}")
    print(f"  Avg Context Recall : {safe_avg('context_recall',      'context_recall')}")

    # Save results
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to: {output_path}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evaluate LLM outputs against GT.")
    parser.add_argument("--gt",     required=True,  help="Path to GT JSON file")
    parser.add_argument("--input",  required=True,  help="Path to input (predicted) JSON file")
    parser.add_argument("--output", default="evaluation_results.json", help="Path to save results JSON")
    args = parser.parse_args()

    evaluate(
        gt_path    = args.gt,
        input_path = args.input,
        output_path= args.output,
    )