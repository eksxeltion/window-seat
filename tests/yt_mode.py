"""YouTube mode for the tests that normally drive the REAL YouTube IFrame API.

Default: the real API over the network, unchanged.
WS_FAKE_YOUTUBE=1: https://www.youtube.com/iframe_api is fulfilled with tests/fake_youtube.js, which replaces
YT.Player without creating any iframe, so nothing else from YouTube is loaded. CI sets this because YouTube
apparently does not play live-stream embeds for datacenter runner IPs (the cam never reaches PLAYING, camId
stays None), while VOD music still plays. Every assertion still runs in both modes."""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
FAKE = os.environ.get("WS_FAKE_YOUTUBE") == "1"


def announce():
    if FAKE:
        print("YOUTUBE MODE: fake (tests/fake_youtube.js, WS_FAKE_YOUTUBE=1)", flush=True)
    else:
        print("YOUTUBE MODE: real IFrame API over the network (set WS_FAKE_YOUTUBE=1 for the fake)", flush=True)


def route(pg):
    """Call before pg.goto. No-op unless WS_FAKE_YOUTUBE=1."""
    if not FAKE:
        return
    with open(os.path.join(HERE, "fake_youtube.js")) as f:
        body = f.read()
    pg.route("**/iframe_api", lambda r: r.fulfill(status=200, content_type="text/javascript", body=body))
