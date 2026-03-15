#!/usr/bin/env python3

import argparse
import json
import math
import re
import subprocess
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional


DEFAULT_REPO_PATH = Path("vendor/posthog")
DEFAULT_OUTPUT = Path("analysis/results/git-history-summary.json")
DEFAULT_OPEN_PR_INPUT = Path("analysis/results/open-pr-summary.json")
DEFAULT_MERGED_PR_INPUT = Path("analysis/results/merged-pr-summary.json")
DEFAULT_DAYS_BACK = 90
PR_NUMBER_PATTERN = re.compile(r"\(#(\d+)\)")
FIELD_SEPARATOR = "\x1f"
REPO_ROOT = Path(__file__).resolve().parent.parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze local git history for the PostHog submodule.",
    )
    parser.add_argument(
        "--repo-path",
        type=Path,
        default=DEFAULT_REPO_PATH,
        help="Path to the local git repository to analyze.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Path to the analysis summary JSON file.",
    )
    parser.add_argument(
        "--days-back",
        type=int,
        default=DEFAULT_DAYS_BACK,
        help="Analyze commits from the last N days.",
    )
    parser.add_argument(
        "--open-pr-input",
        type=Path,
        default=DEFAULT_OPEN_PR_INPUT,
        help="Path to the open PR summary JSON file.",
    )
    parser.add_argument(
        "--merged-pr-input",
        type=Path,
        default=DEFAULT_MERGED_PR_INPUT,
        help="Path to the merged PR summary JSON file.",
    )
    return parser.parse_args()


def run_git_command(repo_path: Path, args: list[str]) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_path), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def isoformat_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def extract_pr_number(subject: str) -> Optional[int]:
    match = PR_NUMBER_PATTERN.search(subject)
    if not match:
        return None
    return int(match.group(1))


def parse_numstat(value: str) -> int:
    if not value or value == "-":
        return 0
    return int(value)


def resolve_repo_path(path: Path) -> Path:
    if path.is_absolute():
        return path
    return REPO_ROOT / path


def fetch_commit_history(repo_path: Path, since: str) -> list[dict[str, Any]]:
    pretty_format = (
        f"%H{FIELD_SEPARATOR}%an{FIELD_SEPARATOR}%ae{FIELD_SEPARATOR}%aI"
        f"{FIELD_SEPARATOR}%s{FIELD_SEPARATOR}%P"
    )
    output = run_git_command(
        repo_path,
        ["log", f"--since={since}", "--numstat", f"--pretty=format:{pretty_format}"],
    )

    commits: list[dict[str, Any]] = []
    current_commit: Optional[dict[str, Any]] = None
    for line in output.splitlines():
        parts = line.split(FIELD_SEPARATOR)
        if len(parts) == 6:
            if current_commit is not None:
                commits.append(current_commit)

            sha, author_name, author_email, authored_at, subject, parents = parts
            current_commit = {
                "sha": sha,
                "author_name": author_name,
                "author_email": author_email,
                "authored_at": authored_at,
                "subject": subject,
                "pull_request_number": extract_pr_number(subject),
                "is_merge_commit": len([parent for parent in parents.split() if parent]) > 1,
                "files_changed": 0,
                "additions": 0,
                "deletions": 0,
            }
            continue

        if current_commit is None:
            continue

        fields = line.split("\t")
        if len(fields) != 3:
            continue
        current_commit["additions"] += parse_numstat(fields[0])
        current_commit["deletions"] += parse_numstat(fields[1])
        current_commit["files_changed"] += 1

    if current_commit is not None:
        commits.append(current_commit)

    return commits


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def raw_engineer_key(commit: dict[str, Any]) -> str:
    return commit["author_name"] or commit["author_email"]


def build_canonical_engineer_lookup(
    commits: list[dict[str, Any]],
    merged_pr_payload: Optional[dict[str, Any]],
) -> dict[str, str]:
    if merged_pr_payload is None:
        return {}

    pr_author_by_number = {
        pull_request.get("number"): pull_request.get("author_login")
        for pull_request in merged_pr_payload.get("pull_requests", [])
        if pull_request.get("number") is not None and pull_request.get("author_login")
    }

    login_votes_by_engineer: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for commit in commits:
        pr_number = commit.get("pull_request_number")
        author_login = pr_author_by_number.get(pr_number)
        if not author_login:
            continue
        login_votes_by_engineer[raw_engineer_key(commit)][author_login] += 1

    canonical_lookup: dict[str, str] = {}
    for engineer, login_votes in login_votes_by_engineer.items():
        canonical_login = max(
            login_votes.items(),
            key=lambda item: (item[1], item[0].lower()),
        )[0]
        canonical_lookup[engineer] = canonical_login

    return canonical_lookup


def analyze_commit_activity(commits: list[dict[str, Any]]) -> dict[str, Any]:
    per_engineer: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "engineer": "",
            "commit_count": 0,
            "merge_commit_count": 0,
            "files_changed": 0,
            "lines_added": 0,
            "lines_deleted": 0,
        }
    )

    for commit in commits:
        engineer = commit.get("engineer") or raw_engineer_key(commit)
        stats = per_engineer[engineer]
        stats["engineer"] = engineer
        stats["commit_count"] += 1
        stats["merge_commit_count"] += 1 if commit["is_merge_commit"] else 0
        stats["files_changed"] += commit["files_changed"]
        stats["lines_added"] += commit["additions"]
        stats["lines_deleted"] += commit["deletions"]

    return {
        "analysis": "commit_activity",
        "description": "Per-engineer commit activity from local git history.",
        "engineers": sorted(per_engineer.values(), key=lambda item: item["commit_count"], reverse=True),
    }


def analyze_pr_merge_proxy(commits: list[dict[str, Any]]) -> dict[str, Any]:
    merged_prs_by_engineer: dict[str, set[int]] = defaultdict(set)

    for commit in commits:
        pr_number = commit["pull_request_number"]
        if pr_number is None:
            continue
        engineer = commit.get("engineer") or raw_engineer_key(commit)
        merged_prs_by_engineer[engineer].add(pr_number)

    engineers = [
        {
            "engineer": engineer,
            "merged_pr_count": len(pr_numbers),
        }
        for engineer, pr_numbers in sorted(
            merged_prs_by_engineer.items(),
            key=lambda item: len(item[1]),
            reverse=True,
        )
    ]

    return {
        "analysis": "merged_pr_count",
        "description": "Unique merged PRs per engineer inferred from commit messages in local git history.",
        "notes": [
            "This is not a true opened-to-merged ratio.",
            "Git history can show merged PRs referenced in commit messages, but not every PR opened in the time window.",
        ],
        "engineers": engineers,
    }


def calculate_balance_score(review_count: int, commit_count: int, pr_count: int) -> float:
    total = review_count + commit_count + pr_count
    if total == 0:
        return 0.0

    values = [review_count / total, commit_count / total, pr_count / total]
    return round(1 - (max(values) - min(values)), 4)


def analyze_review_commit_pr_ratio(open_pr_payload: dict[str, Any]) -> dict[str, Any]:
    authored_prs: dict[str, dict[str, int]] = defaultdict(
        lambda: {
            "open_pr_count": 0,
            "open_pr_commit_count": 0,
        }
    )
    review_load: dict[str, int] = defaultdict(int)

    for pull_request in open_pr_payload.get("pull_requests", []):
        author = pull_request.get("author_login") or "unknown"
        authored_prs[author]["open_pr_count"] += 1
        authored_prs[author]["open_pr_commit_count"] += pull_request.get("commits") or 0

        for reviewer in pull_request.get("requested_reviewers", []):
            review_load[reviewer] += 1

    engineers = sorted(set(authored_prs.keys()) | set(review_load.keys()))
    rows = []
    for engineer in engineers:
        review_count = review_load.get(engineer, 0)
        commit_count = authored_prs.get(engineer, {}).get("open_pr_commit_count", 0)
        pr_count = authored_prs.get(engineer, {}).get("open_pr_count", 0)
        rows.append(
            {
                "engineer": engineer,
                "review_count": review_count,
                "commit_count": commit_count,
                "pr_count": pr_count,
                "review_commit_pr_ratio": f"{review_count}:{commit_count}:{pr_count}",
                "balance_score": calculate_balance_score(review_count, commit_count, pr_count),
            }
        )

    rows.sort(key=lambda item: item["balance_score"], reverse=True)

    return {
        "analysis": "review_commit_pr_ratio",
        "description": "Per-engineer review, commit, and PR ratio based on the current open PR dataset.",
        "engineers": rows,
    }


def calculate_duration_hours(created_at: str, merged_at: str) -> float:
    duration = parse_iso_datetime(merged_at) - parse_iso_datetime(created_at)
    return duration.total_seconds() / 3600


def analyze_pr_open_to_merge_time(merged_pr_payload: dict[str, Any]) -> dict[str, Any]:
    durations_by_engineer: dict[str, list[float]] = defaultdict(list)

    for pull_request in merged_pr_payload.get("pull_requests", []):
        created_at = pull_request.get("created_at")
        merged_at = pull_request.get("merged_at")
        if not created_at or not merged_at:
            continue

        engineer = pull_request.get("author_login") or "unknown"
        durations_by_engineer[engineer].append(calculate_duration_hours(created_at, merged_at))

    engineers = []
    for engineer, durations in durations_by_engineer.items():
        durations.sort()
        count = len(durations)
        midpoint = count // 2
        if count % 2 == 0:
            median = (durations[midpoint - 1] + durations[midpoint]) / 2
        else:
            median = durations[midpoint]

        engineers.append(
            {
                "engineer": engineer,
                "merged_pr_count": count,
                "average_open_to_merge_hours": round(sum(durations) / count, 2),
                "median_open_to_merge_hours": round(median, 2),
                "average_open_to_merge_days": round((sum(durations) / count) / 24, 2),
                "median_open_to_merge_days": round(median / 24, 2),
            }
        )

    engineers.sort(key=lambda item: item["average_open_to_merge_hours"])

    return {
        "analysis": "pr_open_to_merge_time",
        "description": "Average and median time between PR creation and merge for each engineer.",
        "engineers": engineers,
    }


def run_analyses(
    commits: list[dict[str, Any]],
    repo_path: Path,
    since: str,
    open_pr_payload: Optional[dict[str, Any]],
    merged_pr_payload: Optional[dict[str, Any]],
) -> dict[str, Any]:
    canonical_lookup = build_canonical_engineer_lookup(commits, merged_pr_payload)
    normalized_commits = []
    for commit in commits:
        normalized_commit = dict(commit)
        normalized_commit["engineer"] = canonical_lookup.get(raw_engineer_key(commit), raw_engineer_key(commit))
        normalized_commits.append(normalized_commit)

    analyses = [
        analyze_commit_activity(normalized_commits),
        analyze_pr_merge_proxy(normalized_commits),
    ]
    if open_pr_payload is not None:
        analyses.append(analyze_review_commit_pr_ratio(open_pr_payload))
    if merged_pr_payload is not None:
        analyses.append(analyze_pr_open_to_merge_time(merged_pr_payload))

    return {
        "source": {
            "repo_path": str(repo_path),
            "since": since,
            "commit_count": len(commits),
        },
        "analyses": analyses,
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main() -> int:
    args = parse_args()
    if args.days_back < 1:
        raise SystemExit("--days-back must be at least 1.")

    repo_path = resolve_repo_path(args.repo_path)
    open_pr_input = resolve_repo_path(args.open_pr_input)
    merged_pr_input = resolve_repo_path(args.merged_pr_input)
    output_path = resolve_repo_path(args.output)
    since = isoformat_z(datetime.now(timezone.utc) - timedelta(days=args.days_back))
    commits = fetch_commit_history(repo_path, since)
    open_pr_payload = read_json(open_pr_input) if open_pr_input.exists() else None
    merged_pr_payload = read_json(merged_pr_input) if merged_pr_input.exists() else None
    analysis_summary = run_analyses(commits, repo_path, since, open_pr_payload, merged_pr_payload)
    write_json(output_path, analysis_summary)
    print(f"Wrote git history analysis to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
