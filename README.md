# Window Seat

Ambient TV for your living room: public Japan live cams that rotate on their own, with Tokyo Chill Lab mixes playing underneath.

**Live:** https://window-seat-lemon.vercel.app

Open it in a TV browser or cast a Chrome tab to an Android TV, pick a mix, and let it run.

- Cams: a curated set of public YouTube live cams across Japan, rotating every 30 or 60 seconds. Pin a feed to stay on it, or skip any feed.
- Music: long mixes from [TOKYO CHILL LAB](https://www.youtube.com/channel/UCfsmpGXkHvQzOLAhCPML91Q). When one ends, the next starts.
- Everything is embedded with the official YouTube player. Nothing is downloaded or restreamed. All video and music belong to their creators; see the credits on the page.

## Use it on your TV

- **Android TV or Google TV browser:** open the site, press Start with the OK button, and leave it running.
- **Cast from Chrome:** open the site in Chrome on a computer, press Start, then choose Cast, Cast tab, and pick your TV. Press F for fullscreen.
- Sound needs one press of Start because browsers block autoplay with sound. If music still does not start, press "Tap to play music".

## Controls

The control bar hides after 4 seconds. Any key, mouse move, or touch brings it back. The first key press only shows the bar, so nothing happens by accident.

Remote:

- Arrows: move around
- OK: choose
- Back: close a panel

Bar buttons: Previous cam, Pause or Play (the cam and the music), Next cam, rotation every 30s or 60s, Lite, Cams, Music, Vol down, Vol up (music only), Fullscreen, Open in TV browser, About. Pin cam is a separate pill under the caption; the cam is always muted.

Open in TV browser: opens a panel with a QR code and the address `https://window-seat-lemon.vercel.app/?tv=1` in large text. Scan it with your phone, then cast, or type the address in your TV browser. The QR code is drawn in your browser by `js/qr.js` (no external service). The same button is on the Start screen.

Lite mode: the Lite button (saved in your browser, off by default) makes the page much lighter on a laptop. It asks for lower quality where YouTube allows (YouTube makes the final choice) and renders the cam in a smaller 1280x720 viewport that is scaled up to fill the screen, which nudges automatic quality down. It also fixes rotation at 60s (the 30s/60s button is disabled), and turns off all animations, transitions and gradient overlays.

Keyboard:

| Key | Action |
| --- | --- |
| N or Next track | Next cam |
| Previous track | Previous cam |
| P | Pin or unpin the current cam |
| M, or Enter/OK with nothing focused | Open or close the menu |
| U | Mute or unmute music |
| Space or Play/Pause | Play or pause the cam and the music (Space only when no button is focused) |
| F | Fullscreen |
| Esc, Backspace or Back | Close a panel, then the menu |

The controls never hide on their own. Tap the quiet **Menu** button in the top right corner to open or close them; your choice is remembered.

The Cams panel lets you choose which cams are in the rotation, watch any cam now, and turn the caption on or off. The Music panel lets you pick a mix, set the music volume, and shuffle. Your choices are saved in the browser.

## Install as an app

Window Seat is an installable web app. In Chrome or Edge, use the install icon in the address bar (or menu, Install Window Seat). On Android, use Add to Home screen. It opens fullscreen in landscape. A small service worker (`sw.js`) caches only the app shell (page, styles, scripts, icons); YouTube video and music are never cached or intercepted. After changing a shell file, bump `CACHE_VERSION` at the top of `sw.js` so installed copies update.

## TV mode

TV mode turns on with `?tv=1`, and automatically on Android TV, Google TV and other smart TV browsers (or a large screen without a fine pointer). `?tv=0` forces it off. In TV mode the controls are larger, focus rings are thick and high contrast, Start is focused and tries fullscreen, and Lite mode is on by default (unless you have already chosen Lite on or off yourself).

## Cast to TV (personal spike)

Chrome on Android can cast Window Seat to a Google TV so the TV itself plays the cam and music (the phone can sleep) and the phone page becomes a remote.

- Receiver URL: https://window-seat-lemon.vercel.app/receiver.html (registered as a Custom Receiver in the Google Cast SDK Developer Console).
- It needs a registered Cast Application ID, and your Cast device must be registered as a test device until the app is published.
- Put the App ID in `js/cast-config.js` (`CAST_APP_ID`). While it is still the placeholder, the Cast button is hidden and the Cast SDK is not loaded.
- The phone is only a remote whenever Cast is connecting or connected: it never plays locally then, and every control goes to the TV. To tell which build each side runs, see "Build" in About on the phone and the debug strip on the TV.
- Debug the receiver in any browser with `receiver.html?debug=1`. It skips the Cast context and exposes `window.__receiver` (`state`, `send(cmd)`, `log`).
- The service worker ignores the receiver files.

## Run locally

It is a plain static site with no build step. Serve the folder with any static server:

```sh
python3 -m http.server 8000
```

Then open http://localhost:8000. Opening index.html straight from disk will not work, because ES modules and the YouTube player need a real http origin. To deploy, put the folder on any static host (for example Vercel) at the root.

## Add or change cams

Edit `js/data.js`:

- `CAMS`: each entry has the YouTube video `id`, a `title`, a `place`, the `channel` name and `channelUrl`, and `default` (true means it is in the rotation by default). The credits are built from this list.
- `MIXES`: each entry has the video `id`, the original `title`, a short English gloss `en`, and the length in `minutes`.

Use live streams that allow embedding. A cam that fails to load is marked "Unavailable right now" for the session and skipped.

## Credits

- Live cams belong to their channels: ANNnewsCH, TBS NEWS DIG Powered by JNN, Kyodo News LiveCamera, ODAIBA TOKYO LIVE, MBS NEWS, Mainichi Shimbun SPORTS, Television OSAKA NEWS, Kyoto City Tourism Association Live, Kawaguchiko Cable Television, STV Sapporo Television, RBC NEWS (Ryukyu Broadcasting), Mine Hills cam, FNN Prime Online, Cross Shinjuku Vision (official), Kyoto Sightseeing Crowd Live, Sapporo Moiwa Ropeway (official), Alfa Live, and Kodama life / Shinkansen Live Broadcast. Links to each channel are on the About page.
- Music: [TOKYO CHILL LAB](https://www.youtube.com/channel/UCfsmpGXkHvQzOLAhCPML91Q).
- Everything is played through the official YouTube IFrame Player. Please support the creators on YouTube.
