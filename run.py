import json
import subprocess
import sys
from pathlib import Path
from datetime import datetime

INPUT_FILE = "/home/olj3kor/praveen/Graph_RAG/gold_v1.0.json"
OUTPUT_FILE = "results.json"
LOG_FILE = "runner_output.log"

QUESTION_LIMIT = 'all'

KEEP_FIELDS = [
    "question",
    "answer",
    "reasoning",
    "evidence",
    "confidence",
    "run_at",
    "errors"
]


def write_log(message: str):
    """
    Append logs to a separate log file.
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with open(LOG_FILE, "a", encoding="utf-8") as log_f:
        log_f.write(f"\n{'=' * 100}\n")
        log_f.write(f"[{timestamp}]\n")
        log_f.write(message)
        log_f.write("\n")


def extract_json(stdout: str):
    """
    Extract JSON object from noisy stdout.
    Uses bracket-balanced scanning so nested blobs (e.g. path_steps)
    don't cause rfind to overshoot the real closing brace.
    After parsing, drops the 'path_steps' key entirely.

    The stdout is full of log lines like:
        13:37:47  WARNING  [neo4j...] ... diagnostic_record={'_class': ...}
    Those log lines may contain '{' characters (Python repr dicts), so we
    skip every line that looks like a log line (HH:MM:SS prefix) and only
    consider a '{' that appears at the start of a line by itself.
    """

    stdout = stdout.strip()

    # Try direct parsing first
    try:
        result = json.loads(stdout)
        result.pop("path_steps", None)
        return result

    except json.JSONDecodeError:
        pass

    # Find the first '{' that starts a line (i.e. the real JSON block),
    # skipping any '{' that appears inside a log line.
    start = -1
    for line_start, line in (
        (sum(len(l) + 1 for l in stdout.splitlines()[:i]), l)
        for i, l in enumerate(stdout.splitlines())
    ):
        stripped = line.strip()
        if stripped.startswith("{"):
            # Confirm this isn't a log line (log lines start with HH:MM:SS)
            import re as _re
            if not _re.match(r'\d{2}:\d{2}:\d{2}', line.lstrip()):
                start = stdout.index("{", line_start)
                break

    if start == -1:
        raise json.JSONDecodeError(
            "No valid JSON object found",
            stdout,
            0
        )

    depth = 0
    in_string = False
    escape_next = False
    end = -1

    for i, ch in enumerate(stdout[start:], start=start):
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i
                break

    if end == -1:
        raise json.JSONDecodeError(
            "No balanced JSON object found",
            stdout,
            0
        )

    json_text = stdout[start:end + 1]

    try:
        result = json.loads(json_text)
    except json.JSONDecodeError:
        preview = json_text[:500].replace("\n", "\\n")
        raise json.JSONDecodeError(
            f"Balanced-scan slice (first 500 chars): {preview}",
            json_text,
            0
        )

    result.pop("path_steps", None)
    return result


def run_query(query: str):

    try:

        result = subprocess.run(
            [sys.executable, "asei_runner.py", "ask", query],
            capture_output=True,
            text=True,
            check=True
        )

        # Save raw runner output to log file
        write_log(
            f"QUERY:\n{query}\n\n"
            f"STDOUT:\n{result.stdout}\n\n"
            f"STDERR:\n{result.stderr}"
        )

        response_json = extract_json(result.stdout)

        if not isinstance(response_json, dict):

            return {
                "question": query,
                "answer": None,
                "reasoning": None,
                "evidence": [],
                "confidence": 0,
                "run_at": None,
                "errors": [
                    "Runner did not return a JSON object"
                ]
            }

        # Keep only selected fields
        filtered = {
            key: response_json.get(key)
            for key in KEEP_FIELDS
        }

        # Ensure structure consistency
        filtered["question"] = filtered.get("question") or query
        filtered["answer"] = filtered.get("answer")
        filtered["reasoning"] = filtered.get("reasoning")
        filtered["evidence"] = filtered.get("evidence") or []
        filtered["confidence"] = filtered.get("confidence", 0)
        filtered["run_at"] = filtered.get("run_at")
        filtered["errors"] = filtered.get("errors") or []

        return filtered

    except subprocess.CalledProcessError as e:

        write_log(
            f"QUERY:\n{query}\n\n"
            f"SUBPROCESS ERROR\n\n"
            f"STDOUT:\n{e.stdout}\n\n"
            f"STDERR:\n{e.stderr}"
        )

        return {
            "question": query,
            "answer": None,
            "reasoning": None,
            "evidence": [],
            "confidence": 0,
            "run_at": None,
            "errors": [
                f"Subprocess failed: {e}"
            ]
        }

    except json.JSONDecodeError as e:

        write_log(
            f"QUERY:\n{query}\n\n"
            f"JSON PARSE ERROR\n\n"
            f"ERROR:\n{str(e)}"
        )

        return {
            "question": query,
            "answer": None,
            "reasoning": None,
            "evidence": [],
            "confidence": 0,
            "run_at": None,
            "errors": [
                "Invalid JSON returned",
                str(e)
            ]
        }

    except Exception as e:

        write_log(
            f"QUERY:\n{query}\n\n"
            f"UNEXPECTED ERROR\n\n"
            f"ERROR:\n{str(e)}"
        )

        return {
            "question": query,
            "answer": None,
            "reasoning": None,
            "evidence": [],
            "confidence": 0,
            "run_at": None,
            "errors": [
                str(e)
            ]
        }


def main():

    # Clear old log file
    Path(LOG_FILE).write_text("", encoding="utf-8")

    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    total_queries = len(data)

    print(f"\nTotal queries available: {total_queries}")

    if QUESTION_LIMIT == "all":

        selected_data = data

    else:

        try:
            limit = int(QUESTION_LIMIT)

            if limit <= 0:
                print("Please enter a positive number.")
                return

            selected_data = data[:limit]

        except ValueError:
            print("Invalid input.")
            return

    all_results = []

    for idx, item in enumerate(selected_data, start=1):

        query = (
            item.get("question")
            or item.get("user_input")
            or ""
        ).strip()

        if not query:
            print(f"Skipping item {idx}: no question found")
            continue

        print("=" * 80)
        print(f"Running Query {idx}/{len(selected_data)}")
        print("=" * 80)
        print(query)
        print()

        output = run_query(query)

        all_results.append(output)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:

        json.dump(
            all_results,
            f,
            indent=2,
            ensure_ascii=False
        )

    print(f"\nSaved {len(all_results)} results to {OUTPUT_FILE}")
    print(f"Raw runner logs saved to {LOG_FILE}")


if __name__ == "__main__":
    main()