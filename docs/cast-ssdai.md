# SSDAI live cams on Cast: what else could work

Written by Claude Code, 2026-11-19. Research note, not a decision. Anything marked UNVERIFIED was not tested on a CrKey.

## The problem (measured)
Live cams whose watch page has `playerConfig.daiConfig.enableServerStitchedDai=true` (server-stitched DAI, SSDAI) sit in
BUFFERING with `getVideoLoadedFraction() == 0` in the YouTube IFrame player on our Cast receiver (Google TV, CrKey 1.56):
34 of 34 fail, 0 of 34 working cams have the flag. The same cams play in the phone's IFrame player and in the official
YouTube app on the TV. Build .33 marks them `web-only` in the sheet and fails any zero-media load after 8s.

## Embedded IFrame player on the receiver: knobs we have not tried
- Host: we use `https://www.youtube-nocookie.com`. Trying `https://www.youtube.com` is a one-line change. UNVERIFIED;
  privacy-enhanced mode changes cookies and ad personalization, so it may change the ad path. Cheapest experiment.
- Player params: `autoplay=1&mute=1&playsinline=1` plus `origin`. No documented param disables or selects SSDAI;
  as far as we know nothing in the reference controls ad insertion (https://developers.google.com/youtube/player_parameters, not re-read today, UNVERIFIED).
- Referer / client identity: since 2025-07-09 a missing Referer or client id gives error 153
  (https://developers.google.com/youtube/iframe_api_revision_history). We get state 3, not 153, so this is probably not
  it, but checking the receiver's Referer policy is cheap. UNVERIFIED.
- Likely cause (inference): SSDAI streams need a stitched-manifest or DRM/ad-beacon path that the CrKey embed does not
  support, while the same stream works in desktop Chrome. Nobody documents this; no public bug found.
- IMA / CAF ad breaks: the Cast Web Receiver supports server-stitched ad breaks for media the receiver itself loads
  (https://developers.google.com/cast/docs/web_receiver/ad_breaks), and the IMA DAI SDK has a CAF guide
  (https://developers.google.com/ad-manager/dynamic-ad-insertion/sdk/cast/get-started/pod-serving). Both need our own
  stream URL and an Ad Manager stream; YouTube does not give embedders the HLS manifest, and pulling it from the watch
  page breaks the YouTube ToS. Not a route.
- A CAF receiver with a different player (Shaka, the CAF media element) has the same wall: no legitimate YouTube URL.

## Native Android (sender or Google TV app)
- YouTube Android Player API: fully deprecated, 1.2.2 has known vulnerabilities; Google says use the IFrame API
  (https://developers.google.cn/youtube/android/player). Community wrapper: https://github.com/PierfrancescoSoffritti/Android-YouTube-Player
  (IFrame in a WebView). On a Google TV app that is the same IFrame player, likely with the same SSDAI failure. UNVERIFIED.
  Since 2024-06-24 WebView embeds send an attested app id (Media Integrity), which may change what YouTube serves.
- Intent into the official YouTube TV app: `ACTION_VIEW` with `vnd.youtube:VIDEO_ID`, package
  `com.google.android.youtube.tv` (community sources: https://dev.to/vast-cow/how-to-open-a-specified-youtube-video-in-the-youtube-app-on-android-tv-3d4g,
  https://community.home-assistant.io/t/android-tv-remote-app-links-deep-linking-guide/567921). The official app plays
  SSDAI (measured). Needs our own Google TV app; we lose the overlay, music bed, rotation clock and mute on top.
- Official YouTube Cast receiver: app id `233637DE` (third-party code only: https://beta.pkg.go.dev/github.com/hbbio/go-castv2/configs).
  The web Cast SDK cannot drive it; the YouTube app controls it over the Lounge API, which is private and "CAN BREAK AT
  ANY TIME" (https://beta.pkg.go.dev/github.com/MarcoLucidi01/ytcast/youtube; https://pypi.org/project/pyytlounge/).
  Pairing by TV code or DIAL. It could queue cam ids (rotation by setting the next video), but not our music, captions
  or mute, and only with an unofficial API.
- Constraints: YouTube API ToS (no stream extraction, no ad blocking, no separating audio and video), autoplay on Google
  TV is fine for intents, rotation control from a phone needs a running sender or our own TV app.

## Ranked
1. Try `host: https://www.youtube.com` (and a Referer check) on the receiver for one SSDAI cam: cheap, reversible.
2. Keep web-only skip (this build) as the baseline; it is safe and already measured.
3. Our own Google TV app that launches SSDAI cams in the official YouTube app by intent, IFrame for the rest.
4. Lounge API to the official YouTube receiver: works today in hobby tools, but private and fragile.

Most promising route: test the youtube.com host on the receiver first; if it fails, a small Google TV app using the
official YouTube app intent for SSDAI cams is the only supported way to show them on the TV.
