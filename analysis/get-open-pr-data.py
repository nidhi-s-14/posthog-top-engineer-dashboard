#!/usr/bin/env python3

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_OWNER = "PostHog"
DEFAULT_REPO = "posthog"
DEFAULT_OUTPUT = Path("analysis/results/open-pr-summary.json")
DEFAULT_PER_PAGE = 100
API_BASE_URL = "https://api.github.com"
REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = REPO_ROOT / ".env"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch current open pull request info with minimal GitHub API usage.",
    )
    parser.add_argument("--owner", default=DEFAULT_OWNER, help="GitHub repo owner.")
    parser.add_argument("--repo", default=DEFAULT_REPO, help="GitHub repo name.")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Path to the JSON file to write.",
    )
    parser.add_argument(
        "--per-page",
        type=int,
        default=DEFAULT_PER_PAGE,
        help="Results per page. GitHub API allows up to 100.",
    )
    parser.add_argument(
        "--start-page",
        type=int,
        default=1,
        help="First page to fetch.",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        help="Optional number of pages to fetch starting from --start-page.",
    )
    parser.add_argument(
        "--sort",
        default="updated",
        choices=["created", "updated", "popularity", "long-running"],
        help="Pull request sort order.",
    )
    parser.add_argument(
        "--direction",
        default="desc",
        choices=["asc", "desc"],
        help="Sort direction.",
    )
    parser.add_argument(
        "--merge-output",
        action="store_true",
        help="Merge newly fetched pages into an existing output file instead of overwriting it.",
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
        "User-Agent": "posthog-top-engineer-dashboard-open-pr-fetcher",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def fetch_page(
    owner: str,
    repo: str,
    page: int,
    per_page: int,
    sort: str,
    direction: str,
) -> list[dict[str, Any]]:
    params = {
        "state": "open",
        "page": page,
        "per_page": per_page,
        "sort": sort,
        "direction": direction,
    }
    url = f"{API_BASE_URL}/repos/{owner}/{repo}/pulls?{urlencode(params)}"
    request = Request(url, headers=build_headers())

    with urlopen(request) as response:
        return json.load(response)


def simplify_pull_request(pull_request: dict[str, Any]) -> dict[str, Any]:
    user = pull_request.get("user") or {}
    assignees = pull_request.get("assignees") or []
    requested_reviewers = pull_request.get("requested_reviewers") or []
    labels = pull_request.get("labels") or []

    return {
        "number": pull_request.get("number"),
        "title": pull_request.get("title"),
        "author_login": user.get("login"),
        "created_at": pull_request.get("created_at"),
        "updated_at": pull_request.get("updated_at"),
        "draft": pull_request.get("draft"),
        "comments": pull_request.get("comments"),
        "review_comments": pull_request.get("review_comments"),
        "commits": pull_request.get("commits"),
        "additions": pull_request.get("additions"),
        "deletions": pull_request.get("deletions"),
        "changed_files": pull_request.get("changed_files"),
        "assignees": sorted(
            assignee.get("login") for assignee in assignees if assignee.get("login")
        ),
        "requested_reviewers": sorted(
            reviewer.get("login") for reviewer in requested_reviewers if reviewer.get("login")
        ),
        "labels": sorted(label.get("name") for label in labels if label.get("name")),
        "html_url": pull_request.get("html_url"),
    }


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_output_path(path: Path) -> Path:
    if path.is_absolute():
        return path
    return REPO_ROOT / path


def merge_pull_requests(existing: list[dict[str, Any]], new: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[int, dict[str, Any]] = {item["number"]: item for item in existing}
    for item in new:
        merged[item["number"]] = item
    return sorted(merged.values(), key=lambda item: item["number"])


def build_engineer_summary(pull_requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summary: dict[str, dict[str, Any]] = {}

    for pull_request in pull_requests:
        engineer = pull_request.get("author_login") or "unknown"
        stats = summary.setdefault(
            engineer,
            {
                "engineer": engineer,
                "open_pr_count": 0,
                "draft_pr_count": 0,
                "total_comments": 0,
                "total_review_comments": 0,
                "total_commits": 0,
            },
        )
        stats["open_pr_count"] += 1
        stats["draft_pr_count"] += 1 if pull_request.get("draft") else 0
        stats["total_comments"] += pull_request.get("comments") or 0
        stats["total_review_comments"] += pull_request.get("review_comments") or 0
        stats["total_commits"] += pull_request.get("commits") or 0

    return sorted(summary.values(), key=lambda item: item["open_pr_count"], reverse=True)


def write_output(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main() -> int:
    load_env_file(ENV_PATH)
    args = parse_args()
    output_path = resolve_output_path(args.output)

    if args.per_page < 1 or args.per_page > 100:
        print("--per-page must be between 1 and 100.", file=sys.stderr)
        return 1
    if args.start_page < 1:
        print("--start-page must be at least 1.", file=sys.stderr)
        return 1
    if args.max_pages is not None and args.max_pages < 1:
        print("--max-pages must be at least 1.", file=sys.stderr)
        return 1

    simplified_pull_requests: list[dict[str, Any]] = []

    try:
        page = args.start_page
        pages_fetched = 0

        while True:
            if args.max_pages is not None and pages_fetched >= args.max_pages:
                break

            page_pull_requests = fetch_page(
                owner=args.owner,
                repo=args.repo,
                page=page,
                per_page=args.per_page,
                sort=args.sort,
                direction=args.direction,
            )

            if not page_pull_requests:
                break

            simplified_pull_requests.extend(
                simplify_pull_request(pull_request) for pull_request in page_pull_requests
            )

            pages_fetched += 1

            if len(page_pull_requests) < args.per_page:
                break

            page += 1
            time.sleep(0.2)
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(f"GitHub API request failed with HTTP {exc.code}: {body}", file=sys.stderr)
        return 1
    except URLError as exc:
        print(f"Network error while calling GitHub API: {exc.reason}", file=sys.stderr)
        return 1

    if args.merge_output and output_path.exists():
        existing_payload = read_json(output_path)
        simplified_pull_requests = merge_pull_requests(
            existing_payload.get("pull_requests", []),
            simplified_pull_requests,
        )

    payload = {
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": {
            "endpoint": f"/repos/{args.owner}/{args.repo}/pulls",
            "owner": args.owner,
            "repo": args.repo,
            "state": "open",
            "per_page": args.per_page,
            "start_page": args.start_page,
            "max_pages": args.max_pages,
            "sort": args.sort,
            "direction": args.direction,
            "merge_output": args.merge_output,
        },
        "pull_request_count": len(simplified_pull_requests),
        "engineer_summary": build_engineer_summary(simplified_pull_requests),
        "pull_requests": simplified_pull_requests,
    }
    write_output(output_path, payload)

    print(f"Wrote {len(simplified_pull_requests)} open pull requests to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
