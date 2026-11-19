"""Unit tests for the Risk labeler gate in .github/scripts/green_automerge.py.

The green-automerge script had no tests; this file covers the new rule: skip a
PR whose latest Risk labeler workflow run on its head SHA did not finish with
success, or is missing. No network. Accepts an optional unused URL argument
for the shared CI runner.
Usage: test_green_automerge.py [BASE_URL]
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".github" / "scripts"))

import green_automerge as ga  # noqa: E402

SHA = "abc123def456"
OTHER = "fff000"


def decide(items, head_sha=SHA):
    return ga.check_labeler(ga.select_labeler_run(items, head_sha))


def wr(run_id, status="completed", conclusion="success", sha=SHA, path=ga.LABELER_WORKFLOW_PATH):
    return {
        "id": run_id,
        "status": status,
        "conclusion": conclusion,
        "head_sha": sha,
        "path": path,
    }


def main() -> int:
    cases = [
        ("missing run", decide([]), "no Risk labeler run on head SHA"),
        (
            "only other SHA",
            decide([wr(1, sha=OTHER)]),
            "no Risk labeler run on head SHA",
        ),
        (
            "wrong workflow path",
            decide([wr(1, path=".github/workflows/ci.yml")]),
            "no Risk labeler run on head SHA",
        ),
        (
            "path with @ref suffix still counts",
            decide([wr(3, path=".github/workflows/risk-labeler.yml@refs/heads/main")]),
            None,
        ),
        (
            "in progress",
            decide([wr(2, status="in_progress", conclusion=None)]),
            "Risk labeler not completed",
        ),
        (
            "queued",
            decide([wr(2, status="queued", conclusion=None)]),
            "Risk labeler not completed",
        ),
        ("failure", decide([wr(4, conclusion="failure")]), "Risk labeler concluded failure"),
        ("cancelled", decide([wr(4, conclusion="cancelled")]), "Risk labeler concluded cancelled"),
        ("success", decide([wr(5)]), None),
        (
            "newer failure beats older success",
            decide([wr(10, conclusion="success"), wr(20, conclusion="failure")]),
            "Risk labeler concluded failure",
        ),
        (
            "newer success beats older failure",
            decide([wr(10, conclusion="failure"), wr(20, conclusion="success")]),
            None,
        ),
        (
            "newer pending beats older success",
            decide([wr(10, conclusion="success"), wr(30, status="in_progress", conclusion=None)]),
            "Risk labeler not completed",
        ),
        (
            "newer other-SHA run is ignored",
            decide([wr(10, conclusion="success"), wr(40, conclusion="failure", sha=OTHER)]),
            None,
        ),
    ]

    fails = 0
    for name, got, expected in cases:
        if got != expected:
            print(f"FAIL {name}: got {got!r}, expected {expected!r}")
            fails += 1
        else:
            print(f"PASS {name}  [{got!r}]")

    print(f"\n{len(cases)} cases, {fails} failures")
    return fails


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
