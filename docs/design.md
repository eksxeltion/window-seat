# Window Seat design system v1

This is the design source of truth for the Window Seat visual redesign. It is a proposal, not shipped UI. The matching mocks live in the Paper file [Window Seat redesign v1](https://app.paper.design/file/01M7KCRQZVMH15C8SWE16RHQFE). Comment on the artboards there; we will iterate from those notes before any app code changes.

The live app today is a dark, gold-accent media player with a crowded control bar. The redesign keeps the product job (phone as remote, TV as the window) and makes the phone feel like a polished consumer app: bright, calm, and easy to use one-handed.

## Mood

**Maritime, late afternoon at a harbor window.** Soft sea fog on the walls, deep navy for ink and actions, one horizon-blue accent. The scene is a bright living room with a window open to water and city light, not a cinema or a travel brochure.

This is a deliberate step away from the current black-and-gold look, and also away from the common warm-linen-plus-terracotta palette. Airbnb is a reference for hospitality, spacing, and photography as the hero, not for coral buttons on cream.

On the TV, the same palette goes nocturnal: the video is the picture, and type sits in a dark water-glass overlay that fades after a change.

## Personality

- Quiet hospitality, not a media-player chrome kit.
- One strong color moment (horizon blue or navy) rather than a rainbow of accents.
- Photography (the live cam) does the visual work. UI stays out of the way.
- Large, obvious controls, usable from the couch and with a thumb on a Pixel.

## Color

### Phone and start (bright)

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

### TV overlay (nocturnal variant)

Used only on top of video, including the TV lower third and any full-bleed cinema moment. Never as the phone's default chrome.

| Token | Value | Role |
| --- | --- | --- |
| `--color-overlay` | `rgba(10, 18, 24, 0.62)` | Glass behind the lower third |
| `--color-overlay-strong` | `rgba(10, 18, 24, 0.82)` | Toasts, command confirmation |
| `--color-overlay-ink` | `#F3F5F7` | Title and brand |
| `--color-overlay-dim` | `rgba(243, 245, 247, 0.78)` | City, country, mix line |
| `--color-overlay-line` | `rgba(255, 255, 255, 0.16)` | Hairline on glass |
| `--color-overlay-accent` | `#8EC4D4` | Playing or pinned marker, readable on video |

The video itself stays untouched. No colored grade on the cam. A soft bottom scrim (black at 45% fading to transparent by 28% of the height) exists only while the lower third is visible.

### Mapping from the current app

| Today | Redesign |
| --- | --- |
| `#000` body, `#ffd27f` gold accent, cream type | Bright fog canvas and navy ink on the phone |
| Glass bar `rgba(18, 18, 24, 0.82)` | White sheets and a navy transport cluster |
| Gold as the only accent | Horizon blue for selection and "now playing" only |

## Type

**Display:** Fraunces (soft optical serif). Wordmark, start-screen title, TV cam title. Weight 500 or 600. Tracking slightly tight on large sizes (`-0.02em` at 40px and up).

**UI:** Plus Jakarta Sans. Everything else: buttons, meta, lists, chips, mix titles. Weights 500, 600, 700. Labels and small caps use weight 500 with open tracking (`0.06em`).

If either family is missing at build time, fall back to `Source Serif 4` for display and `system-ui` for UI, and keep the same sizes.

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
| TV meta | 32 / 40 | Plus Jakarta 500 | City, country, type |
| TV mix | 24 / 32 | Plus Jakarta 500 | Current mix, English title |
| TV brand | 18 / 24 | Plus Jakarta 600, tracking 0.14em, uppercase | WINDOW SEAT |
| TV toast | 28 / 36 | Plus Jakarta 600 | Command confirmation ("Pinned", "Next cam") |

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
| `--radius-pill` | 999px | Primary CTA, cast pill, volume track |

## Elevation

Shadows are cool and short, like light from a north window. No heavy 2010s card stacks.

- **Level 0:** flat on canvas, hairline `--color-line` if a boundary is needed.
- **Level 1 (resting card or video frame):** `0 8px 24px rgba(16, 37, 54, 0.08)`.
- **Level 2 (sheet or menu):** `0 16px 40px rgba(16, 37, 54, 0.12)`.
- **Level 3 (toast):** `0 8px 24px rgba(16, 37, 54, 0.18)`.

On TV, prefer dim glass over drop shadows. Shadows disappear on a 10-foot dark screen.

## Iconography

Simple 24px line icons, 2px stroke, round caps and joins. Navy on bright surfaces, fog-white on TV overlays and on primary buttons. Do not use emoji as icons.

Needed set: previous, play, pause, next, volume, volume muted, pin, pinned, cam, mix, cast, cast connected, close, search, check, exclude, chevron, TV.

Hit targets on the phone are at least 48px, and 64px for transport (previous, play/pause, next).

## Motion

Calm and short. The living room should feel still.

| Motion | Duration | Easing | Use |
| --- | --- | --- | --- |
| Fade | 200ms | ease-out | Sheets, toasts, chips |
| Slide | 280ms | cubic-bezier(0.2, 0.8, 0.2, 1) | Bottom sheets, lower third in |
| Cam crossfade | 400ms | ease | Existing cam blackout, keep this |
| Lower third hold | 5s visible after a cam or mix change, then 800ms fade out |  | TV only |
| Press | 80ms | ease-out | Button scale to 0.98 |

No bounce. No looping decorative motion. The cam is already moving.

## Layout principles

- Phone artboards are Pixel-sized, 412x915, with a status bar except where the OS chrome is intentionally hidden.
- Local playback: the cam is a rounded 16:9 card near the top of a bright canvas. Transport and now-playing live *under* the picture, not on top of it. The phone is a player, not a tiny TV.
- Casting: the phone drops the live video and becomes a remote. A clear "Casting to TV" pill is always visible. The TV holds the picture.
- TV: full-bleed cam, no chrome except a lower third that appears on change or when a command arrives.

## Component specs

### Wordmark

"Window Seat" in Fraunces 600, sentence case. No icon required; if one is used, a simple four-pane window in navy, 32px, to the left of the words. Do not recreate the current gold window glyph as the default mark.

### Primary button

Height 52px, pill radius, navy fill, white label in Plus Jakarta 600. Full width on the start screen. Hover and press darken one step toward `#0A1B28`.

### Ghost button

Same height, transparent fill, navy label, no border (or a hairline only if it sits on a busy photo). Used for "Open in TV browser" and "About".

### Now-playing bar

A block under the video card (local) or under the casting pill (remote).

- Line 1: cam name, Title sm, ink.
- Line 2: `{city}, {country}` plus a type chip (street, harbor, airport, and so on). Body sm and caption.
- Line 3: mix English title, body sm, tertiary, with a small mix note icon. Japanese title can sit in a caption if there is room; otherwise it lives in the mix sheet.
- Trailing: a pin icon button. Pinned state fills the icon and uses `--color-accent-soft` behind it.

Tapping the cam name or the type chip opens the cam menu. Tapping the mix line opens the mix picker.

### Transport controls

A centered cluster, like a music player, but it operates on the *cam*:

- Previous cam (64px).
- Play / pause (72px, navy filled circle, white icon). Pause means "hold this view, do not rotate." Play means "resume rotation."
- Next cam (64px).

This is the main interaction on both the local player and the remote. It replaces the current wrap-around bar of many equal buttons.

Rotation interval ("Every 60s") is a quieter control in the cam menu, not in the transport row.

### Volume

A horizontal pill track, 8px tall, 44px hit height, navy fill for the current level. Label "Mix" to the left so it is obvious this is music volume, not cam volume. Value `0` to `100`, step `5`, default `60` (same as today). A speaker icon on the left mutes or unmutes.

Cam audio stays muted unless we later add an explicit "cam sound" control. Some feeds carry their own music (for example Victoria Harbour from TST) and would clash with the mix.

### Cast state indicator

- Idle: a ghost "Cast to TV" button with the Cast icon, same as today in placement on the start screen and on the local player.
- Connecting: pill, canvas color, "Connecting to TV..."
- Connected: pill, `--color-accent-soft` fill, horizon-blue text, "Casting to TV". The local video card is replaced by a still or a simple navy panel so the phone is clearly a remote.
- Failed or "TV found, not connected": the same pill in danger-soft, plus a short status line. Do not hide the failure.

### Cam picker (sheet)

Full-height phone sheet, canvas background, rounded top 32px if presented as a modal, or a full screen if pushed.

Header: "Cams", close, and a short count such as "38 of 44 in rotation".

Tools row:

- Pin current cam (also available on the now-playing bar).
- Rotation interval (60s, 2m, 5m, off).
- "All defaults" and "All on" to reset the multi-select.

Filter chips, horizontal, scrollable:

- Grouped by country (Japan, France, Italy, Australia, and others present in the sheet).
- Then city, when a country is selected.
- Then type: street, crossing, skyline, landscape, mountain, beach, harbor, river, train, airport, temple, nature, animals, snow, nightlife.

Chips are inclusive filters for *browsing* the list. They do not by themselves change the rotation. A selected chip uses accent-soft fill and accent text.

Multi-select for the rotation:

- Each row has a checkbox, a name, `{city}, {country}`, and a type chip.
- Checked means the cam is in rotation. Unchecked excludes it.
- The starting point is the sheet's `in_rotation` flag (today, 44 cams). The user subtracts or adds from that set.
- Group actions: "Include all visible" and "Exclude all visible" apply to the current filter, so "exclude all Airport" or "include only Kyoto" is two taps.
- The current cam, if excluded, keeps playing until the next rotation or until the user hits Next.

Pin is independent of inclusion. Pinning an excluded cam plays it now and holds it; unpinning returns to the included set.

### Mix picker (sheet)

Same sheet pattern. Header "Mix". Subhead "Tokyo Chill Lab".

Tools: play/pause for *music* (separate from cam transport), mute, shuffle, and the volume slider.

List of the 16 mixes, one current row:

- English title, body, ink.
- Japanese title, body sm, secondary.
- Duration in minutes, caption, tertiary, trailing.
- The playing row uses accent-soft and a small playing mark.

Tapping a row starts that mix immediately on the phone or, while casting, on the TV.

### TV overlay / lower third

Sits in the bottom-left of the 5% safe area. Max width about 46% of the screen.

- Brand line first, small, uppercase, overlay-dim.
- Cam name, TV title, overlay-ink.
- Meta: `{city}, {country}` then a mid-dot then type.
- Mix English title, TV mix size, overlay-dim.

A compact command toast ("Next", "Pinned", "Mix changed") appears top-center in the safe area for about 2 seconds after a remote command.

The lower third appears when the cam or mix changes, and when the remote is first used after a quiet period. It holds for 5 seconds, then fades. No persistent debug strip in the designed state (the current debug strip stays a development-only flag).

### Start screen

Bright canvas. Wordmark, a one-line tagline, a full-width Start button, then Cast to TV, then quieter ghost actions for "Open in TV browser" and "About and credits".

Proposed tagline, open for comment: "Live windows onto the world, with soft music, made for your TV." This reflects the list as it stands (Japan plus other cities). The About copy can still lead with Japan.

## Proposed interaction model

These are design decisions baked into the mocks, and they are the main things to confirm or reject in Paper comments.

1. **Transport is for the cam.** Previous, pause (hold this view), next. Pin is a stronger hold: stay on this cam until I unpin, even if I leave the session and come back (once that persistence exists). Pause is session-only.
2. **Volume is for the mix only.** Cam audio stays muted by default.
3. **Two menus, not one overflow bar.** Cam menu and Mix menu. Interval, lite, caption, and select tools live in the cam menu. Shuffle and volume live in the mix menu.
4. **Inclusion, not a blank playlist.** The sheet's 44 in-rotation cams are the default set. Multi-select excludes or includes from that set, including by country, city, or type.
5. **While casting, the phone is a remote.** No live cam on the phone. Same transport, same menus, plus a persistent casting pill.

Lite mode and "Open in TV browser" stay available but are secondary. They are not in the primary transport.

## Out of scope for this pass

- A native Google TV app, or publishing the Cast app.
- The Android sender APK (comes after the web remote feels right).
- Building the UI in the app. This document and the Paper file are the brief.
- Changing the Google Sheet or `js/data.js`.

## Open questions

See the companion note in the Paper file and the pull request. The short list:

1. Pause versus pin, as described above. Is the distinction useful, or should one control do both?
2. Should volume ever expose cam audio, especially for feeds that already play their own sound?
3. Keep a Japan-first tagline, or widen it now that other cities are in the rotation?
4. Is "exclude from the 44" the right multi-select model, or would you rather build a short include-only playlist each time?
5. For local playback on the phone, is a rounded cam card on a bright canvas right, or do you want full-bleed video with controls overlaid?
