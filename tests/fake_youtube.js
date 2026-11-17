// Deterministic stand-in for the YouTube IFrame API (served in place of /iframe_api by tests).
// Every call on every player is recorded in player.calls, and state changes fire onStateChange.
(function () {
  const players = [];
  const S = { UNSTARTED: -1, ENDED: 0, PLAYING: 1, PAUSED: 2, BUFFERING: 3, CUED: 5 };
  function Player(target, opts) {
    const el = typeof target === "string" ? document.getElementById(target) : target;
    const id = (el && el.id) || "";
    this.kind = id === "music-player" ? "music" : "cam";
    this.opts = opts || {};
    this.calls = [];
    this.state = S.UNSTARTED;
    this.vid = this.opts.videoId || null;
    this.vol = 100;
    this.muted = !!(this.opts.playerVars && this.opts.playerVars.mute);
    this.time = 0;
    this.destroyed = false;
    players.push(this);
    if (this.vid) this.state = S.CUED;
    setTimeout(() => this.opts.events && this.opts.events.onReady && this.opts.events.onReady({ target: this }), 30);
  }
  const P = Player.prototype;
  P._rec = function (name, args) {
    this.calls.push([name].concat(Array.from(args)));
  };
  P._set = function (st) {
    this.state = st;
    setTimeout(() => !this.destroyed && this.opts.events && this.opts.events.onStateChange && this.opts.events.onStateChange({ data: st, target: this }), 0);
  };
  P.loadVideoById = function (arg) {
    this._rec("loadVideoById", arguments);
    this.vid = typeof arg === "object" && arg ? arg.videoId : arg;
    this.time = 0;
    this._set(S.BUFFERING);
    const vid = this.vid;
    setTimeout(() => {
      if (this.vid === vid && this.state === S.BUFFERING) this._set(S.PLAYING);
    }, 120);
  };
  P.cueVideoById = function (arg) {
    this._rec("cueVideoById", arguments);
    this.vid = typeof arg === "object" && arg ? arg.videoId : arg;
    this._set(S.CUED);
  };
  P.playVideo = function () {
    this._rec("playVideo", arguments);
    if (this.vid) this._set(S.PLAYING);
  };
  P.pauseVideo = function () {
    this._rec("pauseVideo", arguments);
    if (this.vid) this._set(S.PAUSED);
  };
  P.stopVideo = function () {
    this._rec("stopVideo", arguments);
    this._set(S.CUED);
  };
  P.seekTo = function (t) {
    this._rec("seekTo", arguments);
    this.time = t;
  };
  P.getDuration = function () {
    return this.kind === "cam" ? 5000 : 3600;
  };
  P.getCurrentTime = function () {
    return this.time;
  };
  P.mute = function () {
    this._rec("mute", arguments);
    this.muted = true;
  };
  P.unMute = function () {
    this._rec("unMute", arguments);
    this.muted = false;
  };
  P.isMuted = function () {
    return this.muted;
  };
  P.setVolume = function (v) {
    this._rec("setVolume", arguments);
    this.vol = v;
  };
  P.getVolume = function () {
    return this.vol;
  };
  P.getPlayerState = function () {
    return this.state;
  };
  P.getVideoData = function () {
    return { video_id: this.vid || "" };
  };
  P.getIframe = function () {
    return null;
  };
  P.setPlaybackQuality = function () {};
  P.destroy = function () {
    this._rec("destroy", arguments);
    this.destroyed = true;
  };
  const live = (kind) => players.filter((p) => p.kind === kind && !p.destroyed).pop() || null;
  window.__yt = { players, cam: () => live("cam"), music: () => live("music") };
  window.YT = { Player, PlayerState: S };
  setTimeout(() => typeof window.onYouTubeIframeAPIReady === "function" && window.onYouTubeIframeAPIReady(), 0);
})();
