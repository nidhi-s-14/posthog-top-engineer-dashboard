#!/usr/bin/env python3

import argparse
import json
from pathlib import Path
from typing import Any


DEFAULT_INPUT = Path("analysis/cleaned-data/posthog-commits-clean.json")
DEFAULT_OUTPUT = Path("analysis/results/analysis-summary.json")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run analyses on cleaned GitHub contribution data.",
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT,
        help="Path to the cleaned data JSON file.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Path to the analysis summary JSON file.",
    )
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def normalize_engineer(commit: dict[str, Any]) -> str:
    return commit.get("author_login") or commit.get("author_name") or "unknown"


def build_unique_pr_index(commits: list[dict[str, Any]]) -> dict[tuple[str, int], dict[str, Any]]:
    pr_index: dict[tuple[str, int], dict[str, Any]] = {}

    for commit in commits:
        pr_number = commit.get("pull_request_number")
        if pr_number is None:
            continue

        engineer = normalize_engineer(commit)
        key = (engineer, pr_number)
        if key not in pr_index:
            pr_index[key] = {
                "engineer": engineer,
                "pr_number": pr_number,
                "pull_request_url": commit.get("pull_request_url"),
                "html_url": commit.get("html_url"),
            }

    return pr_index


def analyze_pr_merge_ratio(commits: list[dict[str, Any]]) -> dict[str, Any]:
    pr_index = build_unique_pr_index(commits)
    merged_counts: dict[str, int] = {}

    for engineer, _ in pr_index.keys():
        merged_counts[engineer] = merged_counts.get(engineer, 0) + 1

    engineers = []
    for engineer in sorted(merged_counts):
        engineers.append(
            {
                "engineer": engineer,
                "opened_pr_count": None,
                "merged_pr_count": merged_counts[engineer],
                "merge_ratio": None,
                "notes": [
                    "Opened PR count is unavailable in the current commit-derived dataset.",
                    "Merged PR count is based on unique pull request numbers found in merged commits.",
                ],
            }
        )

    return {
        "analysis": "pr_merge_ratio",
        "description": "Per-engineer PR opened-to-merged ratio.",
        "status": "partial",
        "notes": [
            "A true PR merge ratio requires a pull request dataset that includes all opened PRs in the time window.",
            "The current pipeline starts from commits, so it only exposes PRs that resulted in commits on the branch being analyzed.",
        ],
        "engineers": engineers,
    }


def run_analyses(payload: dict[str, Any]) -> dict[str, Any]:
    commits = payload.get("commits", [])

    return {
        "source": {
            "input": payload.get("cleaned_from"),
            "fetched_at": payload.get("fetched_at"),
            "commit_count": payload.get("commit_count"),
        },
        "analyses": [
            analyze_pr_merge_ratio(commits),
        ],
    }


def main() -> int:
    args = parse_args()
    payload = read_json(args.input)
    analysis_summary = run_analyses(payload)
    write_json(args.output, analysis_summary)
    print(f"Wrote analysis results to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
