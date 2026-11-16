// Minimal mock of the Cast Web Sender framework for headless tests.
(function () {
  const listeners = {};
  const sent = [];
  const msgListeners = [];
  const session = {
    sendMessage(ns, data) { sent.push({ ns, data: JSON.parse(JSON.stringify(data)) }); return Promise.resolve(); },
    addMessageListener(ns, fn) { msgListeners.push(fn); },
    getCastDevice() { return { friendlyName: "Mock TV" }; },
  };
  let castState = "NOT_CONNECTED";
  const ctx = {
    setOptions() {},
    getCastState: () => castState,
    addEventListener(t, fn) { (listeners[t] = listeners[t] || []).push(fn); },
    requestSession() { return Promise.resolve(); },
    endCurrentSession() { (listeners.sessionstatechanged || []).forEach((f) => f({ sessionState: "SESSION_ENDED" })); },
    getCurrentSession: () => session,
  };
  window.chrome = window.chrome || {};
  window.chrome.cast = { AutoJoinPolicy: { ORIGIN_SCOPED: "origin_scoped" } };
  window.cast = { framework: {
    CastContext: { getInstance: () => ctx },
    SessionState: { SESSION_STARTED: "SESSION_STARTED", SESSION_RESUMED: "SESSION_RESUMED", SESSION_ENDED: "SESSION_ENDED" },
    CastContextEventType: { SESSION_STATE_CHANGED: "sessionstatechanged", CAST_STATE_CHANGED: "caststatechanged" },
    CastState: { NO_DEVICES_AVAILABLE: "NO_DEVICES_AVAILABLE", NOT_CONNECTED: "NOT_CONNECTED", CONNECTING: "CONNECTING", CONNECTED: "CONNECTED" },
  } };
  window.__mockCast = {
    sent,
    start() { castState = "CONNECTED"; (listeners.sessionstatechanged || []).forEach((f) => f({ sessionState: "SESSION_STARTED", session })); },
    reply(obj) { msgListeners.forEach((f) => f("urn:x-cast:com.windowseat", JSON.stringify(obj))); },
  };
  setTimeout(() => window.__onGCastApiAvailable && window.__onGCastApiAvailable(true), 0);
})();
