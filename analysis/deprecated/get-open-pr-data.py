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
DEFAULT_OUTPUT = Path("analysis/raw-data/posthog-open-prs.json")
DEFAULT_PER_PAGE = 100
API_BASE_URL = "https://api.github.com"
REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = REPO_ROOT / ".env"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch currently open pull requests from a GitHub repository.",
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
        "--max-pages",
        type=int,
        help="Optional safety limit on the number of pages to fetch.",
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


def write_output(output_path: Path, payload: dict[str, Any]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main() -> int:
    load_env_file(ENV_PATH)
    args = parse_args()

    if args.per_page < 1 or args.per_page > 100:
        print("--per-page must be between 1 and 100.", file=sys.stderr)
        return 1

    if args.max_pages is not None and args.max_pages < 1:
        print("--max-pages must be at least 1.", file=sys.stderr)
        return 1

    pull_requests: list[dict[str, Any]] = []

    try:
        page = 1
        while True:
            if args.max_pages is not None and page > args.max_pages:
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

            pull_requests.extend(page_pull_requests)

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

    payload = {
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": {
            "endpoint": f"/repos/{args.owner}/{args.repo}/pulls",
            "owner": args.owner,
            "repo": args.repo,
            "state": "open",
            "per_page": args.per_page,
            "max_pages": args.max_pages,
            "sort": args.sort,
            "direction": args.direction,
        },
        "pull_request_count": len(pull_requests),
        "pull_requests": pull_requests,
    }
    write_output(args.output, payload)

    print(f"Wrote {len(pull_requests)} open pull requests to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
