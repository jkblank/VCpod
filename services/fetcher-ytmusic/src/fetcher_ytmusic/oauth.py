"""ytmusicapi's OAuth device-code flow (RFC 8628), broken into two calls
so a caller (the web GUI backend) can poll from an HTTP endpoint instead
of ytmusicapi's own blocking `input()` prompt
(RefreshingToken.prompt_for_token) -- see notes.md's ytmusic-oauth entry.

Google's own OAuth client credentials aren't shared/bundled by
ytmusicapi -- every user has to create their own via Google Cloud
Console (a "TVs and Limited Input devices" OAuth client). client_id/
client_secret are supplied by the caller, not hardcoded here."""

from __future__ import annotations

import time
from dataclasses import dataclass

from ytmusicapi import OAuthCredentials
from ytmusicapi.auth.oauth.exceptions import BadOAuthClient, UnauthorizedOAuthClient
from ytmusicapi.exceptions import YTMusicServerError


class OAuthFlowError(Exception):
    """Raised for a client/token failure that will never resolve by
    polling again -- bad client_id/secret, denied consent, or an
    expired device code. Not raised for "still waiting" (see
    OAuthPending below)."""


class OAuthPending(Exception):
    """Raised by poll_device_flow while the user hasn't finished the
    browser step yet -- not an error, the caller should just poll
    again after `interval` seconds (RFC 8628 authorization_pending/
    slow_down)."""


@dataclass
class DeviceCodeStart:
    device_code: str
    user_code: str
    verification_url: str
    expires_in: int
    interval: int


def start_device_flow(client_id: str, client_secret: str) -> DeviceCodeStart:
    try:
        code = OAuthCredentials(client_id=client_id, client_secret=client_secret).get_code()
    except (BadOAuthClient, UnauthorizedOAuthClient, YTMusicServerError) as e:
        raise OAuthFlowError(str(e)) from e
    return DeviceCodeStart(
        device_code=code["device_code"],
        user_code=code["user_code"],
        verification_url=code["verification_url"],
        expires_in=code["expires_in"],
        interval=code["interval"],
    )


def poll_device_flow(client_id: str, client_secret: str, device_code: str) -> dict:
    """Returns the exact dict shape written to oauth.json (matching
    ytmusicapi's own RefreshingToken.as_dict()/store_token() -- see
    RefreshingToken.prompt_for_token in ytmusicapi/auth/oauth/token.py,
    the CLI equivalent of this) on success.

    Raises OAuthPending while still waiting on the user, OAuthFlowError
    for anything that will never succeed by polling again."""
    try:
        raw = OAuthCredentials(client_id=client_id, client_secret=client_secret).token_from_code(
            device_code
        )
    except (BadOAuthClient, UnauthorizedOAuthClient, YTMusicServerError) as e:
        raise OAuthFlowError(str(e)) from e

    error = raw.get("error") if isinstance(raw, dict) else None
    if error in ("authorization_pending", "slow_down"):
        raise OAuthPending(error)
    if error:
        raise OAuthFlowError(error)

    now = int(time.time())
    return {
        "scope": raw["scope"],
        "token_type": raw["token_type"],
        "access_token": raw["access_token"],
        "refresh_token": raw["refresh_token"],
        "expires_at": now + raw["expires_in"],
        # ytmusicapi's own RefreshingToken.prompt_for_token stores
        # refresh_token_expires_in here, not expires_in -- this field is
        # the *refresh* token's lifetime, not the access token's (the
        # access token's real freshness is tracked via expires_at
        # above and refreshed automatically on next use).
        "expires_in": raw.get("refresh_token_expires_in", raw["expires_in"]),
    }
