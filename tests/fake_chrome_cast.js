// Fake chrome.cast v1 API with the semantics of Chrome Android's clank cast_sender.js:
// a second requestSession while one is pending fails at once with invalid_parameter "Already requesting session".
(function () {
  const log = (window.__fakeLog = []);
  let pending = null, apiConfig = null; const actionListeners = [];
  const C = (window.chrome = window.chrome || {}).cast = {
    AutoJoinPolicy: { ORIGIN_SCOPED: "origin_scoped", TAB_AND_ORIGIN_SCOPED: "tab_and_origin_scoped", PAGE_SCOPED: "page_scoped" },
    DefaultActionPolicy: { CREATE_SESSION: "create_session" },
    ReceiverAvailability: { AVAILABLE: "available", UNAVAILABLE: "unavailable" },
    ReceiverAction: { CAST: "cast", STOP: "stop" },
    SessionStatus: { CONNECTED: "connected", DISCONNECTED: "disconnected", STOPPED: "stopped" },
    ErrorCode: { INVALID_PARAMETER: "invalid_parameter", CANCEL: "cancel" },
    timeout: { requestSession: 10000 },
    Error: function (code, d) { this.code = code; this.description = d; },
    SessionRequest: function (appId) { this.appId = appId; },
    ApiConfig: function (req, sl, rl, aj) { this.sessionRequest = req; this.sessionListener = sl; this.receiverListener = rl; this.autoJoinPolicy = aj; },
    initialize(cfg, ok) { apiConfig = cfg; log.push("initialize"); setTimeout(() => cfg.receiverListener("available"), 0); ok && ok(); },
    addReceiverActionListener(fn) { actionListeners.push(fn); },
    requestSession(ok, err) {
      log.push("requestSession");
      if (pending) { log.push("requestSession rejected: Already requesting session"); return err(new C.Error("invalid_parameter", "Already requesting session")); }
      pending = { ok, err };
    },
    requestSessionById(id) { log.push("requestSessionById " + id); },
  };
  const sent = (window.__fakeSent = []);
  function makeSession() {
    const upd = [], msg = [];
    const o = {
      sessionId: "s1", appId: "48BEB59E", displayName: "Window Seat", statusText: "", namespaces: [{ name: "urn:x-cast:com.windowseat" }],
      status: "connected", media: [], receiver: { friendlyName: "Living Room TV", volume: { level: 1, muted: false } },
      addUpdateListener(f) { upd.push(f); }, addMediaListener() {}, loadMedia() {}, queueLoad() {}, addMessageListener(ns, f) { msg.push(f); }, removeMessageListener() {},
      sendMessage(ns, m, ok) { sent.push({ ns, data: m }); ok && ok(); },
      leave(ok) { this.status = "disconnected"; upd.forEach((f) => f(true)); ok && ok(); }, stop(ok) { this.status = "stopped"; upd.forEach((f) => f(false)); ok && ok(); },
      _reply(o) { msg.forEach((f) => f("urn:x-cast:com.windowseat", JSON.stringify(o))); },
    };
    o._upd = upd;
    return o;
  }
  window.__fake = {
    // The user picks the TV in the native picker: receiver action, then the session arrives.
    pick() { actionListeners.forEach((f) => f({ friendlyName: "Living Room TV" }, "cast")); const s = (window.__fakeSession = makeSession()); const p = pending; pending = null; if (p) p.ok(s); else apiConfig.sessionListener(s); return true; },
    // Session arriving with no request (auto-join / Chrome UI)
    join() { const s = (window.__fakeSession = makeSession()); apiConfig.sessionListener(s); },
    pending: () => !!pending,
  };
})();
