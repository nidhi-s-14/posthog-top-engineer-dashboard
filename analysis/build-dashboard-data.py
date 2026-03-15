#!/usr/bin/env python3

import argparse
import json
import math
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_GIT_HISTORY = Path("analysis/results/git-history-summary.json")
DEFAULT_OPENED_PR_WINDOW = Path("analysis/results/opened-pr-window-summary.json")
DEFAULT_MERGED_PR_INPUT = Path("analysis/results/merged-pr-summary.json")
DEFAULT_BALANCE_OUTPUT = Path("analysis/results/balance-view.json")
DEFAULT_THROUGHPUT_OUTPUT = Path("analysis/results/throughput.json")
DEFAULT_MERGE_TIME_OUTPUT = Path("analysis/results/merge-time.json")
DEFAULT_CHANGE_SURFACE_OUTPUT = Path("analysis/results/change-surface.json")
DEFAULT_SCOREBOARD_OUTPUT = Path("analysis/results/scoreboard.json")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build small dashboard-facing datasets from analysis outputs.",
    )
    parser.add_argument("--git-history-input", type=Path, default=DEFAULT_GIT_HISTORY)
    parser.add_argument("--opened-pr-window-input", type=Path, default=DEFAULT_OPENED_PR_WINDOW)
    parser.add_argument("--merged-pr-input", type=Path, default=DEFAULT_MERGED_PR_INPUT)
    parser.add_argument("--balance-output", type=Path, default=DEFAULT_BALANCE_OUTPUT)
    parser.add_argument("--throughput-output", type=Path, default=DEFAULT_THROUGHPUT_OUTPUT)
    parser.add_argument("--merge-time-output", type=Path, default=DEFAULT_MERGE_TIME_OUTPUT)
    parser.add_argument("--change-surface-output", type=Path, default=DEFAULT_CHANGE_SURFACE_OUTPUT)
    parser.add_argument("--scoreboard-output", type=Path, default=DEFAULT_SCOREBOARD_OUTPUT)
    return parser.parse_args()


def resolve_path(path: Path) -> Path:
    if path.is_absolute():
        return path
    return REPO_ROOT / path


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def build_balance_view(git_history: dict[str, Any]) -> dict[str, Any]:
    ratio_analysis = next(
        analysis
        for analysis in git_history.get("analyses", [])
        if analysis.get("analysis") == "review_commit_pr_ratio"
    )
    engineers = [
        engineer
        for engineer in ratio_analysis.get("engineers", [])
        if engineer.get("review_count", 0) + engineer.get("commit_count", 0) + engineer.get("pr_count", 0) > 0
    ]

    return {
        "source": {
            "from": "analysis/results/git-history-summary.json",
            "analysis": "review_commit_pr_ratio",
        },
        "engineer_count": len(engineers),
        "engineers": engineers,
    }


def build_throughput(opened_pr_window: dict[str, Any]) -> dict[str, Any]:
    engineers = [
        engineer
        for engineer in opened_pr_window.get("engineer_summary", [])
        if engineer.get("opened_pr_count", 0) > 0
    ]
    engineers.sort(key=lambda item: item.get("throughput_ratio", 0), reverse=True)

    return {
        "source": {
            "from": "analysis/results/opened-pr-window-summary.json",
            "definition": "merged_pr_count / opened_pr_count for PRs created inside the selected time window",
        },
        "engineer_count": len(engineers),
        "engineers": engineers,
    }


def parse_iso_datetime(value: str):
    from datetime import datetime

    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def build_merge_time(merged_pr_summary: dict[str, Any]) -> dict[str, Any]:
    durations_by_engineer: dict[str, list[float]] = {}
    for pull_request in merged_pr_summary.get("pull_requests", []):
        created_at = pull_request.get("created_at")
        merged_at = pull_request.get("merged_at")
        if not created_at or not merged_at:
            continue

        engineer = pull_request.get("author_login") or "unknown"
        duration_hours = (parse_iso_datetime(merged_at) - parse_iso_datetime(created_at)).total_seconds() / 3600
        durations_by_engineer.setdefault(engineer, []).append(duration_hours)

    engineers = []
    for engineer, durations in durations_by_engineer.items():
        durations.sort()
        count = len(durations)
        midpoint = count // 2
        median = (
            (durations[midpoint - 1] + durations[midpoint]) / 2
            if count % 2 == 0
            else durations[midpoint]
        )
        average = sum(durations) / count
        engineers.append(
            {
                "engineer": engineer,
                "merged_pr_count": count,
                "average_open_to_merge_hours": round(average, 2),
                "average_open_to_merge_days": round(average / 24, 2),
                "median_open_to_merge_hours": round(median, 2),
                "median_open_to_merge_days": round(median / 24, 2),
            }
        )

    engineers.sort(key=lambda item: item["average_open_to_merge_hours"])
    return {
        "source": {
            "from": "analysis/results/merged-pr-summary.json",
            "definition": "average and median time between created_at and merged_at per engineer",
        },
        "engineer_count": len(engineers),
        "engineers": engineers,
    }


def build_change_surface(git_history: dict[str, Any]) -> dict[str, Any]:
    commit_activity = next(
        analysis
        for analysis in git_history.get("analyses", [])
        if analysis.get("analysis") == "commit_activity"
    )

    engineers = []
    for engineer in commit_activity.get("engineers", []):
        lines_changed = (engineer.get("lines_added") or 0) + (engineer.get("lines_deleted") or 0)
        files_changed = engineer.get("files_changed") or 0
        log_lines_changed = math.log1p(lines_changed)
        change_surface_score = round(log_lines_changed * files_changed, 2)

        engineers.append(
            {
                "engineer": engineer.get("engineer"),
                "lines_changed": lines_changed,
                "files_changed": files_changed,
                "log_lines_changed": round(log_lines_changed, 4),
                "change_surface_score": change_surface_score,
            }
        )

    engineers.sort(key=lambda item: item["change_surface_score"], reverse=True)
    return {
        "source": {
            "from": "analysis/results/git-history-summary.json",
            "analysis": "commit_activity",
            "definition": "log(1 + lines_changed) * files_changed",
        },
        "engineer_count": len(engineers),
        "engineers": engineers,
    }


def normalize_higher_better(values: dict[str, float]) -> dict[str, float]:
    if not values:
        return {}
    min_value = min(values.values())
    max_value = max(values.values())
    if math.isclose(max_value, min_value):
        baseline = 1.0 if max_value > 0 else 0.0
        return {key: baseline for key in values}
    return {
        key: round((value - min_value) / (max_value - min_value), 4)
        for key, value in values.items()
    }


def normalize_lower_better(values: dict[str, float]) -> dict[str, float]:
    if not values:
        return {}
    positive_values = [value for value in values.values() if value > 0]
    if not positive_values:
        return {key: 0.0 for key in values}
    min_value = min(positive_values)
    max_value = max(positive_values)
    if math.isclose(max_value, min_value):
        return {key: 1.0 if value > 0 else 0.0 for key, value in values.items()}
    return {
        key: round((max_value - value) / (max_value - min_value), 4) if value > 0 else 0.0
        for key, value in values.items()
    }


def build_scoreboard(
    throughput: dict[str, Any],
    balance_view: dict[str, Any],
    merge_time: dict[str, Any],
    change_surface: dict[str, Any],
) -> dict[str, Any]:
    throughput_raw = {
        engineer["engineer"]: engineer.get("throughput_ratio", 0.0)
        for engineer in throughput.get("engineers", [])
    }
    collaboration_raw = {
        engineer["engineer"]: engineer.get("balance_score", 0.0)
        for engineer in balance_view.get("engineers", [])
    }
    impact_raw = {
        engineer["engineer"]: engineer.get("change_surface_score", 0.0)
        for engineer in change_surface.get("engineers", [])
    }
    speed_raw = {
        engineer["engineer"]: engineer.get("average_open_to_merge_hours", 0.0)
        for engineer in merge_time.get("engineers", [])
    }

    throughput_map = normalize_higher_better(throughput_raw)
    collaboration_map = normalize_higher_better(collaboration_raw)
    impact_map = normalize_higher_better(impact_raw)
    speed_map = normalize_lower_better(speed_raw)

    engineers = sorted(
        set(throughput_map.keys()) | set(collaboration_map.keys()) | set(impact_map.keys()) | set(speed_map.keys())
    )

    rows = []
    for engineer in engineers:
        throughput_score = throughput_map.get(engineer, 0.0)
        collaboration_score = collaboration_map.get(engineer, 0.0)
        impact_score = impact_map.get(engineer, 0.0)
        speed_score = speed_map.get(engineer, 0.0)

        total_score = (
            0.35 * throughput_score
            + 0.25 * collaboration_score
            + 0.25 * impact_score
            + 0.15 * speed_score
        )

        rows.append(
            {
                "engineer": engineer,
                "overall_score": round(total_score, 4),
                "component_scores": {
                    "throughput": round(throughput_score, 4),
                    "collaboration": round(collaboration_score, 4),
                    "impact": round(impact_score, 4),
                    "speed": round(speed_score, 4),
                },
            }
        )

    rows.sort(key=lambda item: item["overall_score"], reverse=True)

    return {
        "source": {
            "throughput": "analysis/results/throughput.json",
            "collaboration": "analysis/results/balance-view.json",
            "impact": "analysis/results/change-surface.json",
            "speed": "analysis/results/merge-time.json",
        },
        "weights": {
            "throughput": 0.35,
            "collaboration": 0.25,
            "impact": 0.25,
            "speed": 0.15,
        },
        "normalization": {
            "throughput": "min-max (higher is better)",
            "collaboration": "min-max (higher is better)",
            "impact": "min-max (higher is better)",
            "speed": "min-max inverse on average_open_to_merge_hours (lower is better)",
        },
        "engineer_count": len(rows),
        "engineers": rows,
    }


def main() -> int:
    args = parse_args()
    git_history = read_json(resolve_path(args.git_history_input))
    opened_pr_window = read_json(resolve_path(args.opened_pr_window_input))
    merged_pr_summary = read_json(resolve_path(args.merged_pr_input))

    balance_view = build_balance_view(git_history)
    throughput = build_throughput(opened_pr_window)
    merge_time = build_merge_time(merged_pr_summary)
    change_surface = build_change_surface(git_history)

    write_json(resolve_path(args.balance_output), balance_view)
    write_json(resolve_path(args.throughput_output), throughput)
    write_json(resolve_path(args.merge_time_output), merge_time)
    write_json(resolve_path(args.change_surface_output), change_surface)
    write_json(
        resolve_path(args.scoreboard_output),
        build_scoreboard(throughput, balance_view, merge_time, change_surface),
    )
    print("Wrote dashboard data files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
