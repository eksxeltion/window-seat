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
//   2. Keyword rules on paths, skipped when the path is under tests/ (see
//      scanText; the same rules as for patches, see 4).
//   3. Missing patch: the API omits "patch" for binaries and for diffs it
//      considers too large. A file outside tests/ that is not a known binary
//      (see BINARY_EXTENSIONS) and changed but has no patch forces red, so a
//      large rewrite of js/receiver.js cannot slip through unread. Binaries
//      (png, ico...) fall back to the path rules only.
//   4. Patch rules, skipped when every path of the file is under tests/. Only
//      added and removed lines are scanned (not context lines or hunk
//      headers). Text is split into identifiers ([A-Za-z0-9_$] runs) and each
//      identifier into camelCase / snake_case parts (identifierParts):
//      a. Part keywords: a part equal to payment(s), stripe or secret(s), or a
//         part starting with auth or oauth (createPaymentIntent, loadStripe,
//         JWT_SECRET, isAuthenticated, Authorization, authMiddleware, oauth).
//         The auth prefix does not match the author family (author, authors,
//         authored, authoring, authority); authorize and authorization still
//         match.
//      b. Whole-word keywords, only for the noisy words key, token (and
//         delete, see DELETE_MATCHES_PARTS): the whole identifier (or path
//         word) must equal the word, optionally plural. "fooToken", "monkey",
//         "keyframe", "keyFrame" and "tokenizer" do not match.
//      c. Trailing credential noun: an identifier whose last part is secret,
//         password or passwd (JWT_SECRET, myPassword, db_passwd).
//      d. Credential pair: a credential qualifier immediately followed by a
//         credential noun ("apiKey", "api_key", "access_token", "AUTH_TOKEN",
//         "supabaseKey", "sessionToken", "private_key"), also when glued or
//         split oddly ("apikey", "APIkey", "GitHubToken"; see credentialIn).
//         Generic names such as "fooToken", "playerToken" or "csrfToken" have
//         no qualifier and do not match.
//   Reasons name only fixed vocabulary (the keyword, the qualifier+noun pair),
//   never text copied from the patch, because they end up in workflow logs.

'use strict';

const KEYWORDS = ['auth', 'key', 'secret', 'token', 'payment', 'stripe', 'delete'];

// Pending a decision by Stephane: should "delete" also match identifier parts
// (deleteCam, onDelete)? Currently false: "delete" is a whole word only, like
// key and token. Flip to true to match it as an exact part instead.
const DELETE_MATCHES_PARTS = false;

// Matched against single identifier parts.
const PART_PREFIX_KEYWORDS = ['auth', 'oauth'];
// "author" and its inflections are not auth. "authorize" / "authorization" are.
const isAuthorWord = (part) =>
  part.startsWith('author') &&
  !part.startsWith('authorize') &&
  !part.startsWith('authorization');

const partPrefixHit = (part) => {
  for (const pre of PART_PREFIX_KEYWORDS) {
    if (!part.startsWith(pre)) continue;
    if (pre === 'auth' && isAuthorWord(part)) continue;
    return pre;
  }
  return null;
};
const PART_EXACT_KEYWORDS = new Map([
  ['payment', 'payment'],
  ['payments', 'payment'],
  ['stripe', 'stripe'],
  ['secret', 'secret'],
  ['secrets', 'secret'],
]);
if (DELETE_MATCHES_PARTS) {
  PART_EXACT_KEYWORDS.set('delete', 'delete');
  PART_EXACT_KEYWORDS.set('deletes', 'delete');
}

// Matched only against a whole identifier or path word (noisy as parts).
const WHOLE_WORD_KEYWORDS = DELETE_MATCHES_PARTS ? ['key', 'token'] : ['key', 'token', 'delete'];

// An identifier whose last part is one of these is red (JWT_SECRET, myPassword).
const TRAILING_CREDENTIAL_NOUNS = new Set(['secret', 'password', 'passwd']);

// Extensions of binary files. The API never has a patch for these, so a
// missing patch on them is expected and only the path rules apply. Any other
// file (text, unknown extension, or no extension) that changed but has no
// patch forces red.
const BINARY_EXTENSIONS = new Set([
  'avif', 'bin', 'bmp', 'dll', 'dylib', 'eot', 'exe', 'gif', 'gz', 'ico', 'jar',
  'jpeg', 'jpg', 'lockb', 'mov', 'mp3', 'mp4', 'ogg', 'otf', 'pdf', 'png', 'so',
  'tgz', 'tif', 'tiff', 'ttf', 'wasm', 'wav', 'webm', 'webp', 'woff', 'woff2', 'zip',
]);

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
  'supabase',
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

// "apikey" -> "api+key". Values come only from the fixed lists above, so they
// are safe to log.
const GLUED = new Map();
for (const q of CREDENTIAL_QUALIFIERS) {
  for (const n of CREDENTIAL_NOUNS) {
    if (!GLUED.has(q + n)) GLUED.set(q + n, `${q}+${n}`);
  }
}
// Longest window of consecutive identifier parts joined when looking for a
// qualifier + noun pair ("GitHubToken" -> git, hub, token).
const MAX_WINDOW = 4;

const WHOLE_WORD_FORMS = new Map();
for (const k of WHOLE_WORD_KEYWORDS) {
  WHOLE_WORD_FORMS.set(k, k);
  WHOLE_WORD_FORMS.set(`${k}s`, k);
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

// Returns the matched "qualifier+noun" pair if the identifier looks like a
// credential, else null. Any window of up to MAX_WINDOW consecutive parts is
// joined and compared with the glued pairs, so "apiKey" (api, key), "AUTH_TOKEN"
// (auth, token), "apikey" (apikey), "GitHubToken" (git, hub, token) and
// "APIkey" (ap, ikey) all match, while "fooToken" (footoken, token),
// "playerToken", "monkey" and "keyFrame" do not.
// The identifier itself is never returned: it may be a credential value
// copied from a string literal, and the reason ends up in workflow logs.
const credentialIn = (ident) => {
  const parts = identifierParts(ident);
  for (let i = 0; i < parts.length; i++) {
    let joined = '';
    for (let j = i; j < parts.length && j < i + MAX_WINDOW; j++) {
      joined += parts[j];
      const pair = GLUED.get(joined);
      if (pair) return pair;
    }
  }
  return null;
};

// In code, identifiers are runs of [A-Za-z0-9_$].
const CODE_WORDS = /[A-Za-z0-9_$]+/g;
// In paths, any non-alphanumeric character separates whole words.
const PATH_WORDS = /[A-Za-z0-9]+/g;

// Returns a fixed-vocabulary reason if one identifier matches a part rule.
const identifierHit = (ident) => {
  const parts = identifierParts(ident);
  for (const part of parts) {
    const k = PART_EXACT_KEYWORDS.get(part);
    if (k) return `keyword "${k}" in identifier part`;
    const prefix = partPrefixHit(part);
    if (prefix) return `keyword "${prefix}" in identifier part`;
  }
  if (parts.length > 0 && TRAILING_CREDENTIAL_NOUNS.has(parts[parts.length - 1])) {
    return `identifier ending in "${parts[parts.length - 1]}"`;
  }
  const pair = credentialIn(ident);
  if (pair) return `credential-like identifier (${pair})`;
  return null;
};

// Scan text for keywords and credential identifiers.
// wordSplit: regex whose matches are the "words" for the whole-word rule.
const scanText = (text, wordSplit) => {
  const s = String(text);
  for (const w of s.match(wordSplit) || []) {
    const k = WHOLE_WORD_FORMS.get(w.toLowerCase());
    if (k) return `keyword "${k}"`;
  }
  for (const ident of s.match(CODE_WORDS) || []) {
    const hit = identifierHit(ident);
    if (hit) return hit;
  }
  return null;
};

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

// Lowercase extension of the basename ("" if none); ".gitignore" -> "gitignore".
const extension = (p) => {
  const base = basename(p);
  const i = base.lastIndexOf('.');
  return i < 0 ? '' : base.slice(i + 1).toLowerCase();
};

// Anything not known to be binary is expected to have a patch.
const isTextPath = (p) => !BINARY_EXTENSIONS.has(extension(p));

// Returns a short reason string if the patch matches a rule, else null.
// A missing patch is handled by fileReason.
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
  // No patch on a changed text file: the content was not seen, so fail closed.
  // changes === 0 (a pure rename, an empty new file) legitimately has no patch.
  if (!f.patch && f.changes !== 0 && paths.some(isTextPath)) {
    return sanitizeLog(`text file changed but the API returned no patch: ${f.filename}`);
  }
  const r = checkPatch(f.patch);
  if (r) return sanitizeLog(`${r} of ${f.filename}`);
  return null;
};

module.exports = {
  KEYWORDS,
  DELETE_MATCHES_PARTS,
  WHOLE_WORD_KEYWORDS,
  BINARY_EXTENSIONS,
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
