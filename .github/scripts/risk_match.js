// Risk labeler matching rules.
//
// This is the single place that decides whether one changed file in a pull
// request should force the risk:red label. It is used by
// .github/workflows/risk-labeler.yml (loaded from the trusted base / default
// branch checkout, never from the PR head) and by tests/test_risk_labeler.py.
//
// Pure functions only: no network, no filesystem, no process access.
//
// Rules, in order, for each changed file (both filename and previous_filename):
//   1. Path rules (always applied, including under tests/):
//      vercel.json, anything under .github/, package.json, lockfiles, env files.
//   2. Keyword rules, skipped when the path is under tests/:
//      a. keyword-in-path: a keyword appears as a whole word in the path, where
//         path words are separated by any non-alphanumeric character
//         ("secret-config.js", "api_key.json" match; "monkey.js",
//         "keyframes.css", "fooToken.js" do not).
//      b. credential identifier in the path (see below).
//   3. Patch rules, skipped when every path of the file is under tests/:
//      only added and removed lines are scanned (not context lines or hunk
//      headers).
//      a. whole-word keyword: an identifier equal to a keyword, optionally
//         plural ("token", "KEY", "secrets", "delete"). Identifiers are runs of
//         [A-Za-z0-9_$], so "fooToken", "monkey", "keyframe" and "foo_token"
//         do not match.
//      b. credential identifier: an identifier whose camelCase / snake_case
//         parts contain a credential qualifier immediately followed
//         by a credential noun ("apiKey", "api_key", "access_token",
//         "AUTH_TOKEN", "clientSecret", "GITHUB_TOKEN"), or a part that is such
//         a pair glued together ("apikey", "APIKEY"). Generic names such as
//         "fooToken", "playerToken" or "keyFrame" have no qualifier and do not
//         match.

'use strict';

const KEYWORDS = ['auth', 'key', 'secret', 'token', 'payment', 'stripe', 'delete'];

const LOCKFILES = new Set([
  'package-lock.json',
  'yarn.lock',
  'pnpm-lock.yaml',
  'bun.lockb',
  'npm-shrinkwrap.json',
]);

// Words that, placed right before a credential noun, mark a credential.
const CREDENTIAL_QUALIFIERS = [
  'access',
  'admin',
  'api',
  'app',
  'auth',
  'aws',
  'bearer',
  'client',
  'deploy',
  'encryption',
  'gh',
  'github',
  'id',
  'master',
  'npm',
  'oauth',
  'personal',
  'private',
  'refresh',
  'secret',
  'service',
  'session',
  'signing',
  'stripe',
  'vercel',
  'webhook',
];

const CREDENTIAL_NOUNS = [
  'credential',
  'credentials',
  'key',
  'keys',
  'passwd',
  'password',
  'pwd',
  'secret',
  'secrets',
  'token',
  'tokens',
];

const QUALIFIER_SET = new Set(CREDENTIAL_QUALIFIERS);
const NOUN_SET = new Set(CREDENTIAL_NOUNS);
const GLUED_SET = new Set();
for (const q of CREDENTIAL_QUALIFIERS) {
  for (const n of CREDENTIAL_NOUNS) GLUED_SET.add(q + n);
}

const KEYWORD_FORMS = new Map();
for (const k of KEYWORDS) {
  KEYWORD_FORMS.set(k, k);
  KEYWORD_FORMS.set(`${k}s`, k);
}

const TESTS_PREFIX = 'tests/';

// Escape untrusted text so it cannot inject workflow commands via logs.
const sanitizeLog = (text) =>
  String(text).replace(/%/g, '%25').replace(/\r/g, '%0D').replace(/\n/g, '%0A');

const basename = (p) => {
  const parts = String(p).split('/');
  return parts[parts.length - 1];
};

const isTestsPath = (p) => typeof p === 'string' && p.startsWith(TESTS_PREFIX);

// Split an identifier into lowercase parts on _, $, digits and camelCase
// boundaries: "apiKey" -> [api, key], "APIKey" -> [api, key],
// "AUTH_TOKEN" -> [auth, token], "fooToken" -> [foo, token].
const identifierParts = (ident) => {
  const parts = [];
  for (const chunk of String(ident).split(/[^A-Za-z]+/)) {
    if (!chunk) continue;
    const m = chunk.match(/[A-Z]+(?![a-z])|[A-Z]?[a-z]+/g);
    if (m) for (const p of m) parts.push(p.toLowerCase());
  }
  return parts;
};

// Returns a description if the identifier looks like a credential, else null.
const credentialIn = (ident) => {
  const parts = identifierParts(ident);
  for (let i = 0; i < parts.length; i++) {
    if (GLUED_SET.has(parts[i])) return ident;
    if (i + 1 < parts.length && QUALIFIER_SET.has(parts[i]) && NOUN_SET.has(parts[i + 1])) {
      return ident;
    }
  }
  return null;
};

// Scan text for whole-word keywords and credential identifiers.
// wordSplit: regex whose matches are the "words" for the whole-word rule.
const scanText = (text, wordSplit) => {
  const s = String(text);
  for (const w of s.match(wordSplit) || []) {
    const k = KEYWORD_FORMS.get(w.toLowerCase());
    if (k) return `keyword "${k}"`;
  }
  for (const ident of s.match(/[A-Za-z0-9_$]+/g) || []) {
    const c = credentialIn(ident);
    if (c) return `credential identifier "${c}"`;
  }
  return null;
};

// In code, identifiers are runs of [A-Za-z0-9_$].
const CODE_WORDS = /[A-Za-z0-9_$]+/g;
// In paths, any non-alphanumeric character separates words.
const PATH_WORDS = /[A-Za-z0-9]+/g;

// Only added and removed lines carry the change; skip context lines, hunk
// headers and "\ No newline at end of file" markers. The API patch has no
// "---"/"+++" file headers, so a line starting with "---" is a removed line
// whose content starts with "--" and must be kept.
const changedLines = (patch) =>
  String(patch)
    .split(/\r?\n/)
    .filter((l) => l.startsWith('+') || l.startsWith('-'))
    .map((l) => l.slice(1))
    .join('\n');

const isEnvFile = (base) => base === '.env' || base.startsWith('.env.') || base.endsWith('.env');

// Returns a short reason string if the path matches a rule, else null.
const checkPath = (p) => {
  if (!p) return null;
  const base = basename(p);
  if (p === 'vercel.json' || base === 'vercel.json') return 'vercel.json';
  if (p.startsWith('.github/')) return '.github path';
  if (base === 'package.json') return 'package.json';
  if (LOCKFILES.has(base)) return 'lockfile';
  if (isEnvFile(base)) return 'env file';
  if (isTestsPath(p)) return null;
  const hit = scanText(p, PATH_WORDS);
  if (hit) return `${hit} in path`;
  return null;
};

// Returns a short reason string if the patch matches a rule, else null.
const checkPatch = (patch) => {
  if (!patch) return null;
  const hit = scanText(changedLines(patch), CODE_WORDS);
  if (hit) return `${hit} in patch`;
  return null;
};

// f: an entry from the pulls.listFiles API ({filename, previous_filename, patch}).
// Returns a log-safe reason string if this file forces risk:red, else null.
const fileReason = (f) => {
  const paths = [f.filename, f.previous_filename].filter((p) => typeof p === 'string' && p);
  for (const p of paths) {
    const r = checkPath(p);
    if (r) return sanitizeLog(`${r}: ${p}`);
  }
  // Patch keywords are ignored only when every path of the file is under tests/,
  // so moving a file into tests/ does not hide its content.
  if (paths.length > 0 && paths.every(isTestsPath)) return null;
  const r = checkPatch(f.patch);
  if (r) return sanitizeLog(`${r} of ${f.filename}`);
  return null;
};

module.exports = {
  KEYWORDS,
  LOCKFILES,
  CREDENTIAL_QUALIFIERS,
  CREDENTIAL_NOUNS,
  sanitizeLog,
  identifierParts,
  credentialIn,
  checkPath,
  checkPatch,
  fileReason,
};
