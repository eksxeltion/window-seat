# Window Seat telemetry plan v1

Status: for review. Written Nov 17, 2026 against build 2026-11-16.12, revised after reviews by Claude Code and Codex. Tools: PostHog (usage) and Sentry (errors), both on free tiers. This plan follows the Telemetry section of the Android spec and extends it to today's web app and TV receiver. Event names stay the same as the spec where the spec has them.

## 1. What we want to learn

In priority order.

**A. Cast reliability (the key metric).** Is casting good enough to share?

1. How many Cast connect attempts are made, and what share succeed (cancels left out)?
2. When a connect fails, which CAF error code?
3. How often do sessions end without anyone asking, and after how long? Was the session paused or showing the pause warning?
4. How long from tapping Cast until the phone hears the TV's first state, and how long the TV takes from page load to its first cam frame?
5. How often the phone rejoins an existing session, and whether it then hears the TV.
6. How often commands fail to send or get no reply, and how often the custom channel never opens.

**B. Usage.**

1. Sessions per day, and session length.
2. Local playback on the phone vs Cast to the TV, and browser tab vs installed app (PWA).
3. Which cams and mixes are played most, and which cams fail most.
4. How often pause, pin, next, previous, shuffle, mute and the 30s/60s interval are used.
5. How often the cam menu and the mix picker are opened and used.
6. How often the pause timeout warns, how often "Keep paused" is tapped, and how often the session ends from the timeout.
7. How often debug mode is turned on.
8. What share of visitors can't cast at all (no Cast SDK in the browser).

**C. Errors.**

1. Uncaught JS errors and unhandled promise rejections on the phone page and on the TV receiver (Sentry).
2. YouTube player errors by video ID and error code (cams and music, phone and TV).
3. List load failures, and how often we fall back to the saved list or the built-in `js/data.js`.

## 2. Privacy rules

- **No personal data.** No names, emails, accounts, free text, Cast device names, or URLs with query strings or fragments.
- **One pseudonymous ID.** PostHog's random anonymous ID is the `install_id`. It is a random value in this browser's localStorage, never linked to a person, and we never call `identify`. It identifies a browser storage context, not a human.
- **No IP storage and no IP geolocation.** PostHog project setting "Discard client IP data" is on, and every event carries `$geoip_disable: true`. Sentry: `sendDefaultPii: false`, which in the current browser SDK means Sentry does not infer or store the client IP; the project's "Prevent storing of IP addresses" setting should also be on (to check in the Sentry UI). The providers still see the connection IP while receiving data; they don't store it.
- **What PostHog sends automatically is trimmed.** `before_send` removes `$referrer`, `$referring_domain`, `$initial_*` and `$timezone`, and strips query strings and fragments from `$current_url`. Kept: browser, OS, screen size, and `$browser_language`. Location is limited to `locale_region`, the region part of the browser language (for example `US` from `en-US`). That is a language preference, not a country, and no IP lookup is involved.
- **No session replay, autocapture, heatmaps, surveys, web vitals, feature flags or remote config.** posthog-js runs with `autocapture: false`, `capture_pageview: false`, `capture_pageleave: false`, `disable_session_recording: true`, `capture_exceptions: false` (Sentry owns errors), `disable_surveys: true`, `enable_heatmaps: false`, `capture_dead_clicks: false`, `capture_performance: false`, `advanced_disable_flags: true`, `disable_external_dependency_loading: true`, `person_profiles: "never"`, `respect_dnt: true`.
- **Do Not Track and Global Privacy Control win.** If `navigator.doNotTrack`, `window.doNotTrack` or `navigator.msDoNotTrack` is `"1"` or `"yes"`, or `navigator.globalPrivacyControl === true`, telemetry is off, no ID is created, no SDK is loaded, and the About switch is off and disabled with the note "Off because your browser asks sites not to track you". No URL parameter can override it.
- **On by default, with an off switch in About.** This is open question 6 in the Android spec, and it is reversible (one constant). Flagged for Stephane: storing an ID without asking is a consent question in the EU, which matters once the receiver is published. Options are listed in section 10.
- **Off means off.** When off, the SDKs are not downloaded and nothing goes to either host. Turning it off while running: our wrapper's gate flips first (every capture and Sentry `beforeSend` returns nothing), queued events are dropped, `posthog.opt_out_capturing()` is called, PostHog's localStorage keys are removed, and a `telemetry` message tells the TV to stop. No event is sent when turning it off. Turning it back on calls `opt_in_capturing()` and sends `telemetry_enabled`. The switch is one setting for every open copy of the app (tabs and the installed window): the others follow it through the `storage` event, and re-read it whenever they come back into view, so an off in one copy turns the others off too, and each tells its own TV. Only the copy where the switch was flipped sends `telemetry_enabled`.
- **Test data is marked.** Every event carries `test: false`, or `test: true` when the page was loaded with `?telemetry_test=1` (also passed to the TV). Sentry uses `environment: "verification"` for test loads, `local` on localhost, otherwise `production`. Dashboards filter `test = false`. Playwright tests stub both hosts and never reach the real services.

## 3. Setup

- **PostHog:** project "Window Seat" in the existing Eksxel organization, US cloud (`https://us.i.posthog.com`). The free plan allows only one project, so this project is also where future apps go; every event carries `app: "window-seat"` to keep them apart.
- **Sentry:** project `window-seat`, platform `javascript-browser`, in the existing `eksxel` organization, team `eksxel`.
- **Keys:** the PostHog project API key and the Sentry DSN are public client keys and live in `js/telemetry-config.js`. Personal API tokens and auth tokens are never committed.
- **Code:** a small wrapper `js/telemetry.js`, imported by `js/app.js` and `js/receiver.js`. It owns the gate, the queue, the rate limits and the super properties, and never throws into app code. Pinned, vendored SDK builds live in `js/vendor/` and are loaded with a script tag after first paint, only when telemetry is on. The wrapper and config are added to the service worker shell; the vendored SDKs are not precached.
- **Vendored versions (build 2026-11-17.13):**
  - `js/vendor/posthog-1.450.1.no-external.min.js`: posthog-js 1.450.1, `dist/array.no-external.js` (430 KB). The browser array build without external script loading. The "full" variant (817 KB) only adds replay, surveys and other features that stay off, so the smaller build is used. Two edits with no behavior change: the source map comment is removed (the map is not vendored), and one em dash in a log message is a hyphen.
  - `js/vendor/sentry-11.14.0.min.js`: @sentry/browser 11.14.0 (93 KB). Sentry no longer publishes its CDN bundles to npm, so this is a minified IIFE built locally with bun 1.4.4 from the package's ESM build. It exposes `window.Sentry` with only `init`, `captureException`, `withScope`, `flush`, `getClient`, `SDK_VERSION` and the integrations the wrapper uses (global handlers, dedupe, linked errors, HTTP context, event filters, breadcrumbs). No tracing, replay, feedback or session code is included.
  - To update: install the packages with npm in a temporary folder outside the repo, copy or rebuild the files, bump the file names and `js/telemetry-config.js`, and note the versions here.
- **Sentry SDK config:** `release: "window-seat@<BUILD>"`, `tracesSampleRate: 0`, no Replay, no session tracking, no logs or metrics. Default integrations trimmed to the global error handlers, dedupe and basic context (no `browserApiErrors` wrapping, which would wrap CAF's timers and listeners). `beforeBreadcrumb` drops fetch, xhr and navigation breadcrumbs and strips `title` text from click breadcrumbs. `beforeSend` strips query strings and fragments from every URL, scrubs free text (exception values, linked errors, messages and breadcrumb messages: query strings and fragments of URLs inside the text, email addresses and long token-like runs are masked, and the text is capped at 300 characters), drops `extra` (for example the serialized value of a non-Error rejection), drops errors from browser extensions and cross-origin "Script error." with no stack, and enforces the caps below.
- **Early errors:** before the Sentry SDK loads, the wrapper keeps up to 10 `error` and `unhandledrejection` events in memory and hands them to Sentry once it loads (only if telemetry is on).
- **The receiver:** posthog-js with `persistence: "memory"`, `bootstrap: { distinctID: <phone install_id> }` and `request_batching: false`, so nothing is stored on the TV and events are not lost when the receiver stops. The receiver calls the wrapper's flush before `context.stop()`. The flush also covers events still waiting for PostHog to load (they switch to sendBeacon and the flush waits up to 2 s for the SDK to send them), then gives Sentry 600 ms. A repeated stop command during the flush is ignored, so there is one stop and one `receiver_stop`.

## 4. Super properties (on every event)

Computed by the wrapper at capture time (not `posthog.register`, which persists across loads).

| Property | Values | Notes |
| --- | --- | --- |
| `app` | `window-seat` | Lets other apps share the PostHog project. |
| `build` | e.g. `2026-11-17.13` | `BUILD` from `js/build.js` or `js/receiver.js`. |
| `surface` | `web`, `pwa`, `receiver` | `web` is a browser tab, `pwa` is the installed app (display-mode standalone), `receiver` is the TV page. The Android app adds `android`. The spec's single `web` is split into `web` and `pwa`. |
| `device_class` | `phone`, `tablet`, `desktop`, `tv` | Coarse, from screen size and the existing TV detection. Receiver is always `tv`. |
| `cast_state` | `none`, `connecting`, `connected` | Phone side, at capture time. Receiver is always `connected`. |
| `cast_session_id` | CAF session ID or absent | Phone: `session.getSessionId()`. Receiver: `context.getApplicationData().sessionId` (check on a real device). Both sides use the same ID, also after a resume or a second phone joining. |
| `environment` | `production`, `local`, `verification` | Same as Sentry. |
| `locale_region` | e.g. `US`, `JP` | Region part of `navigator.language`. |
| `test` | `false` or `true` | Always sent. |
| `$geoip_disable` | `true` | Turns off PostHog GeoIP. |

The same values go to Sentry as tags (`surface`, `build`, `cast_state`, `device_class`) plus the release.

## 5. The TV follows the phone

The receiver has no UI, so it uses the phone's setting through a new message on the existing channel:

`{ type: "telemetry", on: true|false, installId, test, environment }`

- The phone sends it on every new or resumed session once the namespace is available, and again on every switch change. It is a small, backward compatible addition to the protocol.
- A value counts as sent only once CAF confirms the send. A failed send is retried (after 1, 3 and 8 s, while it is still the current value) and sent again on the next resume, namespace change or switch change, so a failed off is never suppressed. It is not a user command: a failure goes to the debug log only, never to the Cast status, the toast or `cast_send_failed`.
- The receiver stays off until it gets one. If on, it loads the SDKs, sets the phone's `installId` as its distinct ID, and flushes its buffer. The buffer holds at most 50 events and is dropped after 2 minutes without a message.
- Several phones: the receiver sends only while every phone that has told it is on. Any phone saying off turns it off for the rest of that receiver session. When the last phone disconnects, the setting stays as it was (the TV keeps playing on its own).
- `test` and `environment` come from the phone, so verification events from the TV are marked too. Once any phone says `test: true`, the receiver session is a test session and its environment is `verification`, whatever an earlier phone said, so test errors never land in `production`.
- A receiver opened directly in a browser (`receiver.html?debug=1`) sends nothing unless `?telemetry=1` is added.
- **Known blind spot:** if the custom channel never opens, the TV never hears the phone and its events are dropped. That failure is measured from the phone instead (`cast_channel_missing`, `cast_no_reply`). Missing receiver events must not be read as healthy launches.

## 6. Events

All names are snake_case. "Where" names the file and function. Each event fires once per real occurrence, not per retry or re-sync.

### Cast reliability (phone)

| Event | Properties | Where |
| --- | --- | --- |
| `cast_connect_attempt` | `attempt_id` | `js/cast.js` `onCastTap`, when `requestSession` starts. |
| `cast_connect_success` | `attempt_id` (if tapped), `trigger` (`tap`, `auto_join`, `adopted`), `ms_to_connect` (tap only) | `js/cast.js` `onSessionStarted`, once per session (guarded by a flag on the session object, like `__wsPushed`). Covers sessions started from Chrome's own Cast menu too (`adopted`). A tap stays open for 10 s after `requestSession` resolves, since CAF can run the session callback after the promise, so that success still counts as `tap` with its `attempt_id`. |
| `cast_connect_fail` | `attempt_id`, `error_code` (raw CAF code: `cancel`, `timeout`, `session_error`, `receiver_unavailable`, `channel_error`, `extension_missing`, `api_not_initialized`, or `exception`) | `js/cast.js` `onCastTap` promise rejection only (not `recordAttempt` and SESSION_START_FAILED as well). Exception text is never sent. |
| `cast_resume` | `trigger` (`auto_join`, `adopted`) | `js/cast.js` `onSessionStarted` with resumed true, once per session. Success of a resume is read from `cast_first_state` following it. |
| `cast_first_state` | `ms_since_connect`, `resumed` | `js/cast.js` `onMessage`, first state message from the TV in a session. Phone clock only. |
| `cast_channel_missing` | `ms_since_connect` | `js/cast.js`, once per session, when the namespace is still not listed 8 s after the session started (initial setup waits for the namespace, so this cannot rely on a command), or 4 s after a command with no reply. |
| `cast_send_failed` | `code`, `type` | `js/cast.js` `send` failure path (not for each retry, only the final failure). |
| `cast_no_reply` | `type` | `js/cast.js` `send`, when a command never gets a state reply. |
| `cast_session_end` | `stop_requested`, `by_timeout`, `seconds_in_session`, `was_paused`, `pause_warning_up` | `js/cast.js` `leaveRemote`, read before the flags reset, and only if a session actually started. Unasked ends show as `stop_requested = false` and `by_timeout = false`. |

### Cast reliability (receiver)

| Event | Properties | Where |
| --- | --- | --- |
| `receiver_ready` | `ms_since_load` | `js/receiver.js`, CAF `READY` event. |
| `receiver_start_failed` | `kind` (`framework_load`, `start_exception`, `no_ready`) | `js/receiver.js` `startCast`: script load failure, `context.start` throwing, and the existing 10 s no-READY timer. Also sent to Sentry. |
| `receiver_first_frame` | `ms_since_load`, `cam_id` | `js/receiver.js`, first cam PLAYING after load that is not the random start cam (the first cam chosen after a phone connected, or the first rotation). TV clock only. |
| `receiver_sender_left` | `reason` (CAF `SENDER_DISCONNECTED` reason), `senders_left`, `seconds_in_session`, `paused` | `js/receiver.js` SENDER_DISCONNECTED handler. This is where real drops are measured: an `ERROR` or unknown reason with no stop asked is a drop. |
| `receiver_stop` | `reason` (`pause_timeout`, `sender_stop`, `idle`), `seconds_in_session` | `js/receiver.js` right before `context.stop()`, after a flush. |
| `receiver_error` | `kind` (`message_parse`, `list_load`), `code` | `js/receiver.js` where each is handled. `message_parse` also goes to Sentry. Player failures use `player_error` instead. |

### Usage

| Event | Properties | Where |
| --- | --- | --- |
| `app_open` | `installed`, `tv_mode`, `cast_available` (Cast SDK present) | `js/app.js` on load, once per page load. The list source is reported separately by `list_load`. |
| `local_playback_started` | `mix_id`, `cam_count` | `js/app.js` `begin` (not `start`, which can stop at "Connecting to YouTube"). |
| `local_playback_ended` | `seconds`, `reason` (`home`, `cast`, `page_hide`) | `js/app.js` `goHome`, `pauseLocalForCast` (only if local playback had begun), and `pagehide` (sent with sendBeacon). |
| `cam_shown` | `cam_id`, `reason` (`start`, `rotate`, `next`, `prev`, `pick`, `skip`, `retry`, `list`), `mode` (`local`, `cast`), `sampled` | Where a cam actually starts playing: `js/app.js` `onCamPlaying`, `js/receiver.js` `commitPending`. Only the device that plays it sends it. User navigation (`next`, `prev`, `pick`) always sends; automatic reasons (`start`, `rotate`, `skip`, `retry`, `list`) are sampled 1 in 4 with `sampled: 4` so charts can weight them. The phone tracks the reason the same way the receiver does. |
| `mix_started` | `mix_id`, `shuffle`, `user_choice`, `mode` | On the first music PLAYING after a mix is loaded (not when it is only cued): `js/app.js` `onMusicState`, `js/receiver.js` `onMusicState`. `user_choice` passes through from the command (not the connect-time sync). |
| `command` | `type` (`pause`, `play`, `next`, `prev`, `pin`, `unpin`, `mix`, `next_mix`, `cam`, `cams`, `volume`, `mute`, `unmute`, `shuffle`, `interval`, `keep_paused`, `stop`), `mode` (`local`, `cast`) | Once per user action, at the user-intent boundary in `js/app.js` (where each handler branches on `viaTv`) and the remote's buttons in `js/cast.js` `runCmd`. Never from `send`, sync messages or retries. Wire names are mapped (`pause{value:false}` is `play`, `nextMix` is `next_mix`). `volume` once per slider gesture. The spec's `remote_command` and `keep_paused_tapped` are folded into this. |
| `panel_opened` | `panel` (`cams`, `music`, `about`, `tv`), `mode` | `js/app.js` `openPanel` only (the remote opens the same panels). |
| `cam_pool_changed` | `enabled_count`, `effective_count`, `total`, `via` (`toggle`, `shortcut`, `time_rule`), `shortcut`, `skip_night` | After a change settles (2 s debounce, flushed on page hide), from one shared path after `poolChanged`. |
| `pause_warning_shown` | `seconds_paused` | `js/receiver.js` `pauseWarn`. |
| `pause_timeout_fired` | `seconds_paused` | `js/receiver.js` `pauseEnd`, at the moment the stop is committed (after the cancel window), then flushed. |
| `debug_mode_toggled` | `on` | `js/debug.js`, the switch's `onChange`. |
| `telemetry_enabled` |  | When the user turns the switch back on. |

### Errors

| Event | Properties | Where |
| --- | --- | --- |
| `player_error` | `player` (`cam`, `music`), `cam_id` or `mix_id`, `code` (YouTube error code), `mode` | `js/app.js` `failPending` / `failActive` (passing the YouTube code through) and `onMusicError`; `js/receiver.js` `onCamError` and `onMusicError`. PostHog only, since dead streams are expected. |
| `cam_failed` | `cam_id`, `why` | `js/app.js` `failPending` / `failActive`, first transition only; `js/receiver.js` `failCam`. Tells us which cams to drop from the sheet. |
| `cams_all_failed` | `mode` | When every cam in the pool has failed (both pages). |
| `music_unavailable` |  | `js/app.js`, when the music player can't start (the existing unavailable path). |
| `list_load` | `ok`, `source` (`sheet`, `saved`, `phone`, `built-in`, read from `listInfo.source` after the fetch settles), `ms`, `cams`, `mixes`, `error` (fixed codes: `timeout`, `http`, `parse`, `unusable`, `network`) | `js/app.js` `loadSheetConfig`; on the receiver after its own load or the phone's list. `source = built-in` is the alarm case. |
| Sentry error events | stack, release, tags | Global `error` and `unhandledrejection` on both pages, plus `captureException` in a short allowlist of catch blocks that mean a real bug (message parse, CAF start, list parse). Expected failures (storage denied, fullscreen denied, missing APIs) are not captured. |

## 7. Volume and cost guard

- **Budget:** about 30 events per hour of ambient playback per device after sampling (rotations every 30 to 60 s at 1 in 4, plus rare commands). One TV casting around the clock is about 22k events a month, so the 1M PostHog free tier covers about 45 such TVs.
- **Wrapper limits:** max 60 PostHog events per minute per page; max 10 Sentry events per minute and 25 per page load; the same Sentry error fingerprint once per page load.
- **Server side:** Sentry DSN key rate limit (300 events per hour) and spike protection on. PostHog's free plan has a hard cap and no card on file, so it cannot bill; it stops ingesting at the limit. Check monthly usage in both after the first week.

## 8. PostHog dashboard: "Window Seat health"

All tiles filter `app = window-seat`, `test = false`, `environment = production`, last 30 days unless noted. A "session" means a PostHog `$session_id` for phone surfaces and a `cast_session_id` for Cast.

1. **Cast funnel:** `cast_connect_attempt` then `cast_connect_success` then `cast_first_state`, linked by install, within 2 minutes, attempts with `error_code = cancel` excluded.
2. **Cast connect success rate:** weekly `cast_connect_success` (trigger tap) / non-cancel attempts, plus `cast_connect_fail` by `error_code`.
3. **Ends and drops:** `cast_session_end` by `stop_requested` and `by_timeout`, and `receiver_sender_left` by `reason`, with total casting hours (sum of `seconds_in_session` / 3600) alongside as the denominator.
4. **Channel health:** daily `cast_channel_missing`, `cast_no_reply`, `cast_send_failed`.
5. **Time to TV:** median and p90 of `cast_first_state.ms_since_connect` (phone) and `receiver_first_frame.ms_since_load` (TV).
6. **Daily sessions:** unique `$session_id` per day by `surface`, and unique `cast_session_id` per day.
7. **Local vs Cast:** daily `local_playback_started` vs `cast_connect_success`.
8. **Top cams:** `cam_shown` by `cam_id`, top 20 (weighted by `sampled`). Next to it `cam_failed` by `cam_id`, top 10.
9. **Top mixes:** `mix_started` by `mix_id`.
10. **Commands:** `command` by `type` and `mode`.
11. **Pause timeout:** `pause_warning_shown`, `command` with `type = keep_paused`, `pause_timeout_fired`.
12. **Faults:** daily `list_load` with `ok = false` or `source = built-in`, `receiver_start_failed`, `receiver_error`, `cams_all_failed`, kept separate from expected dead streams (`player_error`).

**PostHog alerts** (the free plan allows 5): `list_load` with `source = built-in` above 0 in a day, and `cast_connect_fail` (non-cancel) above 5 in a day. Both email Stephane.

## 9. Sentry alerts

Both email Stephane (the default owner). No Slack.

1. **New issue:** a new issue in `window-seat`, environment `production`: email right away.
2. **Spike:** more than 20 error events in `window-seat` in one hour, at most one email an hour. If metric alerts are not available on the free plan, the fallback is the issue alert "an issue is seen more than 20 times in one hour", which only catches a single noisy issue, not 20 errors spread over several issues.

## 10. Rollout and checks

1. Ship on the phone page and the receiver in one build. Playwright covers: the About switch, DNT/GPC, "off means no requests to either host", queued events dropped on turning off, and the receiver gate (no sends until an on message; off stops sends).
2. Verify on the live site with `?telemetry_test=1`: events show up in PostHog, a test error shows up in Sentry, then resolve the test issue. Verification events still count toward quotas.
3. On the TV, Stephane casts once and we confirm `receiver_ready`, `receiver_first_frame` and `cam_shown` arrive with `surface = receiver` and the same `cast_session_id` as the phone's `cast_connect_success`, then Stop and confirm `receiver_sender_left`.
4. After a week, review the dashboard and quotas, and trim.

## 11. Open questions

1. **Consent default:** on by default with an off switch (current plan), or off until turned on? Middle option: on by default but with no stored ID (`persistence: "memory"`), which loses install counts. Spec open question 6.
2. Keep `locale_region` from the browser language, or drop location entirely?
3. Keep the PostHog project named "Window Seat", or rename it to something shared (for example "Eksxel apps"), since the free plan allows only one project?

## Review notes

- **Claude Code** flagged: session end and drop belong in `leaveRemote` (the stop flags reset there) and real drops can only be seen on the receiver; `remote_command` in `send()` would count sync messages and retries; the command and CAF error vocabularies did not match the code; double-counted connect failures and cancels in the success rate; `onSessionStarted` runs several times per session; use the CAF session ID; receiver events lost on stop (no batching, flush first); receiver needs memory persistence and the phone's ID; PostHog adds `$timezone`, referrer and URLs by itself and can load extra code unless told not to; Sentry breadcrumbs leak URLs; cost of per-rotation events; missing phone-side channel events; corrected placements for `player_error`, `mix_started`, `local_playback_*`, `panel_opened`, `debug_mode_toggled`.
- **Codex** flagged: Sentry `close()` flushes pending events, so gate the transport first; an explicit consent message to the TV with multi-sender rules and test propagation; the receiver blind spot when the channel never opens; `attempt_id` and one outcome per attempt; clock definitions for time to first frame; `receiver_ready` should come from CAF `READY`; Sentry server-side IP setting should be required; early errors before the SDK loads; allowlist Sentry capture in catch blocks; dashboard denominators; the fallback spike alert's narrower scope; PostHog alerts for the built-in fallback and connect failures.
