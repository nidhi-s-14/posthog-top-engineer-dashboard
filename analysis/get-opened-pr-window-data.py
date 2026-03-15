#!/usr/bin/env python3

import argparse
from datetime import datetime, timedelta, timezone
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
DEFAULT_OUTPUT = Path("analysis/results/opened-pr-window-summary.json")
DEFAULT_PER_PAGE = 100
DEFAULT_DAYS_BACK = 90
API_BASE_URL = "https://api.github.com"
REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = REPO_ROOT / ".env"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch pull requests opened in the last N days with minimal GitHub API usage.",
    )
    parser.add_argument("--owner", default=DEFAULT_OWNER, help="GitHub repo owner.")
    parser.add_argument("--repo", default=DEFAULT_REPO, help="GitHub repo name.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Output JSON path.")
    parser.add_argument(
        "--per-page",
        type=int,
        default=DEFAULT_PER_PAGE,
        help="Results per page. GitHub API allows up to 100.",
    )
    parser.add_argument(
        "--days-back",
        type=int,
        default=DEFAULT_DAYS_BACK,
        help="Keep PRs created in the last N days.",
    )
    parser.add_argument("--start-page", type=int, default=1, help="First page to fetch.")
    parser.add_argument(
        "--max-pages",
        type=int,
        help="Optional number of pages to fetch starting from --start-page.",
    )
    parser.add_argument(
        "--merge-output",
        action="store_true",
        help="Merge fetched pages into an existing output file instead of overwriting it.",
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
        "User-Agent": "posthog-top-engineer-dashboard-opened-pr-window-fetcher",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def resolve_output_path(path: Path) -> Path:
    if path.is_absolute():
        return path
    return REPO_ROOT / path


def isoformat_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def fetch_page(owner: str, repo: str, page: int, per_page: int) -> list[dict[str, Any]]:
    params = {
        "state": "all",
        "sort": "created",
        "direction": "desc",
        "page": page,
        "per_page": per_page,
    }
    url = f"{API_BASE_URL}/repos/{owner}/{repo}/pulls?{urlencode(params)}"
    request = Request(url, headers=build_headers())
    with urlopen(request) as response:
        return json.load(response)


def simplify_pull_request(pull_request: dict[str, Any]) -> dict[str, Any]:
    user = pull_request.get("user") or {}
    return {
        "number": pull_request.get("number"),
        "title": pull_request.get("title"),
        "author_login": user.get("login"),
        "state": pull_request.get("state"),
        "created_at": pull_request.get("created_at"),
        "merged_at": pull_request.get("merged_at"),
        "is_merged": pull_request.get("merged_at") is not None,
        "draft": pull_request.get("draft"),
        "html_url": pull_request.get("html_url"),
    }


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def merge_pull_requests(existing: list[dict[str, Any]], new: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[int, dict[str, Any]] = {item["number"]: item for item in existing}
    for item in new:
        merged[item["number"]] = item
    return sorted(merged.values(), key=lambda item: item["number"])


def build_engineer_summary(pull_requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summary: dict[str, dict[str, int]] = {}
    for pull_request in pull_requests:
        engineer = pull_request.get("author_login") or "unknown"
        stats = summary.setdefault(engineer, {"opened_pr_count": 0, "merged_pr_count": 0})
        stats["opened_pr_count"] += 1
        stats["merged_pr_count"] += 1 if pull_request.get("is_merged") else 0

    rows = []
    for engineer, stats in summary.items():
        opened = stats["opened_pr_count"]
        merged = stats["merged_pr_count"]
        rows.append(
            {
                "engineer": engineer,
                "opened_pr_count": opened,
                "merged_pr_count": merged,
                "throughput_ratio": round(merged / opened, 4) if opened else 0.0,
            }
        )
    rows.sort(key=lambda item: item["opened_pr_count"], reverse=True)
    return rows


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
    if args.days_back < 1:
        print("--days-back must be at least 1.", file=sys.stderr)
        return 1
    if args.start_page < 1:
        print("--start-page must be at least 1.", file=sys.stderr)
        return 1
    if args.max_pages is not None and args.max_pages < 1:
        print("--max-pages must be at least 1.", file=sys.stderr)
        return 1

    created_since = datetime.now(timezone.utc) - timedelta(days=args.days_back)
    simplified_pull_requests: list[dict[str, Any]] = []

    try:
        page = args.start_page
        pages_fetched = 0

        while True:
            if args.max_pages is not None and pages_fetched >= args.max_pages:
                break

            page_pull_requests = fetch_page(args.owner, args.repo, page, args.per_page)
            if not page_pull_requests:
                break

            keep_fetching = False
            for pull_request in page_pull_requests:
                created_at = pull_request.get("created_at")
                if not created_at:
                    continue
                created_dt = parse_iso_datetime(created_at)
                if created_dt >= created_since:
                    simplified_pull_requests.append(simplify_pull_request(pull_request))
                    keep_fetching = True
                elif page == args.start_page and pages_fetched == 0:
                    keep_fetching = False

            pages_fetched += 1
            if len(page_pull_requests) < args.per_page or not keep_fetching:
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
            "state": "all",
            "sort": "created",
            "direction": "desc",
            "per_page": args.per_page,
            "days_back": args.days_back,
            "created_since": isoformat_z(created_since),
            "start_page": args.start_page,
            "max_pages": args.max_pages,
            "merge_output": args.merge_output,
        },
        "pull_request_count": len(simplified_pull_requests),
        "engineer_summary": build_engineer_summary(simplified_pull_requests),
        "pull_requests": simplified_pull_requests,
    }
    write_output(output_path, payload)
    print(f"Wrote {len(simplified_pull_requests)} opened PRs to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
