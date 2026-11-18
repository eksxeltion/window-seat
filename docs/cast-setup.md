# Cast to TV: one-time setup (personal use)

Window Seat's Cast receiver is live at:

https://window-seat-lemon.vercel.app/receiver.html

Google only lets an unpublished receiver run on devices you register yourself. These steps are a one-time setup. Nothing gets published.

## 1. Create a Google Cast developer account ($5, one time)

1. Sign in with the Google account you want to own this, then open the Google Cast SDK Developer Console: https://cast.google.com/publish
2. Accept the terms and pay the one-time, non-refundable $5 registration fee.
3. Registration can take up to 48 hours to finish processing.

Source: https://support.google.com/cast-developer/answer/4512496

## 2. Add the Custom Receiver app

1. In the console, click **Add New Application**.
2. Choose **Custom Receiver**.
3. Name: `Window Seat`.
4. Receiver Application URL: `https://window-seat-lemon.vercel.app/receiver.html`
5. Leave "Supports casting to audio-only devices" unchecked.
6. Click **Save**, then copy the **Application ID** (a short code like `ABCD1234`).
7. Do not publish it. Unpublished is fine for personal use.

Source: https://developers.google.com/cast/docs/registration#register_your_application

## 3. Find your Google TV's Cast serial number

Use the software (Cast) serial number, not the one on the box. Either:

- **Easiest:** on a computer on the same Wi-Fi, open the Developer Console in Chrome, then use Chrome's Cast menu to cast that tab to the TV. The serial number shows on the TV screen.
- **Or on the TV:** Settings > System > About > select **Android TV OS build** about 7 times until it says you are a developer. Then go to Settings > System > Cast (or Settings > Device Preferences > Google Cast) and read the serial number.

A factory reset changes this number, so you would need to register it again.

Source: https://developers.google.com/cast/docs/registration#devices

## 4. Register the TV as a test device

1. In the console, click **Add New Device**.
2. Enter the serial number and a name, for example `Living room TV`. Click OK.
3. Wait about 15 minutes, until the status says **Ready for Testing**.
4. Restart the TV: unplug its power, wait a few seconds, and plug it back in.

Source: https://developers.google.com/cast/docs/registration#devices

## 5. Send the Application ID to Forge

Send me the Application ID. I put it in `js/cast-config.js` (`CAST_APP_ID_PROD`, production `48BEB59E`) and redeploy.

## 6. Use it

1. On your Pixel, open https://window-seat-lemon.vercel.app in Chrome (same Wi-Fi as the TV).
2. Tap the Cast button and pick your TV.
3. The TV plays the cam and music by itself, and the page turns into a remote. You can lock the phone.

If the Cast button doesn't appear on the Pixel, allow Chrome the **Nearby devices** permission (Android Settings > Apps > Chrome > Permissions).

## Testing the pause timeout in minutes

The receiver reads these URL overrides (see the top of `js/receiver.js`):

- `?debug=1` runs the receiver in any browser. It does not start the Cast context (CAF). It shows the debug strip. Good for a laptop check, not for a real Cast session.
- `?strip=1` shows the debug strip but still starts CAF. So it works as a registered receiver URL.
- `pauseWarnMs` and `pauseEndMs` set the pause warning and the pause end in milliseconds. The defaults are 25 minutes (warning) and 30 minutes (end). A value is used only if it is a positive number. If it is missing, zero, negative or not a number, the default applies. These two work with or without `debug=1`. The code reads them from the URL on every load. If the warning is set later than the end, the warning and the end are due at the same moment, so the warning may never show.

Example for a laptop browser (1 minute warning, 2 minute end):

https://window-seat-lemon.vercel.app/receiver.html?debug=1&pauseWarnMs=60000&pauseEndMs=120000

1. Open the URL in Chrome.
2. Open the browser console.
3. Run `__receiver.send({ type: "pause", value: true })` to pause the feed. The timer starts.
4. Watch the debug strip. The warning shows after 1 minute and the end after 2 minutes.

The limit: a real Cast session loads the receiver URL registered in the Cast console. The phone cannot add query parameters to it. So testing short timers on the TV needs a registered URL that already has the overrides. A second, unpublished test app is the clean way, because the main app keeps its normal URL.

Quick alternative with no code change: in the console, temporarily edit the main Window Seat app's Receiver Application URL to the short-timer URL below, test, then change it back. Console edits can take a few minutes to reach the TV. While it is changed, every cast uses the short timers, so do not forget to revert it.

## Test app console step (done, for reference)

This is already done: the test app exists as `Window Seat (test)`, Application ID `CEB296EE`. The steps stay here for reference.

1. Open https://cast.google.com/publish and sign in.
2. Click **Add New Application**.
3. Choose **Custom Receiver**.
4. Name: `Window Seat (test)`.
5. Receiver Application URL: `https://window-seat-lemon.vercel.app/receiver.html?strip=1&pauseWarnMs=60000&pauseEndMs=120000`
   This gives a 1 minute warning and a 2 minute end. It uses `strip=1`, not `debug=1`, because `debug=1` does not start CAF and the TV would never connect.
6. Leave it unpublished.
7. Click **Save**.
8. Send me the new **Application ID** (it is `CEB296EE`, now in `js/cast-config.js` as `CAST_APP_ID_TEST`).

Registered test devices apply to all apps on the account. So the living room TV should not need to be added again.

Source: https://developers.google.com/cast/docs/registration

## Switching the phone to the test receiver

The web app uses the production app (`48BEB59E`) unless you opt in to the test app (`CEB296EE`). Two ways to turn it on:

- Open https://window-seat-lemon.vercel.app/?castApp=test (`?castApp=CEB296EE` works too).
- Or open About, find **Cast test receiver** under Debug mode (visible even with Debug off), and turn it on. The page reloads.

The choice is saved on the phone (`windowSeat.castApp` in local storage), so later loads keep using the test app until you switch back. The Cast SDK reads the app ID once when the page loads, so a change always applies through a reload.

To switch back: turn **Cast test receiver** off in About (the page reloads with `?castApp=prod`, so the choice applies even if the phone cannot save it), or open `?castApp=prod` (`?castApp=main` and `?castApp=48BEB59E` work too).

How to confirm which app is active:

- The About switch shows the app this page load uses, and the line under it names the ID.
- With Debug mode on, the phone debug panel CAST section shows `App CEB296EE (test receiver)` or `App 48BEB59E (production)`.
- On the TV debug strip (always on for the test app, because its URL has `strip=1`), the second line shows `app CEB296EE`, and the cam line ends with `override warn 1:00 end 2:00`.

If you are already casting, stop casting first, then switch and cast again. The phone only joins a running session of the app it is using.

## Notes

- Test page without a Cast device: https://window-seat-lemon.vercel.app/receiver.html?debug=1
- This is for personal use. The receiver plays two YouTube embeds at once, which YouTube's embed rules don't allow for a published app.
