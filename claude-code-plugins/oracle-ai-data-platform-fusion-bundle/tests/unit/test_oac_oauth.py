"""Unit tests for IdcsTokenFetcher."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from oracle_ai_data_platform_fusion_bundle.oac.rest.oauth import (
    IdcsTokenFetcher,
    TokenBundle,
)


def _ok_response(token: str = "abc.def.ghi", expires_in: int = 3600) -> MagicMock:
    r = MagicMock(status_code=200)
    r.json.return_value = {"access_token": token, "token_type": "Bearer", "expires_in": expires_in}
    return r


class TestIdcsTokenFetcher:
    def test_posts_to_oauth2_v1_token_with_basic_auth(self) -> None:
        s = MagicMock()
        s.post.return_value = _ok_response("xyz.token", 3600)
        fetcher = IdcsTokenFetcher(
            "https://idcs-abc.identity.oraclecloud.com",
            client_id="cid",
            client_secret="csec",
            session=s,
        )
        token = fetcher.get_token()
        assert token == "xyz.token"

        call = s.post.call_args
        url = call.args[0] if call.args else call.kwargs["url"]
        assert url == "https://idcs-abc.identity.oraclecloud.com/oauth2/v1/token"
        assert call.kwargs["data"] == {
            "grant_type": "client_credentials",
            "scope": "urn:opc:resource:fawcommon:OAC",
        }
        assert call.kwargs["auth"] == ("cid", "csec")

    def test_caches_token_until_expiry(self) -> None:
        s = MagicMock()
        s.post.return_value = _ok_response("first.token", 3600)
        fetcher = IdcsTokenFetcher("https://idcs-x.identity.oraclecloud.com", "id", "sec", session=s)
        assert fetcher.get_token() == "first.token"
        # Change the mock — since cached, second call should not refetch
        s.post.return_value = _ok_response("second.token", 3600)
        assert fetcher.get_token() == "first.token"
        assert s.post.call_count == 1

    def test_force_refresh_bypasses_cache(self) -> None:
        s = MagicMock()
        s.post.side_effect = [_ok_response("v1", 3600), _ok_response("v2", 3600)]
        fetcher = IdcsTokenFetcher("https://idcs-x.identity.oraclecloud.com", "id", "sec", session=s)
        assert fetcher.get_token() == "v1"
        assert fetcher.get_token(force_refresh=True) == "v2"
        assert s.post.call_count == 2

    def test_raises_on_non_200(self) -> None:
        s = MagicMock()
        s.post.return_value = MagicMock(
            status_code=400, text='{"error":"invalid_grant"}'
        )
        fetcher = IdcsTokenFetcher("https://idcs-x.identity.oraclecloud.com", "id", "sec", session=s)
        with pytest.raises(RuntimeError, match="invalid_grant"):
            fetcher.get_token()

    def test_raises_when_no_access_token_in_body(self) -> None:
        s = MagicMock()
        r = MagicMock(status_code=200)
        r.json.return_value = {"token_type": "Bearer"}
        s.post.return_value = r
        fetcher = IdcsTokenFetcher("https://idcs-x.identity.oraclecloud.com", "id", "sec", session=s)
        with pytest.raises(RuntimeError, match="missing access_token"):
            fetcher.get_token()

    def test_requires_url_scheme(self) -> None:
        with pytest.raises(ValueError, match="must include scheme"):
            IdcsTokenFetcher("idcs-x.identity.oraclecloud.com", "id", "sec")


class TestTokenBundle:
    def test_is_valid_when_far_from_expiry(self) -> None:
        b = TokenBundle("t", expires_at=time.time() + 3600)
        assert b.is_valid() is True

    def test_is_invalid_when_within_leeway(self) -> None:
        b = TokenBundle("t", expires_at=time.time() + 5)
        assert b.is_valid(leeway_seconds=30) is False

    def test_is_invalid_after_expiry(self) -> None:
        b = TokenBundle("t", expires_at=time.time() - 1)
        assert b.is_valid() is False
