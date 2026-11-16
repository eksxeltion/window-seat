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

- **Easiest:** on a computer on the same wifi, open the Developer Console in Chrome, then use Chrome's Cast menu to cast that tab to the TV. The serial number shows on the TV screen.
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

Send me the Application ID. I put it in `js/cast-config.js` (`CAST_APP_ID`) and redeploy.

## 6. Use it

1. On your Pixel, open https://window-seat-lemon.vercel.app in Chrome (same wifi as the TV).
2. Tap the Cast button and pick your TV.
3. The TV plays the cam and music by itself, and the page turns into a remote. You can lock the phone.

If the Cast button doesn't appear on the Pixel, allow Chrome the **Nearby devices** permission (Android Settings > Apps > Chrome > Permissions).

## Notes

- Test page without a Cast device: https://window-seat-lemon.vercel.app/receiver.html?debug=1
- This is for personal use. The receiver plays two YouTube embeds at once, which YouTube's embed rules don't allow for a published app.
