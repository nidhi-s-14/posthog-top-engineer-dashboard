#!/usr/bin/env python3

import argparse
import json
import re
from pathlib import Path
from typing import Any, Optional


DEFAULT_INPUT = Path("analysis/enriched-data/posthog-commits-enriched.json")
DEFAULT_OUTPUT = Path("analysis/cleaned-data/posthog-commits-clean.json")
PR_NUMBER_PATTERN = re.compile(r"\(#(\d+)\)")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reduce raw GitHub commit data to analysis-ready fields.",
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT,
        help="Path to the raw commit JSON file.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Path to the cleaned commit JSON file.",
    )
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def extract_pr_number(message: str) -> Optional[int]:
    match = PR_NUMBER_PATTERN.search(message)
    if not match:
        return None
    return int(match.group(1))


def clean_commit(commit: dict[str, Any]) -> dict[str, Any]:
    commit_meta = commit.get("commit", {})
    author_meta = commit_meta.get("author") or {}
    committer_meta = commit_meta.get("committer") or {}
    author = commit.get("author") or {}
    committer = commit.get("committer") or {}
    message = commit_meta.get("message", "")
    parents = commit.get("parents") or []
    enrichment = commit.get("enrichment") or {}
    pull_request = enrichment.get("pull_request") or {}

    return {
        "author_login": author.get("login"),
        "author_name": author_meta.get("name"),
        "committer_login": committer.get("login"),
        "committed_at": committer_meta.get("date"),
        "authored_at": author_meta.get("date"),
        "message": message,
        "pull_request_number": commit.get("pull_request_number") or extract_pr_number(message),
        "comment_count": commit_meta.get("comment_count", 0),
        "pull_request_comment_count": pull_request.get("comments"),
        "review_comment_count": pull_request.get("review_comment_count"),
        "review_count": pull_request.get("review_count"),
        "reviewers": pull_request.get("reviewers", []),
        "requested_reviewers": pull_request.get("requested_reviewers", []),
        "is_merge_commit": len(parents) > 1,
        "html_url": commit.get("html_url"),
        "pull_request_url": pull_request.get("url"),
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main() -> int:
    args = parse_args()
    raw_payload = read_json(args.input)
    raw_commits = raw_payload.get("commits", [])
    cleaned_commits = [clean_commit(commit) for commit in raw_commits]

    payload = {
        "cleaned_from": str(args.input),
        "fetched_at": raw_payload.get("fetched_at"),
        "source": raw_payload.get("source", {}),
        "commit_count": len(cleaned_commits),
        "commits": cleaned_commits,
    }
    write_json(args.output, payload)

    print(f"Wrote {len(cleaned_commits)} cleaned commits to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
