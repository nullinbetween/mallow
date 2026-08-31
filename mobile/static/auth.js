/*
 * Who is using Mallow, and how the page says so.
 *
 * Two ways in, both ending at one verified uid:
 *   Google sign-in   the account this workspace belongs to
 *   just look around anonymous, and honestly labelled — it lives in this
 *                    browser, and clearing site data can lose it
 *
 * When no Firebase project is configured the page runs on the server's local
 * identity instead. That is a real, isolated workspace on this machine; it is
 * not a shared one, and it is not pretending to be an account.
 *
 * Three things this file is careful about:
 *
 *   One initialisation. `initializeApp` is memoised behind a single promise.
 *   Calling it twice with the same name throws duplicate-app.
 *
 *   🔴 Signing in is a popup, everywhere, and never a redirect. This used to
 *   say the opposite — Firebase's guidance was that mobile prefers redirect —
 *   and following it was the defect. Mallow is served from a Cloud Run origin
 *   while `authDomain` is `<project>.firebaseapp.com`, and a browser blocking
 *   third-party storage (Safari 16.1+) cannot recover the redirect helper's
 *   state on the way back: Google authorises, the browser returns, and the gate
 *   is still there. Owner hit exactly that. So there is no redirect fallback in
 *   the front door; a path known to be broken is not a fallback, it is a slower
 *   failure that arrives after consent has already been given.
 *
 *   The press must not await anything. iOS Safari will not open a popup asked
 *   for after network I/O, so `prepare()` loads the SDK and builds the provider
 *   during boot, and the button stays disabled until `ready()`.
 *
 *   Anonymous mode has one exit. Google sign-in happens at the front door;
 *   this module no longer exposes an in-place anonymous-account linking path.
 */
"use strict";

window.Mallow = (function () {
  let cfg = null;
  let sdk = null;          // memoised { app, auth, mod }
  let user = null;

  /* 🔴 The synchronous handle on the loaded SDK, and the provider built once.
   *
   * iOS Safari will not open a popup that is asked for after the click handler
   * has awaited network I/O: the user activation is gone by then. The old
   * `signInWithGoogle()` began with `await firebase()`, which is a dynamic
   * import on the first press. So the SDK and the provider are prepared during
   * `boot()`, and the button stays disabled until they are here; the press
   * itself then calls `signInWithPopup` with nothing awaited in front of it.
   *
   * `prepare()` is what fills these. Nothing else may. */
  let sdkNow = null;             // { app, auth, mod } once resolved, else null
  let googleProvider = null;     // built once; a press must never construct one

  async function config() {
    if (!cfg) cfg = await (await fetch("/auth/config")).json();
    return cfg;
  }

  /* Exactly one Firebase app per page, however many times this is called. */
  function firebase() {
    if (sdk) return sdk;
    sdk = (async () => {
      const c = await config();
      if (!c.firebase || !c.firebase.projectId) {
        throw new Error("no Firebase project is configured");
      }
      const [appMod, mod] = await Promise.all([
        import("https://www.gstatic.com/firebasejs/10.12.2/firebase-app.js"),
        import("https://www.gstatic.com/firebasejs/10.12.2/firebase-auth.js"),
      ]);
      // getApps() first: a hot reload, or a second copy of this file, would
      // otherwise hit duplicate-app.
      const existing = appMod.getApps();
      const app = existing.length ? existing[0] : appMod.initializeApp({
        apiKey: c.firebase.apiKey,
        authDomain: c.firebase.authDomain,
        projectId: c.firebase.projectId,
      });
      const resolved = { app, auth: mod.getAuth(app), mod };
      sdkNow = resolved;                 // the synchronous handle a press needs
      return resolved;
    })().catch(err => { sdk = null; sdkNow = null; throw err; });
    return sdk;                                      // a failure must not stick
  }

  /*
   * Load the SDK and build the Google provider, so that a later press has
   * nothing to wait for. Called by `boot()`; safe to call again.
   *
   * 🔴 The provider cannot be built at module load: the SDK is a dynamic
   * import and `GoogleAuthProvider` does not exist until it lands. So "ready"
   * is a state the interface has to wait for, not an assumption — the button
   * stays disabled until `ready()` is true rather than awaiting on click.
   */
  async function prepare() {
    const { mod } = await firebase();
    if (!googleProvider) {
      const p = new mod.GoogleAuthProvider();
      // Identity only. Mallow does not request access to Google Drive.
      p.addScope("openid");
      p.addScope("email");
      p.addScope("profile");
      googleProvider = p;
    }
    return true;
  }

  /* Can a press open a popup right now without awaiting anything? */
  function ready() { return !!(sdkNow && googleProvider); }

  async function token() {
    if (!user) return null;
    try { return await user.getIdToken(); } catch (e) { return null; }
  }

  /* The only way this app talks to the server about anything private. */
  async function mallowFetch(url, opts = {}) {
    const t = await token();
    const headers = new Headers(opts.headers || {});
    if (t) headers.set("Authorization", "Bearer " + t);
    return fetch(url, { ...opts, headers, credentials: "same-origin" });
  }

  /*
   * 🔴 Signing in is a popup, on every browser, and never a redirect.
   *
   * The redirect flow was the default on mobile because Firebase used to
   * recommend it there. It does not work here: Mallow is served from a Cloud
   * Run origin while `authDomain` is `<project>.firebaseapp.com`, and a browser
   * that blocks third-party storage — Safari 16.1+ among them — cannot recover
   * the redirect helper's state on the way back. Owner saw exactly that: Google
   * authorised, the browser returned, and Mallow was still showing the gate.
   *
   * So there is no redirect fallback here. Falling back to a path known to be
   * broken is not a fallback; it is a slower way to fail, and it fails after
   * the person has already given Google their consent.
   *
   * 🔴 Neither of these functions awaits anything before the popup call. The
   * SDK and the provider were prepared in `boot()`, and the button that calls
   * this is disabled until `ready()`. That is what keeps the user activation
   * alive on iOS Safari.
   */
  function signInWithGoogle() {
    if (!ready()) return Promise.reject(new Error("sign-in is not ready yet"));
    return sdkNow.mod.signInWithPopup(sdkNow.auth, googleProvider)
      .then(res => { user = (res && res.user) || user; return user; });
  }

  async function browseAnonymously() {
    const { auth, mod } = await firebase();
    const res = await mod.signInAnonymously(auth);
    user = res.user;
    return user;
  }

  /*
   * Hand the verified token to the server once, and get back a cookie that a
   * plain navigation can carry.
   *
   * A bearer token only exists inside `fetch`. Clicking "records" or downloading
   * a PDF are navigations — the browser sends no header — so without this the
   * whole private half of the product answered 401 to somebody who was signed in.
   *
   * The cookie is minted by the server after it verified the token, and it is
   * accepted for GET only. Writes still carry the token.
   */
  async function startSession() {
    const t = await token();
    if (!t) return false;
    try {
      const r = await fetch("/auth/session", {
        method: "POST", credentials: "same-origin",
        headers: { Authorization: "Bearer " + t },
      });
      return r.ok;
    } catch (e) { return false; }
  }

  async function signOut() {
    const { auth, mod } = await firebase();
    await mod.signOut(auth);
    user = null;
    // The cookie outlives the SDK's own state, so it has to be dropped too —
    // otherwise "sign out" would leave the records still reachable by URL.
    let sessionCleared = false;
    try {
      const r = await fetch("/auth/session/clear",
                            { method: "POST", credentials: "same-origin" });
      sessionCleared = r.ok;
    } catch (e) { sessionCleared = false; }
    if (!sessionCleared) throw new Error("Mallow could not clear the navigation session");
    return true;
  }

  function describe(u) {
    return {
      mode: "firebase",
      signedIn: !!u,
      temporary: !u || u.isAnonymous,
      anonymous: !!u && u.isAnonymous,
      email: (u && u.email) || null,
    };
  }

  /* Restore an existing session before the page decides what to show. */
  async function boot() {
    const c = await config();
    if (!c.firebase || !c.firebase.projectId) {
      return { mode: "local", signedIn: true, temporary: true,
               anonymous: true, email: null };
    }
    const { auth, mod } = await firebase();
    // 🔴 Before anything else that can take time: the gate's button is enabled
    // off the back of this, and a press must never have to wait for an import.
    await prepare();
    return new Promise(resolve => {
      // The listener can fire synchronously, before the unsubscribe handle
      // exists — a browser test caught exactly that. Resolve once, and detach
      // whenever the handle turns up.
      let stop = null, done = false;
      const finish = u => {
        if (done) return;
        done = true;
        user = u;
        if (stop) stop();
        resolve(describe(u));
      };
      stop = mod.onAuthStateChanged(auth, finish);
      if (done && stop) stop();
    }).then(async state => {
      // Refresh the navigation cookie on every boot. ID tokens last an hour;
      // doing it here means the cookie is never the stale half of the pair.
      if (!state.signedIn) return {...state, sessionReady: false};
      const sessionReady = await startSession();
      return {...state, sessionReady};
    });
  }

  return { config, boot, firebase, mallowFetch, signInWithGoogle,
           prepare, ready, browseAnonymously, signOut, describe,
           startSession };
})();
