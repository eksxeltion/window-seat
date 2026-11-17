# Window Seat telemetry plan v1

Status: draft for review. Written Nov 17, 2026 against build 2026-11-16.12. Tools: PostHog (usage) and Sentry (errors), both on free tiers. This plan follows the Telemetry section of the Android spec and extends it to today's web app and TV receiver.

## 1. What we want to learn

In priority order.

**A. Cast reliability (the key metric).** Is casting good enough to share?

1. How many Cast connect attempts are made, and what share succeed?
2. When a connect fails, why (cancelled, timeout, launch error, no device) and with which CAF error code?
3. How often do sessions drop without the user asking, and after how long? Was the session paused or showing the pause warning at the time?
4. How long from tapping Cast until the TV shows its first cam frame (time to first frame)?
5. How often does the phone resume an existing session, and does it work?

**B. Usage.** What do people actually do?

1. Sessions per day, and session length.
2. Local playback on the phone vs Cast to the TV, and browser tab vs installed app (PWA).
3. Which cams and mixes are played most, and which cams fail most.
4. How often pause, pin, next, previous and shuffle are used.
5. How often the cam menu and the mix picker are opened and used.
6. How often the pause timeout warns, how often "Keep paused" is tapped, and how often the session ends from the timeout.
7. How often debug mode is turned on.

**C. Errors.** What breaks?

1. Uncaught JS errors and unhandled promise rejections on the phone page and on the TV receiver (Sentry).
2. YouTube player errors, by video ID and error code (cams and mixes, phone and TV).
3. Sheet load failures and how often we fall back to the cached list or the built-in `js/data.js`.

## 2. Principles and privacy rules

- **No personal data.** No names, emails, accounts, free text, or device names (Cast device friendly names are never sent).
- **Anonymous ID only.** PostHog's anonymous distinct ID is our `install_id`: random, created on first load, kept in localStorage, never linked to a person. We never call `identify`.
- **No IP storage and no fine location.** PostHog: "Discard client IP data" on, GeoIP enrichment off (`$geoip_disable: true` on every event). Sentry: `sendDefaultPii: false` and "Prevent storing of IP addresses" on in project settings where available. The only location signal is the coarse country from the browser's language region (for example `US` from `en-US`) sent as `locale_region`; no IP-based geolocation.
- **No session replay, no autocapture, no heatmaps, no surveys** for now. Only the explicit events below, plus Sentry error events.
- **Respect Do Not Track and Global Privacy Control.** If `navigator.doNotTrack === "1"` or `navigator.globalPrivacyControl === true`, telemetry is off and the About switch shows it off with the note "Off because your browser asks sites not to track you". The scripts are never loaded.
- **On by default, with an off switch in About.** This is open question 6 in the Android spec, and it is reversible. Flagged for Stephane: if he prefers opt-in, flipping the default is one line.
- **Off means off.** When the switch is off (or DNT is on), the PostHog and Sentry scripts are not even downloaded, no request goes to either host, and queued events are dropped. Turning it off while running calls `posthog.opt_out_capturing()` and closes the Sentry client.
- **The TV follows the phone.** The receiver has no UI of its own. It buffers events in memory (max 50) until the first sender message carries the phone's telemetry setting. If on, it loads the SDKs and flushes; if off or never told, it drops them. A receiver opened directly in a browser (`receiver.html?debug=1`) sends nothing unless `?telemetry=1` is added.
- **Never block the app.** Both SDKs load after first paint, asynchronously, and any failure to load is swallowed. Telemetry code must never throw into app code.
- **Cost guard.** Free tiers only. PostHog free tier is 1M events and Sentry Developer is 5k errors a month. Our budget: under 200 events per hour of playback per device. Rate limit in our wrapper: max 60 PostHog events per minute and max 10 Sentry events per minute per page, duplicates of the same error message within a session sent once.
- **Test data is marked.** Any load with `?telemetry_test=1` adds `test: true` to every PostHog event and sets Sentry `environment: "verification"`. Dashboards filter out `test = true`.

## 3. Setup

- **PostHog:** project "Window Seat" in the existing Eksxel organization, US cloud (`https://us.i.posthog.com`). The free plan allows one project, so this is the shared project for future apps too; every event carries `app: "window-seat"` so other apps can live next to it.
- **Sentry:** project `window-seat`, platform `javascript-browser`, in the existing `eksxel` organization, team `eksxel`.
- **Keys:** the PostHog project API key and the Sentry DSN are public client keys and live in `js/telemetry-config.js`. Personal API tokens and auth tokens are never committed.
- **Loading:** pinned, vendored builds under `js/vendor/` (posthog-js and the Sentry browser bundle), loaded by a small wrapper `js/telemetry.js` that both `js/app.js` and `js/receiver.js` import. Vendoring keeps the deploy self-contained, verifiable by sha1, and works the same in the CAF receiver.
- **Config:** PostHog `autocapture: false`, `capture_pageview: false`, `capture_pageleave: false`, `disable_session_recording: true`, `capture_exceptions: false` (Sentry owns errors), `persistence: "localStorage"`, `person_profiles: "never"`. Sentry `release: "window-seat@<BUILD>"`, `environment: "production"` (or `local` on localhost, `verification` in test loads), `tracesSampleRate: 0`, no Replay integration, `sendDefaultPii: false`.

## 4. Super properties (on every event)

| Property | Values | Notes |
| --- | --- | --- |
| `app` | `window-seat` | Lets other apps share the PostHog project. |
| `build` | e.g. `2026-11-17.13` | `BUILD` from `js/build.js` or `js/receiver.js`. |
| `surface` | `web`, `pwa`, `receiver` | `web` is a browser tab on any device, `pwa` is the installed app (display-mode standalone), `receiver` is the TV page. The Android app will add `android`. The spec's single `web` is split into `web` and `pwa` here. |
| `device_class` | `phone`, `tablet`, `desktop`, `tv` | Coarse, from screen size and the existing TV detection. Receiver is always `tv`. |
| `cast_state` | `none`, `connecting`, `connected` | Phone side, live. Receiver is always `connected`. |
| `cast_session_id` | random ID or absent | Created by the phone on a new session, sent to the receiver, so phone and TV events of one session line up. |
| `locale_region` | e.g. `US`, `JP` | From `navigator.language`. No IP lookup. |
| `test` | `true` or absent | Only with `?telemetry_test=1`. |
| `$geoip_disable` | `true` | Turns off PostHog GeoIP. |

The receiver uses the phone's `install_id` as its distinct ID (sent with the telemetry flag), so a phone and its TV count as one install.

The same values go to Sentry as tags (`surface`, `build`, `cast_state`, `device_class`) and the release.

## 5. Events

All names are snake_case. "Where" names the file and function where it fires.

### Cast reliability

| Event | Properties | Where |
| --- | --- | --- |
| `cast_connect_attempt` | `resume` (false) | `js/cast.js` `onCastTap`, when a session request starts. |
| `cast_connect_success` | `resume`, `ms_to_connect` | `js/cast.js` `onSessionStarted` (resume false). `ms_to_connect` from the attempt. |
| `cast_connect_fail` | `reason` (`cancelled`, `timeout`, `launch_error`, `no_device`, `other`), `error_code` (CAF code) | `js/cast.js` `recordAttempt` with a failing code, and SESSION_START_FAILED. |
| `cast_resume` | `ok`, `ms_to_resume` | `js/cast.js` `onSessionStarted` with resumed true. |
| `cast_first_state` | `ms_to_first_state` | `js/cast.js` `onMessage`, first state message from the TV after connect. |
| `cast_session_end` | `reason` (`user_stop`, `pause_timeout`, `receiver_stop`), `seconds_in_session` | `js/cast.js` `onSessionEnded` when the end was asked for. |
| `cast_session_drop` | `reason` (`network`, `receiver_gone`, `unknown`), `seconds_in_session`, `was_paused`, `pause_warning_up` | `js/cast.js` `onSessionEnded` when nobody asked for it. |
| `receiver_ready` | `caf_ok`, `namespace_listed`, `ms_since_load` | `js/receiver.js` `startCast` after `context.start`. |
| `receiver_first_frame` | `ms_since_load`, `ms_since_connect`, `cam_id` | `js/receiver.js` first cam PLAYING after load. Time to first frame on the TV. |
| `receiver_error` | `kind` (`caf_start`, `sheet_load`, `player_error`, `message_parse`), `cam_id` or `mix_id`, `code` | `js/receiver.js` where each failure is handled. Also sent to Sentry when `kind` is `caf_start` or `message_parse`. |

### Usage

| Event | Properties | Where |
| --- | --- | --- |
| `app_open` | `installed` (bool), `tv_mode` (bool), `list_source` | `js/app.js` on load, once per page load (phone). The receiver sends `receiver_ready` instead. |
| `local_playback_started` | `mix_id`, `cam_count` | `js/app.js` `begin` or `start`, playing on this device. |
| `local_playback_ended` | `seconds`, `reason` (`home`, `cast`, `hidden`) | `js/app.js` `goHome`, `pauseLocalForCast`, and page hide. |
| `cam_shown` | `cam_id`, `reason` (`rotate`, `next`, `prev`, `pick`, `start`), `mode` (`local`, `cast`) | Wherever a cam actually starts playing: `js/app.js` `onCamPlaying` (local) and `js/receiver.js` `commitPending` (TV). Only the device that plays it sends it. |
| `mix_started` | `mix_id`, `shuffle`, `user_choice`, `mode` | `js/app.js` `playMix` (local) and `js/receiver.js` `playMix` (TV). |
| `remote_command` | `type` (`pause`, `play`, `next`, `prev`, `pin`, `unpin`, `mix`, `next_mix`, `cam`, `cams`, `volume`, `shuffle`, `keep_paused`, `stop`), `from` (`web`) | `js/cast.js` `send`, for user commands only (not silent sync messages). `volume` at most once per slider gesture. |
| `local_command` | `type` (same list, minus `keep_paused` and `stop`) | `js/app.js` handlers for the same actions while playing locally. |
| `panel_opened` | `panel` (`cams`, `music`, `about`, `tv`) | `js/app.js` `openPanel`, and the remote's cam menu and mix picker in `js/cast.js`. |
| `cam_pool_changed` | `enabled_count`, `total`, `via` (`toggle`, `shortcut`, `time_rule`), `shortcut` | `js/app.js` `setEnabled`, `setManyEnabled`, `pickGroup`, `toggleTimeRule`. Debounced 2 s. |
| `pause_warning_shown` | `seconds_paused` | `js/receiver.js` `pauseWarn`. |
| `keep_paused_tapped` |  | `js/cast.js` when the user taps Keep paused. |
| `pause_timeout_fired` | `seconds_paused` | `js/receiver.js` `pauseEnd`. |
| `debug_mode_toggled` | `on` | `js/app.js` `setupDebug` change handler. |
| `telemetry_enabled` |  | Once, when the user turns the switch back on. No event is sent when turning it off. |

### Errors

| Event | Properties | Where |
| --- | --- | --- |
| `player_error` | `player` (`cam`, `music`), `video_id`, `code` (YouTube error code), `mode` | `js/app.js` `onCamError` and `onMusicError`; `js/receiver.js` `onCamError` and `onMusicError`. PostHog only, these are expected (dead streams). |
| `cam_failed` | `cam_id`, `why` | `js/app.js` `markFailed`, `js/receiver.js` `failCam`. Tells us which cams to drop from the sheet. |
| `list_load` | `ok`, `source` (`sheet`, `cache`, `built-in`), `ms`, `cams`, `mixes`, `error` (short code, never a URL) | `js/config.js` `refreshFromSheet` result, reported from `js/app.js` `loadSheetConfig` and `js/receiver.js`. A fallback to `built-in` is the alarm case. |
| Sentry error events | stack, release, tags | Global `error` and `unhandledrejection` on both pages through the Sentry SDK, plus explicit `captureException` in existing catch blocks that today only log. |

Sentry `beforeSend` strips query strings from URLs and drops events from browser extensions and from cross-origin "Script error." with no stack.

## 6. PostHog dashboard: "Window Seat health"

All tiles filter `app = window-seat` and `test != true`, last 30 days unless noted.

1. **Cast funnel:** `cast_connect_attempt` then `cast_connect_success` then `cast_first_state` then `receiver_first_frame`, within 2 minutes, by install.
2. **Cast connect success rate:** daily `cast_connect_success` / `cast_connect_attempt`, with `cast_connect_fail` broken down by `reason`.
3. **Drops per hour casting:** daily `cast_session_drop` count, next to `cast_session_end` by `reason`.
4. **Time to first frame:** median and p90 of `receiver_first_frame.ms_since_connect`.
5. **Daily sessions:** unique sessions per day by `surface`.
6. **Local vs Cast:** daily `local_playback_started` vs `cast_connect_success`.
7. **Top cams:** `cam_shown` by `cam_id`, top 20. Next to it `cam_failed` by `cam_id`, top 10.
8. **Top mixes:** `mix_started` by `mix_id`.
9. **Commands:** `remote_command` and `local_command` by `type`.
10. **Pause timeout:** `pause_warning_shown`, `keep_paused_tapped`, `pause_timeout_fired`.
11. **Error rate:** daily (`player_error` + `receiver_error` + `list_load` with `ok = false`) per session, by `surface`.

## 7. Sentry alerts

Both send email to Stephane (the default owner). No Slack.

1. **New issue:** when a new issue is created in `window-seat` (environment `production`), email right away.
2. **Spike:** when the number of error events in `window-seat` is above 20 in one hour, email, at most once an hour. If the free plan does not offer metric alerts, use the issue alert condition "an issue is seen more than 20 times in one hour" instead.

## 8. Rollout and checks

1. Ship on the phone page and the receiver in one build. The About switch, the DNT rule and the "off means no requests" rule are covered by Playwright tests.
2. Verify on the live site with `?telemetry_test=1`: events show up in PostHog, a test error shows up in Sentry, then resolve the test issue.
3. On the TV, Stephane checks: cast once, then confirm `receiver_ready` and `receiver_first_frame` arrive with `surface = receiver` and the same `cast_session_id` as the phone's `cast_connect_success`.
4. After a week, review the dashboard and decide what to trim.

## 9. Open questions

1. On by default with an off switch (current plan), or off until turned on? Spec open question 6.
2. Is `locale_region` from the browser language enough as a country signal, or should we drop location entirely?
3. Keep the PostHog project named "Window Seat", or rename it to something shared (for example "Eksxel apps") since the free plan allows only one project?
