# posthog-top-engineer-dashboard

## Progress So Far

We pivoted from a GitHub API-heavy workflow to a local git-history workflow by adding `PostHog/posthog` as a submodule at `vendor/posthog`. The main analysis script is now `analysis/analyse-data.py`, which reads local git history from the submodule and writes summary output to `analysis/results/git-history-summary.json`.

## Why We Changed Approach

The original plan relied on several GitHub API scripts to fetch commits, enrich them with pull request metadata, and then clean the results. That approach started to create friction because enrichment required many API calls and ran into rate-limit and time constraints. Given the assignment timeline, analyzing local git history is a simpler and more reliable path for commit-based engineering impact metrics.

## Current Workflow

The current workflow is: initialize the `vendor/posthog` submodule, make sure it has the history window we want to analyze, optionally fetch the current open PR summary with `analysis/get-open-pr-data.py`, optionally fetch merged PR metadata with `analysis/get-merged-pr-data.py`, and then run `analysis/analyse-data.py`. The main analyzer uses local git history for merged work and can also incorporate the open PR and merged PR datasets for PR-based metrics.

## Ratio Metric Definition

The `review_commit_pr_ratio` analysis is based on the current open PR dataset. In this metric, `review_count` means how many open PRs an engineer is currently requested on as a reviewer, `commit_count` means the total commit count inside that engineer's currently open PRs, and `pr_count` means how many open PRs that engineer currently owns. The `balance_score` is a simple evenness score across those three values, where a more even distribution is treated as more all-rounded.

## Merge Time Metric Definition

The `pr_open_to_merge_time` analysis is based on merged PR metadata fetched from GitHub. For each engineer, it calculates the time between `created_at` and `merged_at` on merged pull requests in the selected window, then reports both average and median time in hours and days.

## Deprecated Scripts

The older API-based scripts are still available in `analysis/deprecated/` for reference, but they are no longer the primary path because of the API cost and runtime overhead.
