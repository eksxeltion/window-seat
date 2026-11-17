# Window Seat design system

This is the design source of truth for the Window Seat visual redesign. It is a proposal, not shipped UI. The matching mocks live in the Paper file [Window Seat redesign v1](https://app.paper.design/file/01M7KCRQZVMH15C8SWE16RHQFE). v1 artboards are kept in place. v2 artboards sit next to them and reflect the decisions below. Comment on the artboards there; we will iterate from those notes before any app code changes.

The live app today is a dark, gold-accent media player with a crowded control bar. The redesign keeps the product job (phone as remote, TV as the window) and makes the phone feel like a polished consumer app: bright, calm, and easy to use one-handed. Local playback is the exception: once a cam is playing on the phone, it goes full screen, with controls overlaid on the picture.

## Mood

**Maritime, late afternoon at a harbor window.** Soft sea fog on the walls, deep navy for ink and actions, one horizon-blue accent. The scene is a bright living room with a window open to water and city light, not a cinema or a travel brochure.

This is a deliberate step away from the current black-and-gold look, and also away from the common warm-linen-plus-terracotta palette. Airbnb is a reference for hospitality, spacing, and photography as the hero, not for coral buttons on cream.

On the TV, and on the phone's full-screen local player, the same palette goes nocturnal: the video is the picture, and type sits in a dark water-glass overlay that fades after a change.

## Personality

- Quiet hospitality, not a media-player chrome kit.
- One strong color moment (horizon blue or navy) rather than a rainbow of accents.
- Photography (the live cam) does the visual work. UI stays out of the way.
- Large, obvious controls, usable from the couch and with a thumb on a Pixel.

## Color

### Phone and start (bright)

Used on Home, the cam menu, the mix picker, and the casting remote.

| Token | Hex | Role | Object in the scene |
| --- | --- | --- | --- |
| `--color-canvas` | `#F3F5F7` | Page background | Sea fog on plaster |
| `--color-surface` | `#FFFFFF` | Sheets, menus, cards | Sail canvas |
| `--color-surface-raised` | `#EEF3F6` | Recessed wells, chip tracks | Shallow tide pool |
| `--color-ink` | `#102536` | Primary text, icons | Deep harbor water at dusk |
| `--color-ink-secondary` | `#4A5C68` | Meta, labels | Wet slate |
| `--color-ink-tertiary` | `#7B8B96` | Captions, timestamps | Distant haze |
| `--color-line` | `#D5DEE4` | Hairline borders | Horizon mist |
| `--color-primary` | `#102536` | Primary buttons, key actions | Same navy as ink |
| `--color-primary-ink` | `#FFFFFF` | Text on primary | Sail canvas |
| `--color-accent` | `#2F6F8A` | Selected chips, links, playing state | Horizon blue |
| `--color-accent-soft` | `#E4F0F5` | Selected chip fill, pin highlight | Shallow water |
| `--color-focus` | `#2F6F8A` | Focus ring | Horizon blue |
| `--color-success` | `#2C6A4F` | In-rotation, connected | Harbor pine |
| `--color-danger` | `#8F3A3A` | Destructive or excluded | Channel marker |
| `--color-sleeping` | `#6B7C89` | Local times and Sleeping marks for night cams | Late harbor haze |

### Night field (local full-screen player and TV)

Used on top of video only: the phone's full-screen local player, the TV lower third, and TV toasts. Never as the phone's default chrome on Home or sheets.

| Token | Value | Role |
| --- | --- | --- |
| `--color-night-field` | `#0A1A26` | Full-screen player field when no still is shown |
| `--color-night-glass` | `rgba(10, 26, 38, 0.72)` | Glass panels and scrims on the local player |
| `--color-accent-night` | `#8CC3D6` | Pinned pill, night accent, readable on video |
| `--color-on-glass` | `#F3F5F7` | Titles and primary labels on glass |
| `--color-on-glass-secondary` | `#A9B8C3` | Meta on glass |
| `--color-overlay` | `rgba(10, 18, 24, 0.62)` | Glass behind the TV lower third |
| `--color-overlay-strong` | `rgba(10, 18, 24, 0.82)` | TV toasts, command confirmation |
| `--color-overlay-ink` | `#F3F5F7` | Title and brand |
| `--color-overlay-dim` | `rgba(243, 245, 247, 0.78)` | City, country, mix line |
| `--color-overlay-line` | `rgba(255, 255, 255, 0.16)` | Hairline on glass |
| `--color-overlay-accent` | `#8EC4D4` | Playing or pinned marker, readable on video |

The video itself stays untouched. No colored grade on the cam. A soft bottom scrim exists only while chrome is visible on the local player or the TV lower third is on screen.

### Mapping from the current app

| Today | Redesign |
| --- | --- |
| `#000` body, `#ffd27f` gold accent, cream type | Bright fog canvas and navy ink on the phone |
| Glass bar `rgba(18, 18, 24, 0.82)` | White sheets, and night glass only over video |
| Gold as the only accent | Horizon blue for selection and "now playing" only |

## Type

**Display:** Fraunces (soft optical serif). Wordmark, start-screen title, TV cam title. Weight 500 or 600. Tracking slightly tight on large sizes (`-0.02em` at 40px and up).

**UI:** Plus Jakarta Sans. Everything else: buttons, meta, lists, chips, mix titles. Weights 500, 600, 700. Labels and small caps use weight 500 with open tracking (`0.06em`).

**Japanese:** Noto Sans JP, for mix Japanese titles only. Cam names stay in the sheet language (almost always English).

If a family is missing at build time, fall back to `Source Serif 4` for display, `system-ui` for UI, and `Noto Sans JP` or `system-ui` for Japanese, and keep the same sizes.

### Phone scale

| Name | Size / line | Weight | Use |
| --- | --- | --- | --- |
| Display | 40 / 48 | Fraunces 600 | Start wordmark |
| Title | 28 / 34 | Plus Jakarta 650 | Sheet titles |
| Title sm | 22 / 28 | Plus Jakarta 650 | Now-playing cam name |
| Body | 16 / 24 | Plus Jakarta 500 | Lists, descriptions |
| Body sm | 14 / 20 | Plus Jakarta 500 | City, country, duration |
| Caption | 12 / 16 | Plus Jakarta 500, tracking 0.06em | Section labels, type tags |
| Button | 16 / 20 | Plus Jakarta 600 | Buttons |

### TV scale (10-foot, designed at 1920x1080)

Type must read from the couch, about 3 meters away. Sizes are px at 1080p and should scale with the viewport (`vw`, with a px minimum).

| Name | Size / line | Weight | Use |
| --- | --- | --- | --- |
| TV title | 64 / 76 | Fraunces 600 | Cam name in the lower third |
| TV meta | 32 / 40 | Plus Jakarta 500 | City, country, local time |
| TV mix | 24 / 32 | Plus Jakarta 500 | Current mix, English title |
| TV brand | 18 / 24 | Plus Jakarta 600, tracking 0.14em, uppercase | WINDOW SEAT |
| TV toast | 28 / 36 | Plus Jakarta 600 | Command confirmation ("Pinned. Staying on this cam.") |

## Spacing

4px base. Use only these steps.

| Token | Value |
| --- | --- |
| `--space-4` | 4px |
| `--space-8` | 8px |
| `--space-12` | 12px |
| `--space-16` | 16px |
| `--space-20` | 20px |
| `--space-24` | 24px |
| `--space-32` | 32px |
| `--space-40` | 40px |
| `--space-48` | 48px |
| `--space-64` | 64px |

Rhythm: tight within a control (8 or 12), comfortable between related blocks (16 or 24), generous around a hero or a sheet title (32 or 40). Phone screen padding is 24px. TV safe area is 5% on every side (96px at 1920x1080), and the lower third stays inside it.

## Radii

| Token | Value | Use |
| --- | --- | --- |
| `--radius-sm` | 8px | Chips, small icon buttons |
| `--radius-md` | 16px | Buttons, list rows, inputs |
| `--radius-lg` | 24px | Sheets, video card, transport well |
| `--radius-xl` | 32px | Start card, large panels |
| `--radius-pill` | 999px | Primary CTA, cast pill, volume track, pin pill |

## Elevation

Shadows are cool and short, like light from a north window. No heavy 2010s card stacks.

- **Level 0:** flat on canvas, hairline `--color-line` if a boundary is needed.
- **Level 1 (resting card or video frame):** `0 8px 24px rgba(16, 37, 54, 0.08)`.
- **Level 2 (sheet or menu):** `0 16px 40px rgba(16, 37, 54, 0.12)`.
- **Level 3 (toast):** `0 8px 24px rgba(16, 37, 54, 0.18)`.

On TV and on the night field, prefer dim glass over drop shadows. Shadows disappear on a 10-foot dark screen.

## Iconography

Simple 24px line icons, 2px stroke, round caps and joins. Navy on bright surfaces, fog-white on night glass and on primary buttons. Do not use emoji as icons.

Needed set: previous, play, pause, next, volume (music note is acceptable next to the mix slider), pin, pinned, cam, mix, close, search, check, exclude, chevron, fullscreen, fullscreen_exit, Home back.

**Cast icons must be the official Google Cast (Material) glyphs:** a rectangle with signal arcs rising from the lower left. Use `cast` when idle and `cast_connected` when a session is live. Do not invent a substitute glyph.

Hit targets on the phone are at least 48px, and 64px for transport (previous, play/pause, next). The floating show/hide-controls button is 52px and always sits at the bottom right of the local player.

## Motion

Calm and short. The living room should feel still.

| Motion | Duration | Easing | Use |
| --- | --- | --- | --- |
| Fade | 200ms | ease-out | Sheets, toasts, chips |
| Slide | 280ms | cubic-bezier(0.2, 0.8, 0.2, 1) | Bottom sheets, lower third in |
| Cam crossfade | 400ms | ease | Existing cam blackout, keep this |
| Local chrome hold | 6s visible after a tap, then fade, except when paused or a sheet is open |  | Local player only |
| Lower third hold | 10s visible after a cam loads, then 800ms fade out |  | TV only |
| Pin toast hold | 5s visible after pinning, then fade |  | TV only |
| Press | 80ms | ease-out | Button scale to 0.98 |

No bounce. No looping decorative motion. The cam is already moving.

## Layout principles

- Phone artboards are Pixel-sized, 412x915, with a status bar except on the full-screen local player, where system bars are hidden.
- Local playback: the cam plays full screen with controls overlaid on the picture. A floating button shows or hides the chrome. Exit full screen restores system bars and leaves the player.
- Casting: the phone drops the live video and becomes a remote. A clear "Casting to {room}" pill with the official connected Cast icon is always visible. The TV holds the picture.
- TV: full-bleed cam, no chrome except a lower third for the first 10 seconds after a cam loads, a 5-second pin toast, and a persistent Paused pill while paused.

## Component specs

### Wordmark

"Window Seat" in Fraunces 600, sentence case. No icon required; if one is used, a simple four-pane window in navy, 32px, to the left of the words. Do not recreate the current gold window glyph as the default mark.

### Primary button

Height 52px, pill radius, navy fill, white label in Plus Jakarta 600. Full width on the start screen. Hover and press darken one step toward `#0A1B28`.

### Ghost button

Same height, transparent fill, navy label, no border (or a hairline only if it sits on a busy photo). Used for "Open in TV browser" and "About".

### Start screen (Home)

Bright canvas. Wordmark, a one-line tagline, a "Right now" hero, a full-width Start watching button, then Cast to your TV (official Cast icon), then quieter ghost actions for "Open in TV browser" and "About and credits".

Tagline: "Live windows onto the world, with soft music, made for your TV." This is intentionally wider than Japan. About copy can still lead with Japan.

The "Right now" hero shows a current cam still, the local time in that city, and a short line about how many windows are awake. Example, for a Monday afternoon in Pacific time: "It's 9:51 AM in Tokyo." and "Morning on the harbor. 33 windows are awake." Counts come from the sheet plus the sleeping-cam rule, not from invented places.

### Local player

Full-screen night field, no status bar.

- Top scrim: Home (exit back to the start screen), official Cast icon, and fullscreen_exit.
- Bottom glass panel: cam name, `{city}, {country}` and local time, rotation status, pin pill, previous / pause / next, Music volume slider, Cams and Mix.
- A 52px floating button at the bottom right shows or hides the chrome. Auto-hide after 6 seconds, except when paused or a sheet is open.
- Exit full screen restores system bars.

### Pause and pin

These are independent controls with independent states: Playing, Pinned, Paused, and Paused and pinned. Never use the word "pause" on the pin control.

**Pause** is the large center transport button, icon only. It pauses the entire feed: the cam and the music. On resume, the cam jumps to the live edge and the mix continues where it stopped. While paused, the local player keeps chrome visible, and the TV shows a persistent "Paused" pill so a frozen frame is not mistaken for a crash.

**Pin** is a text pill in the info row, labeled "Pin cam" when idle (outline) and "Pinned" when on (filled with accent-night on the local player, accent-soft on the bright remote). Pin stops auto rotation and keeps showing the current cam. Music keeps playing. Next and previous while pinned still change the cam and keep the pin. Pin wins over the sleeping-cam skip: a pinned cam stays on screen even after local midnight.

The rotation hairline ("Next cam in 2:14") is visible only when rotation is on. When pinned, replace it with "Pinned. Rotation off." When paused and not pinned, show "Paused" and freeze the hairline.

### Transport controls

A centered cluster, like a music player:

- Previous cam (64px).
- Pause / play (72px, filled circle, contrasting icon). This pauses or resumes the whole feed, not rotation.
- Next cam (64px).

Rotation interval ("Every 60s") is a quieter control in the cam menu, not in the transport row.

### Volume

A horizontal pill track, 8px tall, 44px hit height, navy fill for the current level, labeled "Music". Value `0` to `100`, step `5`, default `60`. This slider controls only the mix. Cam audio is always muted. There is no cam volume control and no cam mute toggle. Helper copy in the mix picker: "Cam sound is always off."

### Cast state indicator

Use the official Material Cast icons, never a custom glyph.

- Idle: "Cast to your TV" with the `cast` icon, on Home and in the local player top scrim.
- Connecting: pill, canvas color, "Connecting to TV..."
- Connected: pill, `--color-accent-soft` fill, horizon-blue text, official `cast_connected` icon, "Casting to {room}". The phone shows no live cam. A small still or navy panel can stand in for the current window.
- Failed or "TV found, not connected": the same pill in danger-soft, plus a short status line. Do not hide the failure.

### Cam picker (sheet)

Full-height phone sheet, canvas background.

Header: "Cams", Done, and a short count such as "33 in rotation now. 9 checked cams are sleeping."

Time of day:

- **Skip sleeping cams** is on by default and resets each session. A cam is sleeping when local time is midnight to 6 AM. Sleeping cams stay visible in list order, marked with their local time and a "Sleeping" label. They sit out of rotation until morning unless the toggle is turned off.
- **Only daytime** is off by default. When on, rotate only cams where it is 7 AM to 7 PM local time.

Quick picks, in addition to unchecking from the full list: All, None, and a few country or type shortcuts. A filled chip means every cam in that group is currently checked. These change inclusion; they do not hide rows.

Each row: checkbox, name, country and type, local time, Sleeping mark when relevant, and a Play now button. Tapping Play now starts that cam immediately and does not change the checkbox. Play now can open a sleeping cam. Pinning also wins over the night rule.

The starting point is the sheet's `in_rotation` set (today, 44 cams), all checked. The user unchecks from that list, or uses shortcuts. Group actions apply to the current filter.

Local times and the sleeping-cam rule use the sheet's new `timezone` column, which holds an IANA name such as `Asia/Tokyo`. Country is no longer used to guess the clock. If a cell is blank or not a valid IANA name, the cam shows no local time and is never marked Sleeping, so a missing value errs toward showing the cam.

Mock local times in the v2 and v3 artboards assume Monday 16 November 2026, 4:51 PM Pacific (UTC-8), with Tokyo at Asia/Tokyo (9:51 AM).

### Mix picker (sheet)

Same sheet pattern. Header "Mix". Subhead "Tokyo Chill Lab".

**Shuffle is on by default for every session.** When it is on, Window Seat picks a random mix and plays it from the beginning. It does not start partway through a playlist, for now. When a mix ends, shuffle picks another random mix, never the same one twice in a row, and that one also starts at the beginning. The user can turn shuffle off for the current session. With it off, mixes play in sheet order from the current one.

Volume slider lives here too, with the helper "Cam sound is always off."

List of the 16 mixes, one current row:

- English title, body, ink.
- Japanese title in Noto Sans JP, body sm, secondary.
- Duration in minutes, caption, tertiary, trailing.
- The playing row uses accent-soft and a "Playing" mark.

Tapping a row starts that mix immediately on the phone or, while casting, on the TV.

### TV overlay / lower third

Sits in the bottom-left of the 5% safe area. Max width about 46% of the screen.

- Cam name, TV title, overlay-ink.
- Meta: `{city}, {country}` then a mid-dot then local time. No type tag.

The lower third shows for the first 10 seconds after the cam loads, then fades so more of the video is visible. After it fades, the quiet TV is video only.

A pin toast ("Pinned. Staying on this cam.") appears top-center in the safe area for 5 seconds after pinning, then fades. It is temporary, not persistent.

While the feed is paused, a persistent "Paused" pill stays on the TV.

No debug strip unless Debug mode is on. See Debug mode.

## Interaction model

These decisions are locked for the v2 and v3 mocks.

1. **Pause pauses the entire feed.** Video and music both stop. Resume jumps the cam to the live edge and continues the mix.
2. **Pin stops auto rotation only.** The current cam stays on screen and music keeps playing. Pin and pause are visually distinct and can combine.
3. **Volume is for the mix only.** Cam audio is always muted, with no control for it.
4. **Inclusion from the full list, plus shortcuts.** The 44 in-rotation cams start checked. Users uncheck from that list, or use All / None / country / type shortcuts. Skip sleeping cams (midnight to 6 AM local) is on by default and resets each session. Only daytime is optional.
5. **While casting, the phone is a remote.** No live cam on the phone. Same transport and menus, plus a persistent casting pill with the official connected Cast icon.
6. **Local play is full screen.** Controls overlay the cam, with a floating toggle and an exit.

7. **Debug mode is opt-in.** Off by default, toggled in About, remembered in localStorage, and synced to the TV while casting.
8. **Shuffle starts mixes from the top.** A random mix, played from the beginning.
9. **A paused TV rests after 30 minutes.** The Cast session ends so the TV can return to its ambient screen.
10. **Local time comes from the sheet's timezone column.** IANA names such as Asia/Tokyo.

Lite mode and "Open in TV browser" stay available but are secondary. They are not in the primary transport.

## Debug mode

Debug mode is a calm, opt-in diagnostic surface for the person running the app. It replaces the development-only strip, which was always on or set by a query flag.

- **Default:** off.
- **Where:** a "Debug mode" switch in the "This copy" card in About (opened from Home, About and credits), next to the build and list lines.
- **Helper:** "Shows build, list, and Cast details on this phone and on the TV. Off unless you turn it on. This browser remembers your choice."
- **Persistence:** stored in localStorage on the phone. It survives reloads until turned off.
- **Sync:** while casting, the phone sends the existing `debug` command on `urn:x-cast:com.windowseat` (shown as windowseat STRING) with the current value. It sends it when the switch changes and again every time a session starts or resumes. The receiver hides its strip by default and shows it only after `debug` with value true.
- **Off:** hides the phone overlay and the TV strip together.
- **Feedback toasts:** "Debug on." / "Debug on. Showing details on the TV too." / "Debug off."

Required fields:

| Field | Phone | TV |
| --- | --- | --- |
| Build stamp | Phone build, plus TV build while casting | Yes |
| List source and counts, timezone coverage | `sheet 44/16 · tz 44/44` | `List sheet 44/16` |
| Cast state | CAF state, session state, last cast attempt or error | CAF state, appstate, last in, error |
| Namespace | `windowseat STRING`, ns ok | `windowseat STRING`, ready-ns |
| Messages | rx and tx, mirrored from the TV | rx and tx |
| Now | Cam name and id, mix English and Japanese name and id | Cam id, mix id |

**Phone overlay.** A collapsed pill (`DEBUG  {build}`, with a status dot that turns danger red and reads `DEBUG  error` when there is an error). On the local player it sits centered in the top scrim. It stays visible at 70% opacity when the chrome auto-hides, and expands into a night-glass panel that drops from the top and never covers the bottom panel or the floating button. On the casting remote it is a slim row under the casting pill and expands as a white card over the now-playing panel, never over transport or volume. Labels use Caption. Values and ids use Roboto Mono 500 13/18 (debug surfaces only). "Copy details" copies one plain line.

**TV strip.** Bottom right in the 5% safe area, right-aligned, max 44% width. Overlay glass, 24/32 type, overlay-dim text, error text `#E8A3A3`. No auto-fade. It coexists with the lower third (bottom left, 46% max) with a 10% gutter. The pin toast is top center and the Paused pill is top right, so there is no overlap. If space is ever contested, the strip moves; guest-facing elements do not. Hidden by default.

## Pause timeout while casting

A Cast session that stays paused for 30 minutes ends so the TV can return to its ambient screen. The receiver owns the timer, so it still works if the phone sleeps. The timer starts when the feed pauses during a live session. Any phone command while paused (next, previous, pin, mix pick, volume) restarts it. Resume, disconnect, or stop casting cancels it. Local play has no timeout.

- Remote hairline while paused: "Paused. The TV rests after 30 minutes paused."
- At 25 minutes, the TV Paused pill adds "Closing in 5 minutes". Phone toast: "The TV will go back to its home screen in 5 minutes. Tap play to keep watching."
- At 30 minutes, the phone Cast pill returns to idle. Toast: "Paused for 30 minutes, so the TV went back to its home screen. Cast again whenever you like."

## Out of scope for this pass

- A native Google TV app, or publishing the Cast app.
- The Android sender APK (comes after the web remote feels right).
- Building the UI in the app. This document and the Paper file are the brief.
- Changing `js/data.js`. The sheet is gaining a `timezone` column; this document describes how the UI uses it, but this PR does not edit the sheet.

## Open questions

1. Should Debug mode reset each session, like Skip sleeping cams? The brief persists it in localStorage. The risk is that it stays on for weeks and the TV strip becomes wallpaper. If it stays persistent, a softer option is a once-per-session "Debug on." reminder toast.
2. Should the remote let you dismiss the 5-minute warning without resuming, with something like "Keep it paused" that restarts the 30 minutes?
