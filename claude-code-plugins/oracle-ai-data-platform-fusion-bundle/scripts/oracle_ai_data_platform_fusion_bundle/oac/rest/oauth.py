"""IDCS OAuth client-credentials Bearer token fetcher.

OAC's public REST API at ``/api/20210901/...`` only accepts OAuth 2.0
Bearer tokens. Session cookies that drive OAC's web UI work for
``/ui/sac/api/v1/...`` and ``/ui/dv/ui/api/v1/...`` (UI-internal APIs)
but not for the documented public endpoints.

This module fetches a Bearer via the IDCS confidential-application
client-credentials grant. The IDCS app must be registered ahead of
time — see ``docs/oac_rest_api_setup.md`` for the one-time admin steps.

Verified live 2026-04-30 against ``https://oacai.cealinfra.com``:
    GET /api/20210901/catalog/connections
        Authorization: Bearer <missing>
        -> HTTP 401, www-authenticate: Bearer error="invalid_session"
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import requests


@dataclass
class TokenBundle:
    """A Bearer token plus the absolute time it expires."""

    access_token: str
    expires_at: float

    def is_valid(self, leeway_seconds: int = 30) -> bool:
        return time.time() + leeway_seconds < self.expires_at


class IdcsTokenFetcher:
    """Fetches and caches IDCS Bearer tokens via client-credentials grant.

    Args:
        idcs_url: IDCS stripe URL, e.g.
            ``https://idcs-<stripe>.identity.oraclecloud.com``. Trailing slash optional.
        client_id: Confidential-application Client ID from IDCS admin console.
        client_secret: Confidential-application Client Secret. Should be loaded from
            OCI Vault — never put in ``bundle.yaml`` directly.
        scope: OAuth scope. Default matches OAC docs (``urn:opc:resource:fawcommon:OAC``);
            override if your IDCS admin published a different scope for the OAC API.

    Usage:
        >>> fetcher = IdcsTokenFetcher("https://idcs-abc123.identity.oraclecloud.com",
        ...                            client_id="...", client_secret="...")
        >>> token = fetcher.get_token()
        >>> requests.get(url, headers={"Authorization": f"Bearer {token}"})
    """

    def __init__(
        self,
        idcs_url: str,
        client_id: str,
        client_secret: str,
        *,
        scope: str = "urn:opc:resource:fawcommon:OAC",
        timeout: int = 30,
        session: requests.Session | None = None,
    ) -> None:
        if not idcs_url.startswith(("http://", "https://")):
            raise ValueError(f"idcs_url must include scheme: got {idcs_url!r}")
        self._token_endpoint = idcs_url.rstrip("/") + "/oauth2/v1/token"
        self._client_id = client_id
        self._client_secret = client_secret
        self._scope = scope
        self._timeout = timeout
        self._session = session or requests.Session()
        self._cached: TokenBundle | None = None

    def get_token(self, *, force_refresh: bool = False) -> str:
        """Return a valid Bearer access token, fetching a new one if needed."""
        if not force_refresh and self._cached is not None and self._cached.is_valid():
            return self._cached.access_token
        bundle = self._fetch()
        self._cached = bundle
        return bundle.access_token

    def _fetch(self) -> TokenBundle:
        """One-shot client-credentials POST to ``/oauth2/v1/token``."""
        response = self._session.post(
            self._token_endpoint,
            data={
                "grant_type": "client_credentials",
                "scope": self._scope,
            },
            auth=(self._client_id, self._client_secret),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=self._timeout,
        )
        if response.status_code != 200:
            # Fail loudly with the IDCS error body so the user sees `invalid_grant`,
            # `unauthorized_client`, etc. and can fix the IDCS app config.
            raise RuntimeError(
                f"IDCS token fetch failed: HTTP {response.status_code} from "
                f"{self._token_endpoint}: {response.text}"
            )
        body = response.json()
        token = body.get("access_token")
        if not isinstance(token, str) or not token:
            raise RuntimeError(f"IDCS response missing access_token: keys={sorted(body.keys())}")
        expires_in = int(body.get("expires_in", 3600))
        return TokenBundle(access_token=token, expires_at=time.time() + expires_in)


def derive_idcs_url(oac_url: str) -> str | None:
    """Extract the IDCS stripe URL from an OAC URL where it can be inferred.

    Some OAC tenancies use the convention ``https://<oac-host>`` where the
    IDCS stripe is discoverable via the OAC home page redirect. This helper
    is a best-effort, NOT authoritative — for production setups, pass the
    IDCS URL explicitly via ``--idcs-url`` or ``bundle.yaml``.

    Returns ``None`` if no obvious mapping exists.
    """
    # The OAC URL alone doesn't carry the IDCS stripe; require explicit configuration.
    # Keep this as a placeholder for future auto-discovery (e.g. follow the redirect
    # chain on the OAC home page and parse the IDCS host out of the Location header).
    parsed = urlparse(oac_url)
    if not parsed.netloc:
        return None
    return None


__all__ = ["IdcsTokenFetcher", "TokenBundle", "derive_idcs_url"]
