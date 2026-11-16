"""Fake Cast platform for the real CAF receiver: serves ws://localhost:8008/v2/ipc (the CAF receiver connects there
in a normal browser). Importable: FakePlatform(sender_id).start() runs it in a thread; .log holds receiver messages."""
import asyncio, json, threading, time
import websockets

NS = "urn:x-cast:com.windowseat"
SYS = "urn:x-cast:com.google.cast.system"
PORT = 8008


class FakePlatform:
    def __init__(self, sender_id="sender-1"):
        self.sender_id = sender_id
        self.log = []  # parsed JSON frames from the receiver
        self.ready = threading.Event()
        self.connected = threading.Event()
        self.error = None
        self._loop = None
        self._ws = None

    def _run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        async def boot():
            self._server = await websockets.serve(self._handler, "localhost", PORT)
        try:
            self._loop.run_until_complete(boot())
        except OSError as e:
            self.error = e
            self.ready.set()
            return
        self.ready.set()
        self._loop.run_forever()

    async def _handler(self, ws):
        self._ws = ws
        self.connected.set()
        async for m in ws:
            try:
                self.log.append(json.loads(m))
            except Exception:
                self.log.append({"raw": str(m)})

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()
        self.ready.wait(5)
        return self.error is None

    def stop(self):
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)

    def _send(self, ns, sender, data):
        fut = asyncio.run_coroutine_threadsafe(self._ws.send(json.dumps({"namespace": ns, "senderId": sender, "data": data})), self._loop)
        fut.result(5)

    def system_ready(self):
        self._send(SYS, "SystemSender", json.dumps({"type": "ready", "launchingSenderId": self.sender_id, "applicationId": "48BEB59E", "applicationName": "Window Seat", "version": "x"}))

    def sender_connected(self):
        self._send(SYS, "SystemSender", json.dumps({"type": "senderconnected", "senderId": self.sender_id, "userAgent": "phone"}))

    def to_receiver(self, obj):
        self._send(NS, self.sender_id, json.dumps(obj))

    def parsed(self):
        """(namespace, senderId, data-as-object) for every receiver frame."""
        out = []
        for o in self.log:
            d = o.get("data")
            try:
                d = json.loads(d) if isinstance(d, str) else d
            except Exception:
                pass
            out.append((o.get("namespace"), o.get("senderId"), d))
        return out

    def wait_for(self, pred, timeout=8):
        end = time.time() + timeout
        while time.time() < end:
            if any(pred(*x) for x in self.parsed()):
                return True
            time.sleep(0.1)
        return False
