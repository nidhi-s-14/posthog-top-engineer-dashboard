#!/usr/bin/env python3

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_INPUT = Path("analysis/raw-data/posthog-commits.json")
DEFAULT_OUTPUT = Path("analysis/enriched-data/posthog-commits-enriched.json")
API_BASE_URL = "https://api.github.com"
REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = REPO_ROOT / ".env"
PR_NUMBER_PATTERN = re.compile(r"\(#(\d+)\)")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Enrich raw commit data with pull request review metadata.",
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
        help="Path to the enriched commit JSON file.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="Number of parallel pull request enrichments to run.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        help="Number of unique pull requests to enrich in this run.",
    )
    parser.add_argument(
        "--batch-index",
        type=int,
        default=0,
        help="Zero-based batch index to run when --batch-size is set.",
    )
    return parser.parse_args()


def load_env_file(env_path: Path) -> None:
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')

        if key and key not in os.environ:
            os.environ[key] = value


def build_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "posthog-top-engineer-dashboard-enricher",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def github_get(url: str) -> Any:
    request = Request(url, headers=build_headers())
    with urlopen(request) as response:
        return json.load(response)


def extract_pr_number(message: str) -> Optional[int]:
    match = PR_NUMBER_PATTERN.search(message or "")
    if not match:
        return None
    return int(match.group(1))


def fetch_pull_request(owner: str, repo: str, pr_number: int) -> dict[str, Any]:
    return github_get(f"{API_BASE_URL}/repos/{owner}/{repo}/pulls/{pr_number}")


def fetch_pull_request_reviews(owner: str, repo: str, pr_number: int) -> list[dict[str, Any]]:
    return github_get(f"{API_BASE_URL}/repos/{owner}/{repo}/pulls/{pr_number}/reviews")

def summarize_reviews(reviews: list[dict[str, Any]]) -> list[dict[str, Any]]:
    reviewers: dict[str, dict[str, Any]] = {}

    for review in reviews:
        user = review.get("user") or {}
        login = user.get("login")
        if not login:
            continue

        existing = reviewers.get(login)
        summary = {
            "login": login,
            "state": review.get("state"),
            "submitted_at": review.get("submitted_at"),
        }
        if existing is None or (summary["submitted_at"] or "") > (existing.get("submitted_at") or ""):
            reviewers[login] = summary

    return sorted(reviewers.values(), key=lambda reviewer: reviewer["login"])


def build_pr_enrichment(
    owner: str,
    repo: str,
    pr_number: int,
) -> dict[str, Any]:
    pull_request = fetch_pull_request(owner, repo, pr_number)
    reviews = fetch_pull_request_reviews(owner, repo, pr_number)

    return {
        "pull_request": {
            "number": pr_number,
            "title": pull_request.get("title"),
            "state": pull_request.get("state"),
            "created_at": pull_request.get("created_at"),
            "merged_at": pull_request.get("merged_at"),
            "comments": pull_request.get("comments"),
            "review_comments": pull_request.get("review_comments"),
            "requested_reviewers": sorted(
                reviewer.get("login")
                for reviewer in pull_request.get("requested_reviewers", [])
                if reviewer.get("login")
            ),
            "reviewers": summarize_reviews(reviews),
            "review_count": len(reviews),
            "review_comment_count": pull_request.get("review_comments"),
            "url": pull_request.get("html_url"),
        }
    }


def fetch_all_pr_enrichments(
    owner: str,
    repo: str,
    pr_numbers: list[int],
    workers: int,
) -> dict[int, dict[str, Any]]:
    pr_cache: dict[int, dict[str, Any]] = {}
    max_workers = max(1, workers)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_pr = {
            executor.submit(build_pr_enrichment, owner, repo, pr_number): pr_number
            for pr_number in pr_numbers
        }

        for future in as_completed(future_to_pr):
            pr_number = future_to_pr[future]
            pr_cache[pr_number] = future.result()

    return pr_cache


def load_existing_pr_cache(path: Path) -> dict[int, dict[str, Any]]:
    if not path.exists():
        return {}

    payload = read_json(path)
    cache: dict[int, dict[str, Any]] = {}
    for commit in payload.get("commits", []):
        pr_number = commit.get("pull_request_number")
        enrichment = commit.get("enrichment")
        if pr_number is None or not enrichment:
            continue
        cache[pr_number] = enrichment
    return cache


def select_batch(pr_numbers: list[int], batch_size: Optional[int], batch_index: int) -> list[int]:
    if batch_size is None:
        return pr_numbers

    start = batch_index * batch_size
    end = start + batch_size
    return pr_numbers[start:end]


def main() -> int:
    load_env_file(ENV_PATH)
    args = parse_args()
    raw_payload = read_json(args.input)
    source = raw_payload.get("source", {})
    owner = source.get("owner")
    repo = source.get("repo")

    if not owner or not repo:
        print("Input payload is missing source.owner or source.repo.", file=sys.stderr)
        return 1
    if args.workers < 1:
        print("--workers must be at least 1.", file=sys.stderr)
        return 1
    if args.batch_size is not None and args.batch_size < 1:
        print("--batch-size must be at least 1.", file=sys.stderr)
        return 1
    if args.batch_index < 0:
        print("--batch-index must be at least 0.", file=sys.stderr)
        return 1

    commits = raw_payload.get("commits", [])
    enriched_commits: list[dict[str, Any]] = []
    pr_numbers = sorted(
        {
            extract_pr_number((commit.get("commit") or {}).get("message", ""))
            for commit in commits
        }
        - {None}
    )
    existing_pr_cache = load_existing_pr_cache(args.output)
    batch_pr_numbers = select_batch(pr_numbers, args.batch_size, args.batch_index)
    missing_batch_pr_numbers = [
        pr_number for pr_number in batch_pr_numbers if pr_number not in existing_pr_cache
    ]

    try:
        pr_cache = dict(existing_pr_cache)
        if missing_batch_pr_numbers:
            pr_cache.update(
                fetch_all_pr_enrichments(
                    owner=owner,
                    repo=repo,
                    pr_numbers=missing_batch_pr_numbers,
                    workers=args.workers,
                )
            )

        for commit in commits:
            commit_meta = commit.get("commit", {})
            pr_number = extract_pr_number(commit_meta.get("message", ""))
            enriched_commit = dict(commit)
            enriched_commit["pull_request_number"] = pr_number
            if pr_number is not None and pr_number in pr_cache:
                enriched_commit["enrichment"] = pr_cache[pr_number]
            enriched_commits.append(enriched_commit)
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(f"GitHub API request failed with HTTP {exc.code}: {body}", file=sys.stderr)
        return 1
    except URLError as exc:
        print(f"Network error while calling GitHub API: {exc.reason}", file=sys.stderr)
        return 1

    payload = {
        "enriched_from": str(args.input),
        "fetched_at": raw_payload.get("fetched_at"),
        "enriched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": source,
        "commit_count": len(enriched_commits),
        "enriched_pull_request_count": len(pr_cache),
        "total_pull_request_count": len(pr_numbers),
        "batch": {
            "batch_size": args.batch_size,
            "batch_index": args.batch_index,
            "selected_pull_request_count": len(batch_pr_numbers),
            "newly_enriched_pull_request_count": len(missing_batch_pr_numbers),
        },
        "commits": enriched_commits,
    }
    write_json(args.output, payload)

    print(
        f"Wrote {len(enriched_commits)} commits with {len(pr_cache)}/{len(pr_numbers)} pull requests enriched to {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
