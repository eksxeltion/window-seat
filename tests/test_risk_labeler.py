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
    # CURRENT CHOICE, not an oversight: DELETE_MATCHES_PARTS = false in risk_match.js keeps "delete" whole-word only,
    # so deleteCam is not red. Stephane decides whether that changes; if the constant is flipped, flip this fixture.
    ("deleteCam (DELETE_MATCHES_PARTS = false) and tokenizer identifiers", NOT_RED, [
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
    ("whole word secrets alone", RED, [{"filename": "js/j2.js", "patch": patch("+// rotate the secrets")}]),
    ("whole word stripe and payment", RED, [{"filename": "js/k.js", "patch": patch("+import stripe from 'x'; // payment")}]),
    ("x-api-key header", RED, [{"filename": "js/l.js", "patch": patch("+h['x-api-key'] = v;")}]),
    ("one red file among clean ones", RED, [
        {"filename": "js/sender.js", "patch": patch("+const delay = 5000;")},
        {"filename": "tests/test_x.py", "patch": patch("+token = 1")},
        {"filename": "js/auth.js", "patch": patch("+x()")},
    ]),

    # Kent P1: part matching for auth, oauth, payment(s), stripe, secret(s); credential pairs; trailing nouns.
    ("P1 app.use(authMiddleware)", RED, [{"filename": "js/server.js", "patch": patch("+app.use(authMiddleware);")}]),
    ("P1 headers.Authorization bearer", RED, [
        {"filename": "js/api.js", "patch": patch("+headers.Authorization = 'Bearer ' + t;")}]),
    ("P1 isAuthenticated", RED, [{"filename": "js/session.js", "patch": patch("+if (!isAuthenticated()) return;")}]),
    ("P1 import from ./oauth.js", RED, [{"filename": "js/app.js", "patch": patch("+import { login } from './oauth.js';")}]),
    ("P1 createPaymentIntent(amount)", RED, [{"filename": "js/shop.js", "patch": patch("+await createPaymentIntent(amount);")}]),
    ("P1 loadStripe(pk)", RED, [{"filename": "js/shop.js", "patch": patch("+const s = await loadStripe(pk);")}]),
    ("P1 jwt.sign(payload, JWT_SECRET)", RED, [{"filename": "js/jwt.js", "patch": patch("+jwt.sign(payload, JWT_SECRET);")}]),
    ("P1 supabaseKey", RED, [{"filename": "js/db.js", "patch": patch("+createClient(url, supabaseKey);")}]),
    ("P1 sessionToken", RED, [{"filename": "js/s.js", "patch": patch("+let sessionToken;")}]),
    ("P1 private_key", RED, [{"filename": "tools/sign.py", "patch": patch("+private_key = load()")}]),
    ("P1 myPassword", RED, [{"filename": "js/p.js", "patch": patch("+const myPassword = v;")}]),
    ("P1 db_passwd", RED, [{"filename": "tools/db.py", "patch": patch("+db_passwd = v")}]),
    ("P1 payments part", RED, [{"filename": "js/q.js", "patch": patch("+listPayments();")}]),
    ("P1 path js/authMiddleware.js", RED, [{"filename": "js/authMiddleware.js", "patch": patch("+x()")}]),
    ("P1 path js/oauth.js", RED, [{"filename": "js/oauth.js", "patch": patch("+x()")}]),
    ("P1 path js/stripeCheckout.js", RED, [{"filename": "js/stripeCheckout.js", "patch": patch("+x()")}]),
    ("P1 tests/ patch with auth, stripe, payment, secret parts", NOT_RED, [
        {"filename": "tests/test_shop.py", "patch": patch(
            "+    loadStripe = 1", "+    createPaymentIntent = 2", "+    isAuthenticated = True", "+    JWT_SECRET = 'x'")}]),
    ("P1 tests/ filenames with auth and stripe parts", NOT_RED, [
        {"filename": "tests/test_authMiddleware.py", "patch": patch("+x = 1")},
        {"filename": "tests/fake_stripeCheckout.js", "patch": patch("+x()")},
    ]),

    # Kent P3: a changed text file without a patch fails closed.
    ("P3 large rewrite of js/receiver.js with no patch", RED, [{"filename": "js/receiver.js", "changes": 9000}]),
    ("P3 html without patch", RED, [
        {"filename": "receiver.html", "changes": 5000}]),
    ("P3 py without patch", RED, [{"filename": "tools/gen.py", "changes": 4000}]),
    ("P3 json without patch", RED, [{"filename": "data/cams.json", "changes": 4000}]),
    ("P3 css without patch", RED, [{"filename": "css/style.css", "changes": 4000}]),
    ("P3 toml without patch", RED, [{"filename": "config.toml", "changes": 4000}]),
    ("P3 yml without patch", RED, [{"filename": "deploy/app.yml", "changes": 4000}]),
    ("P3 yaml without patch", RED, [{"filename": "docs/conf.yaml", "changes": 4000}]),
    ("P3 md without patch", RED, [{"filename": "docs/notes.md", "changes": 4000}]),
    ("P3 svg without patch", RED, [{"filename": "icons/logo.svg", "changes": 4000}]),
    ("P3 sh without patch", RED, [{"filename": "scripts/build.sh", "changes": 4000}]),
    ("P3 txt without patch", RED, [{"filename": "docs/a.txt", "changes": 4000}]),
    ("P3 extensionless without patch", RED, [{"filename": "scripts/build", "changes": 4000}]),
    ("P3 unknown extension Containerfile.dev without patch", RED, [{"filename": "Containerfile.dev", "changes": 4000}]),
    ("P3 unknown extension .properties without patch", RED, [
        {"filename": "config/service.properties", "changes": 4000}]),
    ("P3 missing patch and unknown changes", RED, [{"filename": "js/sender.js"}]),
    ("P3 binary under assets/ with no patch", NOT_RED, [{"filename": "assets/hero.png", "changes": 0}]),
    ("P3 binary jpg with changes and no patch", NOT_RED, [{"filename": "assets/photo.jpg", "changes": 120}]),
    ("P3 png and woff2 with changes and no patch", NOT_RED, [
        {"filename": "icons/icon-512.png", "changes": 300}, {"filename": "assets/font.woff2", "changes": 80}]),
    ("P3 tests/ text file with no patch", NOT_RED, [{"filename": "tests/snapshot_cams.csv", "changes": 9000}]),
    ("P3 tests/ js file with no patch", NOT_RED, [{"filename": "tests/fake_youtube.js", "changes": 9000}]),
    ("P3 pure rename (changes 0, no patch)", NOT_RED, [
        {"filename": "js/cam_menu.js", "previous_filename": "js/menu.js", "changes": 0}]),
    ("P3 empty patch string on changed text file", RED, [{"filename": "js/x.js", "patch": "", "changes": 10}]),

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
    deleteParts: m.DELETE_MATCHES_PARTS,
  };
  process.stdout.write(JSON.stringify({ out, extra }));
});
"""

# Runs the github-script body of the workflow (extracted from the YAML, so comments cannot satisfy anything) against
# mocked github/core/context/require objects, one scenario at a time, and records every API call.
SCRIPT_DRIVER = r"""
const realPath = require('path');
const helperFile = process.argv[1];
let input = '';
process.stdin.on('data', (d) => { input += d; });
process.stdin.on('end', async () => {
  const { script, scenarios } = JSON.parse(input);
  const AsyncFunction = Object.getPrototypeOf(async () => {}).constructor;
  const WS = '/fake-ws';
  process.env.GITHUB_WORKSPACE = WS;
  const helperPath = realPath.join(WS, 'trusted-base', '.github', 'scripts', 'risk_match.js');
  const results = {};
  for (const sc of scenarios) {
    process.env.CHECKOUT_OUTCOME = sc.checkout || 'success';
    const calls = [];
    const fail = (status, msg) => { const e = new Error(msg); e.status = status; return e; };
    const rec = (name, fn) => async (params) => { calls.push([name, params]); return fn ? fn(params) : { data: {} }; };
    const github = {
      rest: {
        pulls: {
          get: rec('pulls.get', () => {
            if (sc.pullsGetFails) throw fail(503, 'pulls.get 503');
            return { data: { changed_files: sc.files.length } };
          }),
          listFiles: rec('pulls.listFiles'),
        },
        issues: {
          listLabelsOnIssue: rec('issues.listLabelsOnIssue'),
          addLabels: rec('issues.addLabels', (p) => {
            if (sc.addRedFails && p.labels.includes('risk:red')) throw fail(500, 'addLabels red 500');
            return { data: [] };
          }),
          removeLabel: rec('issues.removeLabel'),
          getLabel: rec('issues.getLabel', (p) => {
            if (sc.holdMissing) throw fail(404, 'label not found');
            return { data: { name: p.name } };
          }),
        },
      },
      paginate: async (fn, params) => {
        if (fn === github.rest.pulls.listFiles) {
          calls.push(['paginate.listFiles', params]);
          if (sc.listFilesFails) throw fail(502, 'listFiles 502 Bad Gateway');
          return sc.files;
        }
        if (fn === github.rest.issues.listLabelsOnIssue) {
          calls.push(['paginate.listLabelsOnIssue', params]);
          return sc.labels.map((name) => ({ name }));
        }
        throw new Error('unexpected paginate');
      },
    };
    const logs = [];
    const core = {
      info: (m) => logs.push(['info', m]),
      warning: (m) => logs.push(['warning', m]),
      error: (m) => logs.push(['error', m]),
    };
    const context = {
      repo: { owner: 'o', repo: 'r' },
      payload: {
        action: sc.action || 'synchronize',
        label: sc.removedLabel ? { name: sc.removedLabel } : undefined,
        pull_request: { number: 7, changed_files: sc.files.length },
      },
    };
    const fakeRequire = (id) => {
      if (id === 'path') return realPath;
      if (id === helperPath) {
        if (sc.helper === 'throw') throw new Error("Cannot find module '" + helperPath + "'");
        if (sc.helper === 'empty') return {};
        return require(helperFile);
      }
      throw new Error('unexpected require: ' + id);
    };
    let threw = null;
    try {
      await new AsyncFunction('github', 'context', 'core', 'require', script)(github, context, core, fakeRequire);
    } catch (e) {
      threw = String((e && e.message) || e);
    }
    results[sc.name] = { threw, calls, logs };
  }
  process.stdout.write(JSON.stringify(results));
});
"""

SCENARIOS = [
    {"name": "trusted checkout failed", "checkout": "failure", "files": [], "labels": ["risk:green"]},
    {"name": "pulls.get fails", "pullsGetFails": True, "files": [], "labels": ["risk:green"]},
    {"name": "helper require fails", "helper": "throw", "files": [], "labels": ["risk:green"]},
    {"name": "helper exports nothing", "helper": "empty", "files": [], "labels": []},
    {"name": "listFiles 5xx", "listFilesFails": True, "files": [], "labels": ["risk:green"]},
    {"name": "listFiles 5xx, hold label missing", "listFilesFails": True, "holdMissing": True, "files": [],
     "labels": []},
    {"name": "listFiles 5xx, adding red fails too", "listFilesFails": True, "addRedFails": True, "files": [],
     "labels": []},
    {"name": "red match", "files": [{"filename": "js/shop.js", "patch": "@@ -1 +1 @@\n+loadStripe(pk);", "changes": 1}],
     "labels": ["risk:green", "risk:yellow"]},
    {"name": "clean", "files": [{"filename": "js/a.js", "patch": "@@ -1 +1 @@\n+const delay = 1;", "changes": 1}],
     "labels": ["risk:green"]},
    {"name": "red removed while still matching", "action": "unlabeled", "removedLabel": "risk:red",
     "files": [{"filename": ".github/workflows/ci.yml", "patch": "@@ -1 +1 @@\n+x", "changes": 1}],
     "labels": ["hold"]},
]

fails = []


def extract_script(text):
    """Return the github-script `script: |` block scalar, dedented. It is executed, so JS comments are inert."""
    lines = text.splitlines()
    idx = next(i for i, l in enumerate(lines) if l.strip() == "script: |")
    key_indent = len(lines[idx]) - len(lines[idx].lstrip())
    body = []
    for l in lines[idx + 1:]:
        if l.strip() and len(l) - len(l.lstrip()) <= key_indent:
            break
        body.append(l)
    block_indent = min(len(l) - len(l.lstrip()) for l in body if l.strip())
    return "\n".join(l[block_indent:] for l in body)


def run_workflow_script(wf):
    payload = json.dumps({"script": extract_script(wf), "scenarios": SCENARIOS})
    res = subprocess.run([shutil.which("node"), "-e", SCRIPT_DRIVER, HELPER], input=payload, capture_output=True,
                         text=True, timeout=60)
    if res.returncode != 0:
        print("FAIL workflow script driver exited", res.returncode)
        print(res.stderr)
        sys.exit(1)
    return json.loads(res.stdout)


def labels_added(r):
    return [lab for name, p in r["calls"] if name == "issues.addLabels" for lab in p["labels"]]


def labels_removed(r):
    return [p["name"] for name, p in r["calls"] if name == "issues.removeLabel"]


def check_workflow_behavior(wf):
    res = run_workflow_script(wf)
    for name in ("trusted checkout failed", "pulls.get fails", "helper require fails", "helper exports nothing",
                 "listFiles 5xx"):
        r = res[name]
        check(f"fail closed [{name}]: adds risk:red", "risk:red" in labels_added(r), str(labels_added(r)))
        names = [n if n != "issues.addLabels" else "add:" + ",".join(p["labels"]) for n, p in r["calls"]]
        check(f"fail closed [{name}]: checks hold exists, then adds it",
              "issues.getLabel" in names and "add:hold" in names
              and names.index("issues.getLabel") < names.index("add:hold")
              and all(p["name"] == "hold" for n, p in r["calls"] if n == "issues.getLabel"), str(names))
        check(f"fail closed [{name}]: rethrows", r["threw"] is not None, str(r["threw"]))
        check(f"fail closed [{name}]: red added before hold",
              "risk:red" in labels_added(r) and "hold" in labels_added(r)
              and labels_added(r).index("risk:red") < labels_added(r).index("hold"))
    r = res["trusted checkout failed"]
    check("checkout failed: rethrows an outcome error", "checkout outcome is failure" in (r["threw"] or ""), r["threw"])
    check("checkout failed: never lists files", not any(n == "paginate.listFiles" for n, _ in r["calls"]))
    r = res["pulls.get fails"]
    check("pulls.get fails: rethrows the original error", r["threw"] == "pulls.get 503", r["threw"])
    r = res["helper require fails"]
    check("fail closed: rethrows the original require error", "Cannot find module" in (r["threw"] or ""), r["threw"])
    r = res["listFiles 5xx"]
    check("fail closed: rethrows the original 5xx error", r["threw"] == "listFiles 502 Bad Gateway", r["threw"])
    r = res["listFiles 5xx, hold label missing"]
    check("hold missing: still adds risk:red", labels_added(r) == ["risk:red"], str(labels_added(r)))
    check("hold missing: does not create hold", "hold" not in labels_added(r))
    check("hold missing: rethrows the original error, not the 404", r["threw"] == "listFiles 502 Bad Gateway",
          r["threw"])
    r = res["listFiles 5xx, adding red fails too"]
    check("red add fails: still tries hold", "hold" in labels_added(r), str(labels_added(r)))
    check("red add fails: rethrows the original error", r["threw"] == "listFiles 502 Bad Gateway", r["threw"])
    r = res["red match"]
    check("red match: adds risk:red, no error", labels_added(r) == ["risk:red"] and r["threw"] is None,
          f"{labels_added(r)} {r['threw']}")
    check("red match: removes green and yellow", sorted(labels_removed(r)) == ["risk:green", "risk:yellow"],
          str(labels_removed(r)))
    check("red match: does not add hold", "hold" not in labels_added(r))
    r = res["clean"]
    check("clean: no label changes, no error",
          not labels_added(r) and not labels_removed(r) and r["threw"] is None)
    r = res["red removed while still matching"]
    check("unlabeled red: restores it", labels_added(r) == ["risk:red"] and r["threw"] is None, str(labels_added(r)))
    check("unlabeled red: logs the restore", any("Restoring it" in m for _, m in r["logs"]))
    check("no scenario ever removes risk:red or hold",
          not any(lab in ("risk:red", "hold") for r in res.values() for lab in labels_removed(r)))
    check("failure logs are sanitized (no raw newlines)",
          all("\n" not in m and "\r" not in m for r in res.values() for _, m in r["logs"]))


def strip_comment(value):
    """Drop a trailing ' # comment' from a YAML scalar (values here never contain ' #')."""
    return value.split(" #", 1)[0].strip()


def workflow_steps(text):
    """Minimal parser for the single job's steps: returns dicts with step keys, 'with' and 'env' dicts and the raw text.
    Comment lines are ignored, so a commented-out key can not satisfy a check."""
    lines = [l for l in text.splitlines() if not l.strip().startswith("#")]
    try:
        start = next(i for i, l in enumerate(lines) if l.strip() == "steps:")
    except StopIteration:
        return []
    indent = len(lines[start]) - len(lines[start].lstrip()) + 2
    steps, cur, section = [], None, None
    for l in lines[start + 1:]:
        if not l.strip():
            continue
        ind = len(l) - len(l.lstrip())
        if ind < indent:
            break
        if ind == indent and l.lstrip().startswith("- "):
            cur = {"with": {}, "env": {}, "raw": ""}
            steps.append(cur)
            l = " " * (indent + 2) + l.lstrip()[2:]
            ind = indent + 2
        if cur is None:
            continue
        cur["raw"] += l + "\n"
        body = l.strip()
        if ind == indent + 2 and ":" in body:
            key, _, val = body.partition(":")
            key = key.strip()
            section = key if key in ("with", "env") else None
            if section is None:
                cur[key] = strip_comment(val)
        elif section and ind == indent + 4 and ":" in body:
            key, _, val = body.partition(":")
            cur[section][key.strip()] = strip_comment(val)
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
        check("checkout has id trusted and continue-on-error true",
              checkouts[0].get("id") == "trusted" and checkouts[0].get("continue-on-error") == "true")
        scripts = [st for st in steps if st.get("uses", "").startswith("actions/github-script@")]
        check("labeler script gets CHECKOUT_OUTCOME from the trusted checkout",
              len(scripts) == 1 and scripts[0]["env"].get("CHECKOUT_OUTCOME") == "${{ steps.trusted.outcome }}"
              and "if" not in scripts[0], str(scripts[0]["env"]) if scripts else "no script step")
        check("checkout runs before the labeler script", steps.index(checkouts[0]) < next(
            (i for i, st in enumerate(steps) if st.get("uses", "").startswith("actions/github-script@")), -1))
    check("no step writes into trusted-base other than the checkout",
          sum(1 for st in steps if "trusted-base" in st["raw"]) == 2 and not any(
              "run" in st for st in steps), str([st.get("name") for st in steps]))
    check("workflow never references PR head", not re.search(r"head\.(sha|ref)|head_ref|merge_commit|refs/pull", wf))
    check("workflow keeps no rules of its own", "KEYWORDS" not in wf and ".includes(k)" not in wf
          and "LOCKFILES" not in wf)
    check("delete stays whole-word only (DELETE_MATCHES_PARTS = false)", data["extra"]["deleteParts"] is False)
    check("workflow keeps cap, restore and downgrade removal",
          "FILE_LIST_CAP = 3000" in wf and "Restoring it" in wf and "DOWNGRADE_LABELS = ['risk:green', 'risk:yellow']" in wf
          and "removeLabel" in wf)
    remove_calls = re.findall(r"removeLabel\(\{(.*?)\}\)", wf, re.S)
    check("workflow removeLabel calls only take the downgrade loop variable",
          len(remove_calls) == 1 and all(re.search(r"^\s*name,\s*$", c, re.M) and not re.search(
              r"RED|HOLD|risk:red|'hold'", c) for c in remove_calls)
          and "for (const name of DOWNGRADE_LABELS)" in wf, str(len(remove_calls)))
    check_workflow_behavior(wf)

    print(f"\n{len(FIXTURES)} fixtures, {len(fails)} failures")
    if fails:
        sys.exit(1)


if __name__ == "__main__":
    main()
