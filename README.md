# Window Seat

Ambient TV for your living room: public Japan live cams that rotate on their own, with Tokyo Chill Lab mixes playing underneath.

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

Bar buttons: Previous cam, Pin, Next cam, rotation every 30s or 60s, Cams, Music, Play or Pause music, Vol down, Vol up, Fullscreen, About.

Keyboard:

| Key | Action |
| --- | --- |
| N or Next track | Next cam |
| Previous track | Previous cam |
| P | Pin or unpin the current cam |
| M | Mute or unmute music |
| Space | Play or pause music (when no button is focused) |
| F | Fullscreen |
| Esc or Backspace | Close a panel |

The Cams panel lets you choose which cams are in the rotation, watch any cam now, and turn the caption on or off. The Music panel lets you pick a mix, set the volume, and shuffle. Your choices are saved in the browser.

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
