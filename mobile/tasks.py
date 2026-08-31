"""
The door the scheduler comes through, and nobody else.

Cloud Scheduler calls the scheduled-reflection endpoint with an OIDC token
minted for its own service account. This module is the only thing that decides
whether a request is that call.

🔴 It does not consult `identity`. That is deliberate and it is the whole point:
a person's Firebase ID token is a perfectly valid credential for reading their
own records and must never be a valid credential for running a job across every
workspace in the deployment. Two doors, two keys, no shared hallway — so there
is no sequence of user actions that reaches this one.

Three ways a request is judged, in order:

  1. `Authorization: Bearer <Google OIDC id token>` whose `email` claim is
     exactly `TASKS_SERVICE_ACCOUNT` and whose audience is `TASKS_AUDIENCE`.
     This is production.
  2. `X-Mallow-Task-Key` equal to `MALLOW_TASK_KEY`, and only while no service
     account is configured. This is a laptop and a test suite.
  3. Neither configured → 503. The endpoint is off rather than open.

The fallback in (2) cannot widen (1): the moment `TASKS_SERVICE_ACCOUNT` is set,
the shared key stops being accepted at all, so a deployment cannot end up with
a second way in that somebody forgot about.
"""
from __future__ import annotations

import hmac
import os
from typing import Optional

from flask import request

TASKS_SERVICE_ACCOUNT = os.getenv("TASKS_SERVICE_ACCOUNT", "").strip()
TASKS_AUDIENCE = os.getenv("TASKS_AUDIENCE", "").strip()
TASK_KEY = os.getenv("MALLOW_TASK_KEY", "").strip()


class NotTheScheduler(PermissionError):
    """The caller is not the scheduler. Says nothing about who they are."""


class TaskEndpointOff(RuntimeError):
    """Neither credential is configured, so this endpoint refuses to run."""


def configured() -> bool:
    return bool(TASKS_SERVICE_ACCOUNT or TASK_KEY)


def preflight() -> None:
    """
    🔴 A service account without an audience is a half-checked token.

    `verify_oauth2_token(audience=None)` skips the audience claim entirely, so
    an OIDC token minted for some *other* service — by the same service
    account, for a different Cloud Run app — would sail through. Naming the
    caller without naming what the token was minted for is not a smaller
    check, it is a different and weaker one, so it is refused at startup
    instead of silently accepted.
    """
    if TASKS_SERVICE_ACCOUNT and not TASKS_AUDIENCE:
        raise RuntimeError(
            "TASKS_SERVICE_ACCOUNT is set but TASKS_AUDIENCE is not. The "
            "audience is what ties the token to this service; without it any "
            "OIDC token from that account would be accepted.")


def _verify_oidc(token: str) -> str:
    from google.auth.transport import requests as grequests
    from google.oauth2 import id_token as google_id_token

    try:
        claims = google_id_token.verify_oauth2_token(
            token, grequests.Request(), audience=TASKS_AUDIENCE)
    except Exception as e:                                        # noqa: BLE001
        raise NotTheScheduler(f"token did not verify: {type(e).__name__}") from e

    email = (claims or {}).get("email", "")
    if not claims.get("email_verified"):
        raise NotTheScheduler("token carries an unverified email")
    if email != TASKS_SERVICE_ACCOUNT:
        # A real Firebase user token lands here too: it is a Google-signed
        # token, and it is not this service account.
        raise NotTheScheduler("token is not the scheduler's service account")
    return email


def caller() -> str:
    """
    The verified scheduler identity, or an exception. There is no third answer.
    """
    if not configured():
        raise TaskEndpointOff(
            "set TASKS_SERVICE_ACCOUNT (production) or MALLOW_TASK_KEY (local)")

    if TASKS_SERVICE_ACCOUNT:
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            raise NotTheScheduler("no OIDC token")
        return _verify_oidc(header[7:].strip())

    supplied = request.headers.get("X-Mallow-Task-Key", "")
    if not supplied or not hmac.compare_digest(supplied, TASK_KEY):
        raise NotTheScheduler("task key did not match")
    return "local-task-key"


def audience_hint() -> Optional[str]:
    return TASKS_AUDIENCE or None
