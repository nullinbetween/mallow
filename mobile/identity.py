"""
Who is asking.

Every request that touches records resolves to exactly one `uid`, and that uid
comes from a verified Firebase ID token — never from anything the browser sends
as data. A client that hands us `?uid=someone-else` is ignored, because the only
uid this module will return is the one inside a signature it checked.

Three modes, and the difference between them is stated on screen rather than
hidden:

  firebase   A Firebase ID token in `Authorization: Bearer …`, verified against
             Google's public keys for the configured project. Both the Google
             sign-in path and the anonymous path arrive here; they differ only
             in the token's `provider_id` / `firebase.sign_in_provider` claim.
  local      No Firebase project configured. The server mints its own signed
             cookie so the product can be run and tested on a laptop. The uid is
             real and isolated, but it lives in this browser only. The page says
             so.
  closed     A Firebase project is configured, or `REQUIRE_FIREBASE_AUTH=1`.
             Local identity is refused outright.

🔴 **A configured project closes the local door by itself.** Earlier this was
true only when `REQUIRE_FIREBASE_AUTH=1` was also set, which meant a deployment
that had Firebase wired but had forgotten that one variable would hand every
signed-out visitor a local cookie workspace — working, isolated, and quietly
not the account they thought they had. Two variables had to be right for the
product to be correct, and only one of them looked like a security setting.
Now the project's presence is the switch, and `REQUIRE_FIREBASE_AUTH` remains
as the belt to that pair of braces.

The local mode is not a shortcut past authentication. It is the same contract —
one verified uid per request — with a different signer, and it is gone the
moment anything says this is a real deployment.
"""
from __future__ import annotations

import logging
import os
import secrets
from dataclasses import dataclass
from typing import Optional

from flask import g, request
from itsdangerous import (BadSignature, SignatureExpired, URLSafeSerializer,
                          URLSafeTimedSerializer)

FIREBASE_PROJECT = os.getenv("FIREBASE_PROJECT_ID", "").strip()
REQUIRE_FIREBASE_AUTH = os.getenv("REQUIRE_FIREBASE_AUTH") == "1"
COOKIE = "mallow_local_uid"
SESSION_COOKIE = "mallow_session"
SESSION_MAX_AGE = 60 * 60 * 24          # one demo day; re-issued on page boot

# The two sign-in providers this product has. Anything else — email/password,
# GitHub, a phone number, a custom token — is refused rather than guessed at.
# 🔴 This used to be `"anonymous" if p == "anonymous" else "google"`, which
# labelled every unknown provider as Google. A provider nobody designed for
# must not inherit the permissions or interface of a Google identity.
PROVIDERS = {"google.com": "google", "anonymous": "anonymous"}

# A local secret, generated per process unless one is supplied. A restart
# therefore issues new local workspaces, which is the honest behaviour for a
# dev identity that was never meant to outlive the machine.
_SECRET = os.getenv("MALLOW_LOCAL_SECRET") or secrets.token_urlsafe(32)
_signer = URLSafeSerializer(_SECRET, salt="mallow-local-identity")

# 🔴 The session secret is NOT per-process. Cloud Run runs several instances;
# a cookie minted by one and rejected by the next would log people out at
# random and look like a bug in the product. So in a real deployment it must be
# supplied, and `preflight()` refuses to start without it.
SESSION_SECRET = os.getenv("MALLOW_SESSION_SECRET", "").strip() or _SECRET
_session_signer = URLSafeTimedSerializer(SESSION_SECRET, salt="mallow-session")


class Unauthenticated(RuntimeError):
    """No verifiable identity, and no local identity is permitted."""


@dataclass(frozen=True)
class Identity:
    uid: str
    provider: str          # "google" | "anonymous" | "local"
    mode: str              # "firebase" | "local"

    @property
    def is_anonymous(self) -> bool:
        """True when the workspace is disposable and the page must say so."""
        return self.provider in ("anonymous", "local")

    @property
    def is_local(self) -> bool:
        return self.mode == "local"


# Cloud Run sets K_SERVICE. Either that or a configured Firebase project means
# this is a deployment rather than a laptop.
DEPLOYED = bool(os.getenv("K_SERVICE", "").strip()) or bool(FIREBASE_PROJECT)


def configured() -> bool:
    return bool(FIREBASE_PROJECT)


def secure_cookies() -> bool:
    """
    🔴 Whether cookies carry `Secure`, and it is not read off the request.

    `request.is_secure` is what the app can see, and behind Cloud Run's proxy
    it reflects the *internal* hop unless the forwarded scheme is trusted. A
    cookie that authorises reading somebody's private records must not have its
    transport protection decided by a header that may or may not have survived
    a reverse proxy. Deployed means Secure, full stop; a laptop on plain HTTP is
    the only case that gets False, and only because otherwise it could not be
    used at all.
    """
    return True if DEPLOYED else bool(request.is_secure)


def local_allowed() -> bool:
    """
    Whether this process may mint its own identities.

    Only on a machine with no Firebase project and no explicit demand for one.
    Everywhere else a request without a verified token has no identity, and
    saying so is the whole feature.
    """
    return not configured() and not REQUIRE_FIREBASE_AUTH


def preflight() -> None:
    """
    Refuse to start in a configuration that cannot work.

    `REQUIRE_FIREBASE_AUTH=1` with no project is a locked door with no key cut:
    every request would 401 forever and the sign-in button would have nothing
    to sign in to. Better to fail at startup, loudly, than to serve that.
    """
    if REQUIRE_FIREBASE_AUTH and not configured():
        raise RuntimeError(
            "REQUIRE_FIREBASE_AUTH=1 but FIREBASE_PROJECT_ID is unset: "
            "nobody could ever sign in. Set the project, or unset the flag.")
    if configured() and not os.getenv("MALLOW_SESSION_SECRET", "").strip():
        raise RuntimeError(
            "FIREBASE_PROJECT_ID is set but MALLOW_SESSION_SECRET is not. "
            "Navigation sessions are signed with it, and a per-process random "
            "secret would sign people out whenever Cloud Run answered from a "
            "different instance.")


# ------------------------------------------------------------- the session --
# 🔴 Why this exists at all.
#
# A Firebase ID token can only be attached by JavaScript, to a `fetch`. It
# cannot be attached to a plain navigation: clicking a link, or following a
# redirect back from Google. So with token-only auth, a signed-in person who
# tapped "records" or "download PDF" got a 401 — the entire private half of the
# product was unreachable in the one configuration that matters. It passed the
# tests because the tests ran in local mode, where a cookie already existed.
#
# The fix is a cookie the *server* mints, after it has verified a real token.
# The uid inside it went through `_verify` once; the cookie only carries it.
#
# It authorises GET and HEAD, and nothing else. Every write still needs the
# bearer token, which the page always has and a cross-site form never will —
# so this adds a way to read your own records, not a way for another site to
# post as you.
def issue_session(who: Identity) -> str:
    return _session_signer.dumps({"uid": who.uid, "provider": who.provider})


def _from_session_cookie() -> Optional[Identity]:
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        return None
    try:
        data = _session_signer.loads(raw, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    uid, provider = data.get("uid"), data.get("provider")
    if not uid or provider not in PROVIDERS.values():
        return None
    return Identity(uid=str(uid), provider=provider, mode="firebase")


# ------------------------------------------------------------------ firebase --
def _verify(token: str) -> Identity:
    """
    🔴 Every failure in here is 401, and none of them is 500.

    `verify_firebase_token` raises rather than returning falsy for most of the
    ways a token is bad: expired, malformed, wrong signature, wrong audience —
    and also for things that are not the caller's fault at all, like being
    unable to fetch Google's public keys. Left uncaught, all of those became a
    server error, so an expired token — the single most ordinary thing that
    happens to a token — read as "Mallow is broken" instead of "sign in again".

    The exception type is logged. The token is not, ever: it is a live
    credential and this is one line away from a log aggregator.
    """
    from google.auth.transport import requests as grequests
    from google.oauth2 import id_token as google_id_token

    try:
        claims = google_id_token.verify_firebase_token(
            token, grequests.Request(), audience=FIREBASE_PROJECT)
    except Exception as e:                                        # noqa: BLE001
        logging.getLogger(__name__).info(
            "token rejected: %s", type(e).__name__)
        raise Unauthenticated(f"token did not verify ({type(e).__name__})") from e
    if not claims:
        raise Unauthenticated("token did not verify")

    uid = claims.get("user_id") or claims.get("sub")
    if not uid:
        raise Unauthenticated("token carries no subject")

    raw = (claims.get("firebase", {}) or {}).get("sign_in_provider", "")
    provider = PROVIDERS.get(raw)
    if provider is None:
        raise Unauthenticated(f"sign-in provider {raw!r} is not one this app accepts")
    return Identity(uid=str(uid), provider=provider, mode="firebase")


# --------------------------------------------------------------------- local --
def _local_from_cookie() -> Optional[Identity]:
    raw = request.cookies.get(COOKIE)
    if not raw:
        return None
    try:
        uid = _signer.loads(raw)
    except BadSignature:
        return None                     # forged or from another process: ignored
    return Identity(uid=str(uid), provider="local", mode="local")


# A fixed local workspace, for a demonstration that needs to open on records
# that already exist. Local mode only — a Firebase uid comes out of a token and
# this cannot reach it — and it is read once, here, so there is exactly one
# place to look for why a laptop keeps landing in the same workspace.
PINNED_LOCAL_UID = os.getenv("MALLOW_LOCAL_UID", "").strip()


def new_local_uid() -> str:
    return PINNED_LOCAL_UID or ("local_" + secrets.token_hex(12))


def sign_local(uid: str) -> str:
    return _signer.dumps(uid)


# --------------------------------------------------------------------- entry --
def resolve() -> Identity:
    """
    The only way anything in this app learns who is asking.

    Order matters: a real token always wins, so a stale local cookie cannot
    shadow a signed-in user.
    """
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        if not configured():
            raise Unauthenticated("a token was presented but no project is configured")
        return _verify(header[7:].strip())

    # A navigation — a clicked link, a download, a redirect coming back from
    # Google — carries no token and can carry no token. Reads may lean on the
    # session cookie; writes may not, which is what keeps this from being a
    # CSRF hole.
    if configured() and request.method in ("GET", "HEAD"):
        found = _from_session_cookie()
        if found:
            return found

    if not local_allowed():
        raise Unauthenticated(
            "this deployment requires a Firebase ID token"
            if REQUIRE_FIREBASE_AUTH else
            "a Firebase project is configured; sign in rather than "
            "falling back to a local workspace")

    found = _local_from_cookie()
    if found:
        return found
    return Identity(uid=new_local_uid(), provider="local", mode="local")


def current() -> Identity:
    """
    Resolved once per request and cached on the request context.

    Raises `Unauthenticated` when there is no verifiable identity, which is how
    every route that touches somebody's records answers a stranger.
    """
    if not hasattr(g, "_identity"):
        g._identity = resolve()
    return g._identity


def optional() -> Optional[Identity]:
    """
    The same question, for the pages a person must be able to reach *before*
    they have signed in.

    Without this, a deployment with REQUIRE_FIREBASE_AUTH=1 would 401 the front
    door and the sign-in config, and nobody could ever get in — a lock with the
    key inside. Only the public bootstrap uses it: the meadow shell, the auth
    config, and the static files. Everything that reads or writes a record goes
    through `current()` and gets 401 instead.
    """
    try:
        return current()
    except Unauthenticated:
        return None
