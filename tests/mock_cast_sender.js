// Minimal mock of the Cast Web Sender framework for headless tests.
(function () {
  const listeners = {};
  const sent = [];
  const msgListeners = [];
  const sessListeners = {};
  let options = null; // the last CastContext.setOptions argument
  const session = {
    sendMessage(ns, data) {
      sent.push({ ns, data: JSON.parse(JSON.stringify(data)) });
      if (window.__mockFail) return Promise.reject(window.__mockFail);
      return Promise.resolve();
    },
    getSessionObj() { return { appId: window.__mockAppId || (options && options.receiverApplicationId) || "48BEB59E", namespaces: window.__mockNs || [{ name: "urn:x-cast:com.windowseat" }] }; },
    addMessageListener(ns, fn) { msgListeners.push(fn); },
    getCastDevice() { return { friendlyName: "Mock TV" }; },
    getSessionState() { return sessState; },
    addEventListener(type, fn) { (sessListeners[type] = sessListeners[type] || []).push(fn); },
  };
  let sessState = "SESSION_STARTED";
  let hasSession = !!window.__mockResume;
  const fire = (t, e) => (listeners[t] || []).forEach((f) => f(e));
  let ctxState = window.__mockResume ? "SESSION_RESUMED" : "NO_SESSION";
  let castState = window.__mockResume ? "CONNECTED" : "NOT_CONNECTED";
  const ctx = {
    setOptions(opts) { options = opts; },
    getCastState: () => castState,
    getSessionState: () => ctxState,
    addEventListener(t, fn) { (listeners[t] = listeners[t] || []).push(fn); },
    requestSession() { return Promise.resolve(); },
    endCurrentSession() {
      // Real CAF order: events fire while getCurrentSession() still returns the ending session, then it is cleared.
      sessState = ctxState = "SESSION_ENDED"; castState = "NOT_CONNECTED";
      fire("sessionstatechanged", { sessionState: "SESSION_ENDED", session });
      fire("caststatechanged", { castState });
      hasSession = false; ctxState = "NO_SESSION";
    },
    getCurrentSession: () => (hasSession ? session : null),
  };
  window.chrome = window.chrome || {};
  window.chrome.cast = { AutoJoinPolicy: { ORIGIN_SCOPED: "origin_scoped" } };
  window.cast = { framework: {
    CastContext: { getInstance: () => ctx },
    SessionEventType: { APPLICATION_STATUS_CHANGED: "applicationstatuschanged", APPLICATION_METADATA_CHANGED: "applicationmetadatachanged" },
    SessionState: { SESSION_STARTED: "SESSION_STARTED", SESSION_RESUMED: "SESSION_RESUMED", SESSION_ENDED: "SESSION_ENDED", SESSION_STARTING: "SESSION_STARTING", SESSION_START_FAILED: "SESSION_START_FAILED", SESSION_ENDING: "SESSION_ENDING", NO_SESSION: "NO_SESSION" },
    CastContextEventType: { SESSION_STATE_CHANGED: "sessionstatechanged", CAST_STATE_CHANGED: "caststatechanged" },
    CastState: { NO_DEVICES_AVAILABLE: "NO_DEVICES_AVAILABLE", NOT_CONNECTED: "NOT_CONNECTED", CONNECTING: "CONNECTING", CONNECTED: "CONNECTED" },
  } };
  window.__mockCast = {
    sent,
    get options() { return options; },
    start() { castState = "CONNECTED"; hasSession = true; sessState = ctxState = "SESSION_STARTED"; (listeners.sessionstatechanged || []).forEach((f) => f({ sessionState: "SESSION_STARTED", session })); },
    // TV picked, receiver launching: no session yet.
    connecting() { castState = "CONNECTING"; hasSession = false; ctxState = "SESSION_STARTING"; fire("caststatechanged", { castState }); fire("sessionstatechanged", { sessionState: "SESSION_STARTING" }); },
    // CAF has the session but no event reaches the page.
    silent() { castState = "CONNECTED"; hasSession = true; sessState = ctxState = "SESSION_STARTED"; },
    fail() { castState = "NOT_CONNECTED"; hasSession = false; ctxState = "SESSION_START_FAILED"; fire("sessionstatechanged", { sessionState: "SESSION_START_FAILED", errorCode: "receiver_unavailable" }); fire("caststatechanged", { castState }); },
    fireSession(type) { (sessListeners[type] || []).forEach((f) => f({ type })); },
    reply(obj) { msgListeners.forEach((f) => f("urn:x-cast:com.windowseat", JSON.stringify(obj))); },
  };
  setTimeout(() => window.__onGCastApiAvailable && window.__onGCastApiAvailable(true), 0);
})();
