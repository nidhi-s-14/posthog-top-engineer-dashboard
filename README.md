# posthog-top-engineer-dashboard

## Project Summary

This project builds a one-page dashboard for analyzing engineering activity in `PostHog/posthog` over a recent window. The final approach uses a hybrid data model:

- local git history from the `vendor/posthog` submodule for commit-level activity
- lightweight GitHub PR metadata for open PR and merged PR metrics
- small dashboard-facing JSON files in `analysis/results/` for the frontend

## Why I Changed The Approach

The original plan was much more GitHub API-heavy and relied on commit enrichment per PR. That created runtime and rate-limit pressure, especially under assignment time constraints. I switched the core analysis to local git history because it is faster, more stable, and better suited for commit-level contribution metrics. I still use targeted PR API fetches where git history alone cannot answer the question, such as open PR throughput or PR open-to-merge timing.

## Current Workflow

The current workflow is:

1. initialize the `vendor/posthog` submodule
2. fetch PR metadata with:
   - `analysis/get-open-pr-data.py`
   - `analysis/get-merged-pr-data.py`
   - `analysis/get-opened-pr-window-data.py`
3. run `analysis/analyse-data.py`
4. run `analysis/build-dashboard-data.py`
5. serve the static dashboard from the repo root

The main analysis summary is written to `analysis/results/git-history-summary.json`. The dashboard then reads smaller derived files such as `scoreboard.json`, `throughput.json`, `balance-view.json`, `merge-time.json`, and `change-surface.json`.

## Key Analyses

### 1. Throughput

Throughput is defined as:

`merged_pr_count / opened_pr_count`

This is calculated using PRs opened inside the selected analysis window so the ratio stays bounded between `0` and `1`.

### 2. Balance Metric

The balance metric is based on:

- `review_count`
- `commit_count`
- `pr_count`

These come from the open PR dataset:

- `review_count`: how many open PRs an engineer is currently requested on as a reviewer
- `commit_count`: the total commits inside that engineer's currently open PRs
- `pr_count`: how many open PRs that engineer currently owns

The `balance_score` is an evenness score across those three values. A more even mix is treated as a more all-rounded engineering profile.

### 3. PR Open-to-Merge Time

This metric is calculated from merged PR metadata fetched from GitHub. For each engineer, it measures the time between `created_at` and `merged_at` and reports average and median merge time.

### 4. Change Surface / Impact

The impact metric is:

`log(1 + lines_changed) * files_changed`

This is derived from local git history and is intended to capture both code volume and breadth of change. The `log` transform reduces the effect of extreme line-count outliers.

## Identity Normalization

One challenge in the hybrid pipeline is that local git history uses commit author names, while GitHub PR metadata uses GitHub logins. To keep the dashboard consistent, the git-based analysis now reconciles commit authors against merged PR authors and emits a common canonical engineer identifier wherever possible.

## Leaderboard Formula

The final scoreboard is computed as:

`score = 0.35 * throughput + 0.25 * collaboration + 0.25 * impact + 0.15 * speed`

Before weighting, all four component metrics are normalized onto a common `0..1` scale so that one raw metric cannot dominate the leaderboard simply because it has a larger numeric range.

## Deprecated Scripts

The older API-based scripts are still available in `analysis/deprecated/` for reference, but they are no longer the primary path because of the API cost and runtime overhead.
