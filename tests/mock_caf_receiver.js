// Minimal mock of the CAF Web Receiver framework: records the call order and lets a test inject messages.
(function () {
  const calls = [];
  const listeners = {};
  const sysListeners = {};
  const sent = [];
  let started = false;
  const nsTypes = {}; // namespace -> message type, as the real framework tracks it
  const ctx = {
    addCustomMessageListener(ns, fn) {
      calls.push("addCustomMessageListener:" + ns);
      // Real rule: adding a listener before start() creates the namespace with the default type (JSON).
      if (!nsTypes[ns]) nsTypes[ns] = "JSON";
      listeners[ns] = fn;
    },
    addEventListener(t, fn) { (sysListeners[t] = sysListeners[t] || []).push(fn); },
    getPlayerManager() { return { setMediaElement() { calls.push("setMediaElement"); } }; },
    getSenders() { return [{ id: "sender-1" }]; },
    sendCustomMessage(ns, senderId, data) {
      if (!started) throw new Error("not started");
      sent.push({ ns, senderId, data });
    },
    start(opts) {
      calls.push("start");
      // Real rule: start() requesting a different type for an existing namespace throws.
      for (const [ns, t] of Object.entries((opts && opts.customNamespaces) || {})) {
        if (nsTypes[ns] && nsTypes[ns] !== t) throw new Error("Invalid messageType for the namespace");
        nsTypes[ns] = t;
      }
      started = true; window.__caf.options = opts; (sysListeners.ready || []).forEach((f) => f({})); },
    stop() { calls.push("stop"); },
  };
  window.cast = { framework: {
    CastReceiverContext: { getInstance: () => ctx },
    CastReceiverOptions: function () {},
    system: { MessageType: { JSON: "JSON", STRING: "STRING" }, EventType: { SENDER_CONNECTED: "senderconnected", SENDER_DISCONNECTED: "senderdisconnected", READY: "ready" } },
  } };
  window.__caf = {
    calls, sent,
    message(ns, data, senderId) { listeners[ns]({ data, senderId, type: "message" }); },
    connect() { (sysListeners.senderconnected || []).forEach((f) => f({ senderId: "sender-1" })); },
  };
})();
