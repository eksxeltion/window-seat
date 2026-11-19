"""Fixture-driven test for the risk labeler matching rules in .github/scripts/risk_match.js.

Runs the same JS module the workflow (.github/workflows/risk-labeler.yml) loads, through node, on inline fixtures
shaped like pulls.listFiles entries, and checks each one is classified as "forces red" or "does not force red".
A fixture can hold several files; it forces red when any file does (same as the workflow loop).
Also checks that the workflow loads the helper from a trusted checkout and keeps no matching rules of its own.
Usage: test_risk_labeler.py [BASE_URL] (unused, accepted for the shared runner)"""
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
HELPER = os.path.join(ROOT, ".github", "scripts", "risk_match.js")
WORKFLOW = os.path.join(ROOT, ".github", "workflows", "risk-labeler.yml")

RED = "red"
NOT_RED = "not red"


def patch(*lines):
    """Build an API-style patch (no ---/+++ headers) from diff lines."""
    return "@@ -1,3 +1,3 @@\n" + "\n".join(lines)


# (name, expected, [files])
FIXTURES = [
    # PR 51-style false positives: must not force red.
    ("js fooToken variable", NOT_RED, [
        {"filename": "js/receiver.js", "patch": patch(" const a = 1;", "-let fooToken = null;", "+let fooToken = load();")},
    ]),
    ("js playerToken and csrfToken", NOT_RED, [
        {"filename": "js/player.js", "patch": patch("+const playerToken = next();", "+this.csrfToken = t;")},
    ]),
    ("js monkey", NOT_RED, [
        {"filename": "js/app.js", "patch": patch("+// the monkey patch below keeps old casts alive", "+const monkey = 1;")},
    ]),
    ("css keyframe and keyframes", NOT_RED, [
        {"filename": "css/style.css", "patch": patch("+@keyframes fade { from { opacity: 0 } }", "+.x { animation: fade 1s; }")},
    ]),
    ("js keyFrame camelCase", NOT_RED, [
        {"filename": "js/anim.js", "patch": patch("+const keyFrame = frames[0];", "+const monkeyKeyframe = 2;")},
    ]),
    ("tests patch with key, delete, token", NOT_RED, [
        {"filename": "tests/test_receiver_cmds.py", "patch": patch(
            "+    page.keyboard.press(key)", "+    delete = True", "+    token = 'fake-token'",
            "+    api_key = 'not-a-real-key'", "+    secret = None")},
    ]),
    ("tests filenames with token and key", NOT_RED, [
        {"filename": "tests/test_token_refresh.py", "patch": patch("+x = 1")},
        {"filename": "tests/fixture_key_delete.csv", "patch": patch("+a,b")},
        {"filename": "tests/secret-auth/apiKey.js", "patch": patch("+// tests only")},
    ]),
    ("tests file renamed within tests", NOT_RED, [
        {"filename": "tests/test_key_new.py", "previous_filename": "tests/test_key_old.py", "patch": patch("+token = 1")},
    ]),
    ("context lines are not scanned", NOT_RED, [
        {"filename": "js/cams.js", "patch": "@@ -10,3 +10,3 @@ function deleteKey(token) {\n  delete cams[key];\n-  x = 1;\n+  x = 2;\n  return token;"},
    ]),
    ("deleteCam and tokenizer identifiers", NOT_RED, [
        {"filename": "js/menu.js", "patch": patch("+function deleteCam(id) { return tokenizer(id); }")},
    ]),
    ("plain js and docs changes", NOT_RED, [
        {"filename": "js/sender.js", "patch": patch("+const delay = 5000;")},
        {"filename": "docs/notes.md", "patch": patch("+Monkeys and keyframes are fine.")},
        {"filename": "receiver.html", "patch": patch("+<div class=\"keyboard-hint\"></div>")},
    ]),
    ("binary file without patch", NOT_RED, [
        {"filename": "icons/icon-512.png"},
    ]),
    ("path words: monkey, keyframes, fooToken, tokenizer", NOT_RED, [
        {"filename": "js/monkey.js", "patch": patch("+x()")},
        {"filename": "css/keyframes.css", "patch": patch("+x")},
        {"filename": "js/fooToken.js", "patch": patch("+x()")},
        {"filename": "js/tokenizer.js", "patch": patch("+x()")},
    ]),

    # Real secret-ish changes: must force red.
    ("js apiKey assignment", RED, [
        {"filename": "js/config.js", "patch": patch("+const apiKey = 'abc';")},
    ]),
    ("js api_key", RED, [
        {"filename": "js/a.js", "patch": patch("+api_key = x")},
    ]),
    ("js AUTH_TOKEN", RED, [
        {"filename": "js/b.js", "patch": patch("+process.env.AUTH_TOKEN")},
    ]),
    ("py access_token", RED, [
        {"filename": "tools/sync.py", "patch": patch("+access_token = os.environ['X']")},
    ]),
    ("js APIKey", RED, [
        {"filename": "js/c.js", "patch": patch("+headers.APIKey = v;")},
    ]),
    ("js clientSecret", RED, [{"filename": "js/d.js", "patch": patch("+opts.clientSecret = s;")}]),
    ("js GITHUB_TOKEN", RED, [{"filename": "js/e.js", "patch": patch("+const t = GITHUB_TOKEN;")}]),
    ("js glued apikey", RED, [{"filename": "js/f.js", "patch": patch("+url += '&apikey=' + k;")}]),
    ("js GitHubToken", RED, [{"filename": "js/m.js", "patch": patch("+const GitHubToken = t;")}]),
    ("js APIkey", RED, [{"filename": "js/n.js", "patch": patch("+cfg.APIkey = k;")}]),
    ("js myApiKey", RED, [{"filename": "js/o.js", "patch": patch("+let myApiKey;")}]),
    ("js stripeSecretKey", RED, [{"filename": "js/g.js", "patch": patch("+const stripeSecretKey = k;")}]),
    ("removed apiKey line also counts", RED, [
        {"filename": "js/config.js", "patch": patch("-const apiKey = 'abc';", "+const x = 1;")},
    ]),
    ("removed line starting with -- is scanned", RED, [
        {"filename": "db/schema.sql", "patch": patch("--- api_key column", "+-- nothing")},
    ]),
    ("whole word token in js", RED, [{"filename": "js/h.js", "patch": patch("+send({ token: t });")}]),
    ("whole word delete in js", RED, [{"filename": "js/i.js", "patch": patch("+delete cams[id];")}]),
    ("whole word KEY and secrets", RED, [{"filename": "js/j.js", "patch": patch("+const KEY = 1; // secrets")}]),
    ("whole word stripe and payment", RED, [{"filename": "js/k.js", "patch": patch("+import stripe from 'x'; // payment")}]),
    ("x-api-key header", RED, [{"filename": "js/l.js", "patch": patch("+h['x-api-key'] = v;")}]),
    ("one red file among clean ones", RED, [
        {"filename": "js/sender.js", "patch": patch("+const delay = 5000;")},
        {"filename": "tests/test_x.py", "patch": patch("+token = 1")},
        {"filename": "js/auth.js", "patch": patch("+x()")},
    ]),

    # Path rules: unchanged and not weakened.
    ("workflow edit", RED, [{"filename": ".github/workflows/ci.yml", "patch": patch("+  run: echo hi")}]),
    ("github script edit", RED, [{"filename": ".github/scripts/risk_match.js", "patch": patch("+x")}]),
    ("vercel.json", RED, [{"filename": "vercel.json", "patch": patch("+{}")}]),
    ("nested vercel.json", RED, [{"filename": "sub/vercel.json", "patch": patch("+{}")}]),
    ("package.json", RED, [{"filename": "package.json", "patch": patch("+{}")}]),
    ("lockfile", RED, [{"filename": "package-lock.json", "patch": patch("+{}")}]),
    ("yarn lockfile", RED, [{"filename": "web/yarn.lock", "patch": patch("+x")}]),
    ("root .env", RED, [{"filename": ".env", "patch": patch("+X=1")}]),
    (".env.local", RED, [{"filename": ".env.local", "patch": patch("+X=1")}]),
    ("prod.env", RED, [{"filename": "config/prod.env", "patch": patch("+X=1")}]),
    ("tests/.env still red", RED, [{"filename": "tests/.env", "patch": patch("+X=1")}]),
    ("tests/.env.test still red", RED, [{"filename": "tests/.env.test", "patch": patch("+X=1")}]),
    ("tests/package.json still red", RED, [{"filename": "tests/package.json", "patch": patch("+{}")}]),
    ("tests/vercel.json still red", RED, [{"filename": "tests/vercel.json", "patch": patch("+{}")}]),
    ("env file without patch", RED, [{"filename": "deploy/.env"}]),

    # Keyword in path outside tests/.
    ("secret-config.js", RED, [{"filename": "js/secret-config.js", "patch": patch("+x()")}]),
    ("api_key.json", RED, [{"filename": "config/api_key.json", "patch": patch("+{}")}]),
    ("auth dir", RED, [{"filename": "auth/login.js", "patch": patch("+x()")}]),
    ("payment page", RED, [{"filename": "payment.html", "patch": patch("+<p></p>")}]),
    ("apiKey.js path", RED, [{"filename": "js/apiKey.js", "patch": patch("+x()")}]),
    ("tokens.json path", RED, [{"filename": "data/tokens.json", "patch": patch("+[]")}]),
    ("delete_cam.js path", RED, [{"filename": "js/delete_cam.js", "patch": patch("+x()")}]),
    ("renamed out of tests into js", RED, [
        {"filename": "js/helper.js", "previous_filename": "tests/helper.js", "patch": patch("+token = 1")},
    ]),
    ("renamed from secret path into tests", RED, [
        {"filename": "tests/fixture.js", "previous_filename": "js/secret-config.js", "patch": patch("+x()")},
    ]),
    ("moved into tests from js keeps patch scan", RED, [
        {"filename": "tests/fixture.js", "previous_filename": "js/fixture.js", "patch": patch("+const apiKey = 1;")},
    ]),
    ("Tests/ (not the tests dir) is not skipped", RED, [
        {"filename": "Tests/token.js", "patch": patch("+x()")},
    ]),
]

DRIVER = r"""
const m = require(process.argv[1]);
let input = '';
process.stdin.on('data', (d) => { input += d; });
process.stdin.on('end', () => {
  const fixtures = JSON.parse(input);
  const out = fixtures.map((files) => {
    let reason = null;
    for (const f of files) {
      if (reason) break;
      reason = m.fileReason(f);
    }
    return reason;
  });
  const extra = {
    sanitized: m.fileReason({ filename: 'js/secret\n::error::x%.js' }),
    leak: m.fileReason({ filename: 'js/x.js', patch: '@@ -1 +1 @@\n+const v = "zzLeakMarker_apiKey_9f8e7d";' }),
    parts: m.identifierParts('APIKey_fooToken2AUTH_TOKEN'),
  };
  process.stdout.write(JSON.stringify({ out, extra }));
});
"""

fails = []


def strip_comment(value):
    """Drop a trailing ' # comment' from a YAML scalar (values here never contain ' #')."""
    return value.split(" #", 1)[0].strip()


def workflow_steps(text):
    """Minimal parser for the single job's steps: returns dicts with step keys, a 'with' dict and the raw text.
    Comment lines are ignored, so a commented-out key can not satisfy a check."""
    lines = [l for l in text.splitlines() if not l.strip().startswith("#")]
    try:
        start = next(i for i, l in enumerate(lines) if l.strip() == "steps:")
    except StopIteration:
        return []
    indent = len(lines[start]) - len(lines[start].lstrip()) + 2
    steps, cur, in_with = [], None, False
    for l in lines[start + 1:]:
        if not l.strip():
            continue
        ind = len(l) - len(l.lstrip())
        if ind < indent:
            break
        if ind == indent and l.lstrip().startswith("- "):
            cur = {"with": {}, "raw": ""}
            steps.append(cur)
            l = " " * (indent + 2) + l.lstrip()[2:]
            ind = indent + 2
        if cur is None:
            continue
        cur["raw"] += l + "\n"
        body = l.strip()
        if ind == indent + 2 and ":" in body:
            key, _, val = body.partition(":")
            in_with = key == "with"
            if not in_with:
                cur[key.strip()] = strip_comment(val)
        elif in_with and ind == indent + 4 and ":" in body:
            key, _, val = body.partition(":")
            cur["with"][key.strip()] = strip_comment(val)
    return steps


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail else ""))
    if not cond:
        fails.append(name)


def run_helper():
    node = shutil.which("node")
    if not node:
        print("FAIL node not found on PATH")
        sys.exit(1)
    payload = json.dumps([files for _, _, files in FIXTURES])
    res = subprocess.run([node, "-e", DRIVER, HELPER], input=payload, capture_output=True, text=True, timeout=60)
    if res.returncode != 0:
        print("FAIL node driver exited", res.returncode)
        print(res.stderr)
        sys.exit(1)
    return json.loads(res.stdout)


def main():
    data = run_helper()
    for (name, expected, _), reason in zip(FIXTURES, data["out"]):
        got = RED if reason else NOT_RED
        check(f"{name}: {expected}", got == expected, reason or "")

    sanitized = data["extra"]["sanitized"]
    check("reason is log-sanitized", sanitized is not None and "\n" not in sanitized and "%0A" in sanitized
          and "%25" in sanitized, sanitized or "")
    leak = data["extra"]["leak"]
    check("credential reason names the pair, not the patch text",
          leak is not None and "(api+key)" in leak and "zzLeakMarker" not in leak and "9f8e7d" not in leak, leak or "")
    check("identifier parts split", data["extra"]["parts"] == ["api", "key", "foo", "token", "auth", "token"],
          str(data["extra"]["parts"]))

    wf = open(WORKFLOW, encoding="utf-8").read()
    check("workflow is pull_request_target", re.search(r"^\s+pull_request_target:", wf, re.M) is not None)
    check("workflow loads risk_match.js from trusted checkout",
          "'trusted-base', '.github', 'scripts', 'risk_match.js'" in wf and "fileReason(f)" in wf)
    steps = workflow_steps(wf)
    checkouts = [st for st in steps if st.get("uses", "").startswith("actions/checkout@")]
    check("workflow has exactly one checkout step", len(checkouts) == 1, str(len(checkouts)))
    if checkouts:
        w = checkouts[0]["with"]
        check("checkout ref is github.sha", w.get("ref") == "${{ github.sha }}", str(w.get("ref")))
        check("checkout is this repository", "repository" not in w, str(w.get("repository")))
        check("checkout does not persist credentials", w.get("persist-credentials") == "false")
        check("checkout path is trusted-base", w.get("path") == "trusted-base", str(w.get("path")))
        check("checkout is sparse to .github/scripts", w.get("sparse-checkout") == ".github/scripts")
        check("checkout runs before the labeler script", steps.index(checkouts[0]) < next(
            (i for i, st in enumerate(steps) if st.get("uses", "").startswith("actions/github-script@")), -1))
    check("no step writes into trusted-base other than the checkout",
          sum(1 for st in steps if "trusted-base" in st["raw"]) == 2 and not any(
              "run" in st for st in steps), str([st.get("name") for st in steps]))
    check("workflow never references PR head", not re.search(r"head\.(sha|ref)|head_ref|merge_commit|refs/pull", wf))
    check("workflow keeps no rules of its own", "KEYWORDS" not in wf and ".includes(k)" not in wf
          and "LOCKFILES" not in wf)
    check("workflow keeps cap, restore and downgrade removal",
          "FILE_LIST_CAP = 3000" in wf and "Restoring it" in wf and "DOWNGRADE_LABELS = ['risk:green', 'risk:yellow']" in wf
          and "removeLabel" in wf)
    check("workflow never removes risk:red or hold", not re.search(r"name:\s*(RED|'risk:red'|'hold')", wf))

    print(f"\n{len(FIXTURES)} fixtures, {len(fails)} failures")
    if fails:
        sys.exit(1)


if __name__ == "__main__":
    main()
