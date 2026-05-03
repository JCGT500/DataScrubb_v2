"""MSAL device code flow for SharePoint / Microsoft Graph delegated auth.

The user signs in once via a browser code at https://microsoft.com/devicelogin.
The refresh token is cached to ``~/.datascrubb/msal_cache.bin`` and re-used
for subsequent runs (~90-day refresh window). Subsequent ``acquire_token``
calls hit the cache silently — only the first sign-in needs interaction.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import msal

logger = logging.getLogger("datascrubb.sharepoint.auth")

GRAPH_AUTHORITY = "https://login.microsoftonline.com/{tenant_id}"
DEFAULT_SCOPES = [
    "Files.ReadWrite.All",
    "Sites.ReadWrite.All",
    "User.Read",
]


class SharepointAuthError(RuntimeError):
    """Raised when token acquisition fails (network / consent / bad config)."""


def _cache_path() -> Path:
    p = Path.home() / ".datascrubb" / "msal_cache.bin"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load_cache() -> msal.SerializableTokenCache:
    cache = msal.SerializableTokenCache()
    cp = _cache_path()
    if cp.exists():
        try:
            cache.deserialize(cp.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("Failed to load token cache (%s); starting fresh.", e)
    return cache


def _save_cache(cache: msal.SerializableTokenCache) -> None:
    if cache.has_state_changed:
        _cache_path().write_text(cache.serialize(), encoding="utf-8")


def _build_app(tenant_id: str, client_id: str) -> tuple[msal.PublicClientApplication, msal.SerializableTokenCache]:
    if not tenant_id or not client_id:
        raise SharepointAuthError(
            "SharePoint not configured — tenant_id and client_id are required. "
            "Configure them in Admin → SharePoint."
        )
    cache = _load_cache()
    app = msal.PublicClientApplication(
        client_id=client_id,
        authority=GRAPH_AUTHORITY.format(tenant_id=tenant_id),
        token_cache=cache,
    )
    return app, cache


def signed_in_account(tenant_id: str, client_id: str) -> dict[str, Any] | None:
    """Return the cached account dict, or None if not signed in."""
    if not tenant_id or not client_id:
        return None
    app, _ = _build_app(tenant_id, client_id)
    accounts = app.get_accounts()
    return accounts[0] if accounts else None


def acquire_token(tenant_id: str, client_id: str, scopes: list[str] | None = None) -> str:
    """Get an access token silently (from cache + refresh).

    Raises ``SharepointAuthError`` if no cached account exists — caller should
    then run ``initiate_device_flow`` to start the interactive sign-in.
    """
    scopes = scopes or DEFAULT_SCOPES
    app, cache = _build_app(tenant_id, client_id)
    accounts = app.get_accounts()
    if not accounts:
        raise SharepointAuthError(
            "Not signed in. Click 'Sign in' on the SharePoint admin tab to start the device-code flow."
        )
    result = app.acquire_token_silent(scopes=scopes, account=accounts[0])
    _save_cache(cache)
    if not result or "access_token" not in result:
        raise SharepointAuthError(
            f"Token refresh failed: {result.get('error_description', 'unknown') if result else 'no result'}. "
            "Sign in again."
        )
    return result["access_token"]


def initiate_device_flow(
    tenant_id: str,
    client_id: str,
    scopes: list[str] | None = None,
) -> dict[str, Any]:
    """Begin device-code sign-in. Returns the flow dict containing
    ``user_code``, ``verification_uri``, and ``message`` to display.

    Caller must then call ``complete_device_flow(flow)`` after the user
    enters the code in their browser.
    """
    scopes = scopes or DEFAULT_SCOPES
    app, cache = _build_app(tenant_id, client_id)
    flow = app.initiate_device_flow(scopes=scopes)
    if "user_code" not in flow:
        raise SharepointAuthError(
            f"Failed to start device flow: {flow.get('error_description', flow)}"
        )
    # Stash app + cache so complete_device_flow can re-use them
    flow["_tenant_id"] = tenant_id
    flow["_client_id"] = client_id
    flow["_scopes"] = scopes
    return flow


def complete_device_flow(flow: dict[str, Any]) -> dict[str, Any]:
    """Block until the user completes the device-code sign-in (or it expires).

    Returns the token result dict (with ``access_token``). Persists the cache
    so future ``acquire_token`` calls succeed silently.
    """
    tenant_id = flow.pop("_tenant_id", None)
    client_id = flow.pop("_client_id", None)
    flow.pop("_scopes", None)
    if not tenant_id or not client_id:
        raise SharepointAuthError("Device flow is missing tenant/client metadata.")
    app, cache = _build_app(tenant_id, client_id)
    result = app.acquire_token_by_device_flow(flow)
    _save_cache(cache)
    if "access_token" not in result:
        raise SharepointAuthError(
            f"Sign-in failed or timed out: {result.get('error_description', result)}"
        )
    return result


def sign_out(tenant_id: str, client_id: str) -> None:
    """Remove the cached account (forces a fresh device flow next time)."""
    if not tenant_id or not client_id:
        return
    app, cache = _build_app(tenant_id, client_id)
    for acct in app.get_accounts():
        app.remove_account(acct)
    _save_cache(cache)
