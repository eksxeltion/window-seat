#!/usr/bin/env python3
"""Merge open risk:green PRs that a maintainer has approved with a SHIP comment.

This script never deploys, never checks out or executes PR code, and never
edits labels or comments. It only reads PR metadata through the `gh` CLI and,
in live mode, asks GitHub to squash-merge a PR pinned to its head SHA.

For each open PR the rules below are evaluated in order. The first failing
rule ends evaluation and is logged as `PR #N: SKIP <reason>`.

1. Draft: the PR must not be a draft.
2. Labels: the PR must carry `risk:green` and must not carry `hold`,
   `risk:yellow` or `risk:red`.
3. Risk labeler: among workflow runs of `.github/workflows/risk-labeler.yml`
   whose head_sha equals the PR head SHA, the one with the highest id (the
   latest) must exist, be completed, and have conclusion success. A missing
   run, a pending run, or a non-success conclusion is a skip. This is so a
   risk:green label is not trusted when the labeler failed or has not run
   on this SHA.
4. Files: the file list (pulls/{n}/files, paginated) must be complete: its
   length must equal the PR's `changed_files` (taken from the single-PR GET
   pulls/{n}, which is fetched when the list payload lacks it) and must be
   below 3000, the API's cap on that endpoint. Otherwise the PR is skipped
   with "file list incomplete". Then no changed file (both filename and
   previous_filename for renames) may start with `.github/` or equal
   `.gitleaks.toml`. Defense in depth: otherwise a PR could edit the ci
   workflow it is judged by.
5. CI: check runs on the head SHA are requested with filter=all and
   check_name=ci. Among runs named `ci` from the `github-actions` app, the one
   with the highest id (ids are monotonic) is selected; an older success never
   overrides a newer failure or a pending rerun. It must be completed with
   conclusion success.
6. CI provenance: the workflow run behind the selected check run (looked up
   via actions/runs?check_suite_id=<check_run.check_suite.id>) must be exactly
   one run that is tied to this PR:
     - path, with any `@ref` suffix stripped, is `.github/workflows/ci.yml`,
     - head_sha equals the PR head SHA,
     - event is `pull_request`,
     - status is `completed` and conclusion is `success`,
     - its pull_requests array contains an entry whose number is this PR's
       number. GitHub leaves that array empty for fork PRs, so those are
       skipped with "ci run not linked to this PR".
7. Head arrival time T: the latest of
     - the head commit committer date,
     - the LATEST check suite created_at for the head SHA (a suite is created
       when the SHA is pushed, so a re-push of the same SHA moves T forward),
     - the created_at of the latest head_ref_force_pushed timeline event.
   If there are no check suites for the head SHA, the PR is skipped with
   "cannot establish head push time". T and the source that set it are
   logged.
8. SHIP comment: at least one issue comment must
     - have a body that, after leading whitespace is stripped, starts with
       `SHIP` (case-sensitive),
     - come from an OWNER, MEMBER or COLLABORATOR whose user type is not Bot,
     - have an effective time, max(created_at, updated_at), strictly after T,
     - have an effective time at least 60 minutes ago.
   Editing an old comment into SHIP therefore restarts the 60 minute clock.
   When none qualify, the most specific reason is reported.
9. Merge: in dry run, log `PR #N: WOULD MERGE (squash, sha=<sha>)`. In live
   mode, first re-check: re-fetch the PR (pulls/{n}), its labels, files,
   comments, check runs, workflow run and timing data, and re-run rules 1 to 8
   on the fresh data. Merge only if the re-check passes and the head SHA is
   unchanged. Then, as a final guard immediately before merging, re-fetch the
   PR and its labels one last time and re-run only rule 1, rule 2 and the head
   SHA equality check; if any of them now fails, skip with
   `final guard: <reason>`. Only then PUT pulls/{n}/merge with
   merge_method=squash and sha=<head sha>, and log `PR #N: MERGED` or
   `PR #N: MERGE FAILED <message>`.
   A failed merge does not stop the loop, but makes the script exit 1 at the
   end.

The pure decision functions below operate only on already-fetched dicts; all
API access lives in the thin fetch layer.

Authentication is handled entirely by `gh` (GH_TOKEN in Actions, the user's
gh login locally). This script never reads or prints a token.

Usage:
  green_automerge.py [--repo OWNER/NAME] [--dry-run | --live] [--pr N] [--now ISO]

Mode: --dry-run or --live; if neither is given, env DRY_RUN=true|false decides;
if that is unset too, the default is dry run.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Optional

REQUIRED_LABEL = "risk:green"
BLOCKING_LABELS = ("hold", "risk:yellow", "risk:red")
PROTECTED_PREFIX = ".github/"
PROTECTED_FILES = frozenset({".gitleaks.toml"})
CI_CHECK_NAME = "ci"
CI_APP_SLUG = "github-actions"
CI_WORKFLOW_PATH = ".github/workflows/ci.yml"
CI_WORKFLOW_EVENT = "pull_request"
LABELER_WORKFLOW_PATH = ".github/workflows/risk-labeler.yml"
PR_FILES_API_CAP = 3000
SHIP_PREFIX = "SHIP"
MAINTAINER_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})
SHIP_MIN_AGE = timedelta(minutes=60)


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def parse_ts(value: str) -> datetime:
    """Parse a GitHub ISO 8601 timestamp into an aware UTC datetime."""
    text = value.strip()
    if text.endswith("Z") or text.endswith("z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def fmt_ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Pure decision functions (operate on already-fetched dicts)
# ---------------------------------------------------------------------------

def check_draft(pr: dict) -> Optional[str]:
    """Rule 1. Returns a skip reason, or None if the rule passes."""
    if pr.get("draft"):
        return "draft"
    return None


def check_labels(pr: dict) -> Optional[str]:
    """Rule 2. Returns a skip reason, or None if the rule passes."""
    names = {lbl.get("name") for lbl in pr.get("labels") or []}
    if REQUIRED_LABEL not in names:
        return f"missing label {REQUIRED_LABEL}"
    blocking = [name for name in BLOCKING_LABELS if name in names]
    if blocking:
        return "has label " + ", ".join(blocking)
    return None


def select_labeler_run(workflow_runs: Iterable[dict], head_sha: str) -> Optional[dict]:
    """Pick the latest Risk labeler workflow run on the head SHA (highest id)."""
    candidates = [
        wr
        for wr in workflow_runs
        if _workflow_path(wr) == LABELER_WORKFLOW_PATH
        and wr.get("head_sha") == head_sha
        and isinstance(wr.get("id"), int)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda wr: wr["id"])


def check_labeler(run: Optional[dict]) -> Optional[str]:
    """Rule 3. Returns a skip reason, or None if the latest labeler run succeeded."""
    if run is None:
        return "no Risk labeler run on head SHA"
    if run.get("status") != "completed":
        return "Risk labeler not completed"
    conclusion = run.get("conclusion")
    if conclusion != "success":
        return f"Risk labeler concluded {conclusion}"
    return None


def _is_protected_path(path: Optional[str]) -> bool:
    if not path:
        return False
    return path.startswith(PROTECTED_PREFIX) or path in PROTECTED_FILES


def check_file_list_complete(files: list[dict], changed_files: Any) -> Optional[str]:
    """Rule 4, completeness. The fetched list must match the PR's changed_files
    count and stay below the API cap, otherwise unseen files could be protected."""
    if not isinstance(changed_files, int) or isinstance(changed_files, bool):
        return "file list incomplete"
    if len(files) != changed_files or len(files) >= PR_FILES_API_CAP:
        return "file list incomplete"
    return None


def check_files(files: Iterable[dict]) -> Optional[str]:
    """Rule 4, protected paths. Returns a skip reason, or None if the rule passes."""
    for f in files:
        if _is_protected_path(f.get("filename")) or _is_protected_path(f.get("previous_filename")):
            return "PR changes .github or .gitleaks.toml"
    return None


def select_ci_run(check_runs: Iterable[dict]) -> Optional[dict]:
    """Pick the `ci` run from the github-actions app with the highest id.

    Check run ids are monotonic, so the highest id is the latest run; a newer
    failure or pending rerun always wins over an older success.
    """
    candidates = [
        run
        for run in check_runs
        if run.get("name") == CI_CHECK_NAME
        and (run.get("app") or {}).get("slug") == CI_APP_SLUG
        and isinstance(run.get("id"), int)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda run: run["id"])


def check_ci(run: Optional[dict]) -> Optional[str]:
    """Rule 5, on the run chosen by select_ci_run. Returns a skip reason or None."""
    if run is None:
        return "no ci check run on head SHA"
    if run.get("status") != "completed":
        return "ci not completed"
    conclusion = run.get("conclusion")
    if conclusion != "success":
        return f"ci concluded {conclusion}"
    return None


def ci_check_suite_id(run: dict) -> Optional[int]:
    suite_id = (run.get("check_suite") or {}).get("id")
    return suite_id if isinstance(suite_id, int) else None


def _workflow_path(workflow_run: dict) -> str:
    """Workflow run path with any `@ref` suffix stripped."""
    return (workflow_run.get("path") or "").split("@", 1)[0]


def check_ci_provenance(
    workflow_runs: list[dict], head_sha: str, pr_number: int
) -> Optional[str]:
    """Rule 6. The ci check suite must belong to exactly one successful
    pull_request workflow run of .github/workflows/ci.yml on the PR head SHA,
    linked to this PR number."""
    if len(workflow_runs) != 1:
        return f"ci check not from {CI_WORKFLOW_PATH}"
    wr = workflow_runs[0]
    if _workflow_path(wr) != CI_WORKFLOW_PATH or wr.get("head_sha") != head_sha:
        return f"ci check not from {CI_WORKFLOW_PATH}"
    event = wr.get("event")
    if event != CI_WORKFLOW_EVENT:
        return f"ci run event is {event}, not {CI_WORKFLOW_EVENT}"
    if wr.get("status") != "completed":
        return "ci run not completed"
    conclusion = wr.get("conclusion")
    if conclusion != "success":
        return f"ci run concluded {conclusion}"
    linked = wr.get("pull_requests") or []
    if not any(isinstance(p, dict) and p.get("number") == pr_number for p in linked):
        return "ci run not linked to this PR"
    return None


@dataclass(frozen=True)
class Threshold:
    time: datetime
    source: str


def compute_threshold(
    commit: dict,
    check_suites: Iterable[dict],
    timeline: Iterable[dict],
    head_sha: str,
) -> Optional[Threshold]:
    """Rule 7. T = latest of commit date, latest suite creation, last force push.

    Returns None when there is no check suite for the head SHA, since push time
    cannot then be established.
    """
    suite_times = [
        parse_ts(s["created_at"])
        for s in check_suites
        if s.get("head_sha") == head_sha and s.get("created_at")
    ]
    if not suite_times:
        return None

    candidates: list[Threshold] = [
        Threshold(max(suite_times), "latest check suite created_at"),
    ]

    committer_date = ((commit.get("commit") or {}).get("committer") or {}).get("date")
    if committer_date:
        candidates.append(Threshold(parse_ts(committer_date), "head commit committer date"))

    push_times = [
        parse_ts(ev["created_at"])
        for ev in timeline
        if ev.get("event") == "head_ref_force_pushed" and ev.get("created_at")
    ]
    if push_times:
        candidates.append(Threshold(max(push_times), "latest head_ref_force_pushed event"))

    return max(candidates, key=lambda t: t.time)


def _is_ship(comment: dict) -> bool:
    return (comment.get("body") or "").lstrip().startswith(SHIP_PREFIX)


def _is_maintainer(comment: dict) -> bool:
    user_type = (comment.get("user") or {}).get("type")
    return comment.get("author_association") in MAINTAINER_ASSOCIATIONS and user_type != "Bot"


def comment_effective_time(comment: dict) -> datetime:
    """max(created_at, updated_at): an edit restarts the clock."""
    created = parse_ts(comment["created_at"])
    updated = comment.get("updated_at")
    return max(created, parse_ts(updated)) if updated else created


def check_ship(comments: Iterable[dict], threshold: datetime, now: datetime) -> Optional[str]:
    """Rule 8. Returns the most specific skip reason, or None if a SHIP qualifies."""
    ship = [c for c in comments if _is_ship(c)]
    if not ship:
        return "no SHIP comment"
    maint = [c for c in ship if _is_maintainer(c)]
    if not maint:
        return "SHIP comment from non-maintainer"
    fresh_times = [t for t in (comment_effective_time(c) for c in maint) if t > threshold]
    if not fresh_times:
        return "SHIP comment older than head commit"
    cutoff = now - SHIP_MIN_AGE
    if any(t <= cutoff for t in fresh_times):
        return None
    oldest = min(fresh_times)
    age_min = max(0, int((now - oldest).total_seconds() // 60))
    return f"SHIP comment only {age_min}m old (needs {int(SHIP_MIN_AGE.total_seconds() // 60)})"


def check_final_guard(pr: dict, expected_sha: str) -> Optional[str]:
    """Last check before the merge PUT: rules 1 and 2 plus head SHA equality."""
    reason = check_draft(pr) or check_labels(pr)
    if reason:
        return reason
    if (pr.get("head") or {}).get("sha") != expected_sha:
        return "head SHA changed"
    return None


# ---------------------------------------------------------------------------
# Thin fetch layer (gh CLI)
# ---------------------------------------------------------------------------

class GhError(RuntimeError):
    pass


def _gh(args: list[str]) -> str:
    try:
        proc = subprocess.run(
            ["gh", "api", *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise GhError("gh CLI not found on PATH") from exc
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "").strip().replace("\n", " ")
        raise GhError(msg or f"gh api exited {proc.returncode}")
    return proc.stdout


def _parse_concatenated_json(text: str) -> list[Any]:
    """`gh api --paginate` prints one JSON document per page back to back."""
    decoder = json.JSONDecoder()
    docs: list[Any] = []
    idx, end = 0, len(text)
    while True:
        while idx < end and text[idx].isspace():
            idx += 1
        if idx >= end:
            break
        doc, idx = decoder.raw_decode(text, idx)
        docs.append(doc)
    return docs


def gh_get(path: str) -> Any:
    return json.loads(_gh([path]))


def gh_get_list(path: str, key: Optional[str] = None) -> list[dict]:
    """GET a paginated endpoint and flatten all pages into one list.

    key: for endpoints that wrap the array in an object (check-runs,
    check-suites), the field holding the array on each page.
    """
    items: list[dict] = []
    for page in _parse_concatenated_json(_gh(["--paginate", path])):
        if key is not None:
            page = (page.get(key) or []) if isinstance(page, dict) else []
        if isinstance(page, list):
            items.extend(page)
    return items


def fetch_open_prs(repo: str) -> list[dict]:
    return gh_get_list(f"repos/{repo}/pulls?state=open&per_page=100")


def fetch_pr(repo: str, number: int) -> dict:
    return gh_get(f"repos/{repo}/pulls/{number}")


def fetch_labels(repo: str, number: int) -> list[dict]:
    return gh_get_list(f"repos/{repo}/issues/{number}/labels?per_page=100")


def fetch_files(repo: str, number: int) -> list[dict]:
    return gh_get_list(f"repos/{repo}/pulls/{number}/files?per_page=100")


def fetch_check_runs(repo: str, sha: str) -> list[dict]:
    return gh_get_list(
        f"repos/{repo}/commits/{sha}/check-runs?filter=all&check_name={CI_CHECK_NAME}&per_page=100",
        key="check_runs",
    )


def fetch_workflow_runs_for_suite(repo: str, check_suite_id: int) -> list[dict]:
    return gh_get_list(
        f"repos/{repo}/actions/runs?check_suite_id={check_suite_id}&per_page=100",
        key="workflow_runs",
    )


def fetch_labeler_runs(repo: str, sha: str) -> list[dict]:
    """Workflow runs of risk-labeler.yml on this head SHA, paginated."""
    return gh_get_list(
        f"repos/{repo}/actions/workflows/risk-labeler.yml/runs?head_sha={sha}&per_page=100",
        key="workflow_runs",
    )


def fetch_commit(repo: str, sha: str) -> dict:
    return gh_get(f"repos/{repo}/commits/{sha}")


def fetch_check_suites(repo: str, sha: str) -> list[dict]:
    return gh_get_list(f"repos/{repo}/commits/{sha}/check-suites?per_page=100", key="check_suites")


def fetch_timeline(repo: str, number: int) -> list[dict]:
    return gh_get_list(f"repos/{repo}/issues/{number}/timeline?per_page=100")


def fetch_comments(repo: str, number: int) -> list[dict]:
    return gh_get_list(f"repos/{repo}/issues/{number}/comments?per_page=100")


def merge_pr(repo: str, number: int, sha: str) -> None:
    _gh([
        "-X", "PUT",
        f"repos/{repo}/pulls/{number}/merge",
        "-f", "merge_method=squash",
        "-f", f"sha={sha}",
    ])


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

SKIPPED = "skipped"
WOULD_MERGE = "would_merge"
MERGED = "merged"
MERGE_FAILED = "merge_failed"


def log(line: str) -> None:
    print(line, flush=True)


def evaluate(repo: str, pr: dict, now: datetime, tag: str) -> Optional[str]:
    """Run rules 1 to 8 for one PR, fetching only what each rule needs.

    Returns a skip reason, or None if every rule passes. Raises GhError on
    API failure. `tag` prefixes log lines (e.g. "PR #7" or "PR #7 re-check").
    """
    n = pr["number"]

    reason = check_draft(pr) or check_labels(pr)
    if reason:
        return reason

    sha = (pr.get("head") or {}).get("sha")
    if not sha:
        return "no head SHA"

    reason = check_labeler(select_labeler_run(fetch_labeler_runs(repo, sha), sha))
    if reason:
        return reason

    files = fetch_files(repo, n)
    changed_files = pr.get("changed_files")
    if not isinstance(changed_files, int):
        # The list endpoint omits changed_files; the single-PR GET has it.
        changed_files = fetch_pr(repo, n).get("changed_files")
    reason = check_file_list_complete(files, changed_files) or check_files(files)
    if reason:
        return reason

    ci_run = select_ci_run(fetch_check_runs(repo, sha))
    reason = check_ci(ci_run)
    if reason:
        return reason
    assert ci_run is not None
    suite_id = ci_check_suite_id(ci_run)
    if suite_id is None:
        return f"ci check not from {CI_WORKFLOW_PATH}"
    reason = check_ci_provenance(fetch_workflow_runs_for_suite(repo, suite_id), sha, n)
    if reason:
        return reason

    threshold = compute_threshold(
        fetch_commit(repo, sha),
        fetch_check_suites(repo, sha),
        fetch_timeline(repo, n),
        sha,
    )
    if threshold is None:
        return "cannot establish head push time"
    log(f"{tag}: threshold T={fmt_ts(threshold.time)} (set by {threshold.source})")

    return check_ship(fetch_comments(repo, n), threshold.time, now)


def refetch_pr(repo: str, number: int) -> dict:
    """Fresh PR object with labels taken from the issue labels endpoint."""
    pr = fetch_pr(repo, number)
    pr["labels"] = fetch_labels(repo, number)
    return pr


def evaluate_and_act(repo: str, pr: dict, clock: Callable[[], datetime], live: bool) -> str:
    """Evaluate one PR and, in live mode, re-check on fresh data before merging."""
    n = pr["number"]

    def skip(reason: str) -> str:
        log(f"PR #{n}: SKIP {reason}")
        return SKIPPED

    sha = (pr.get("head") or {}).get("sha")
    try:
        reason = evaluate(repo, pr, clock(), f"PR #{n}")
    except GhError as exc:
        return skip(f"API error: {exc}")
    if reason:
        return skip(reason)

    if not live:
        log(f"PR #{n}: WOULD MERGE (squash, sha={sha})")
        return WOULD_MERGE

    # Re-check right before merging: everything may have changed since the
    # open PR list was fetched (new push, label, edited comment, CI rerun).
    try:
        fresh = refetch_pr(repo, n)
        if fresh.get("state") != "open":
            return skip("re-check: PR no longer open")
        fresh_sha = (fresh.get("head") or {}).get("sha")
        if fresh_sha != sha:
            return skip("re-check: head SHA changed")
        reason = evaluate(repo, fresh, clock(), f"PR #{n} re-check")
    except GhError as exc:
        return skip(f"re-check: API error: {exc}")
    if reason:
        return skip(f"re-check: {reason}")

    # Final guard: the full re-check above makes many API calls, so re-read
    # the cheap, fast-changing state (draft, labels, head SHA) one last time.
    try:
        reason = check_final_guard(refetch_pr(repo, n), sha)
    except GhError as exc:
        return skip(f"final guard: API error: {exc}")
    if reason:
        return skip(f"final guard: {reason}")

    try:
        merge_pr(repo, n, sha)
    except GhError as exc:
        log(f"PR #{n}: MERGE FAILED {exc}")
        return MERGE_FAILED
    log(f"PR #{n}: MERGED")
    return MERGED


def resolve_dry_run(args: argparse.Namespace) -> bool:
    if args.live:
        return False
    if args.dry_run:
        return True
    env = os.environ.get("DRY_RUN", "").strip().lower()
    if env in ("", "true"):
        return True
    if env == "false":
        return False
    raise SystemExit(f"error: DRY_RUN must be 'true' or 'false', got {env!r}")


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Squash-merge approved risk:green PRs.")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"),
                        help="OWNER/NAME (default: env GITHUB_REPOSITORY)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="log decisions only (default)")
    mode.add_argument("--live", action="store_true", help="actually merge")
    parser.add_argument("--pr", type=int, help="only evaluate this PR number")
    parser.add_argument("--now", help="override current time (ISO 8601), for tests")
    args = parser.parse_args(argv)
    if not args.repo or "/" not in args.repo:
        parser.error("--repo OWNER/NAME is required (or set GITHUB_REPOSITORY)")
    return args


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    dry_run = resolve_dry_run(args)
    if args.now:
        fixed_now = parse_ts(args.now)
        clock: Callable[[], datetime] = lambda: fixed_now
    else:
        clock = lambda: datetime.now(timezone.utc)
    now = clock()
    repo = args.repo

    log(f"green-automerge: repo={repo} mode={'DRY RUN' if dry_run else 'LIVE'} now={fmt_ts(now)}")

    try:
        prs = fetch_open_prs(repo)
    except GhError as exc:
        log(f"error: could not list open PRs: {exc}")
        return 2

    counts = {SKIPPED: 0, WOULD_MERGE: 0, MERGED: 0, MERGE_FAILED: 0}

    if args.pr is not None:
        prs = [pr for pr in prs if pr.get("number") == args.pr]
        if not prs:
            log(f"PR #{args.pr}: SKIP not an open PR")
            counts[SKIPPED] += 1

    for pr in sorted(prs, key=lambda p: p.get("number", 0)):
        counts[evaluate_and_act(repo, pr, clock, live=not dry_run)] += 1

    evaluated = sum(counts.values())
    log(
        f"summary: evaluated={evaluated} skipped={counts[SKIPPED]} "
        f"would_merge={counts[WOULD_MERGE]} merged={counts[MERGED]} "
        f"merge_failed={counts[MERGE_FAILED]}"
    )
    return 1 if counts[MERGE_FAILED] else 0


if __name__ == "__main__":
    sys.exit(main())
