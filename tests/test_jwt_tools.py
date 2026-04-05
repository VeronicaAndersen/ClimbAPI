"""Unit tests for security/jwt_tools.py — token creation and decoding."""
import pytest
import jwt as pyjwt

from security.jwt_tools import create_access_token, create_refresh_token, decode_token
from schema.setting import settings


class TestCreateAccessToken:
    def test_returns_string(self):
        token = create_access_token("user123")
        assert isinstance(token, str)

    def test_sub_is_string_when_int_passed(self):
        token = create_access_token(42)
        payload = decode_token(token)
        assert payload["sub"] == "42"

    def test_sub_is_preserved_as_string(self):
        token = create_access_token("alice")
        payload = decode_token(token)
        assert payload["sub"] == "alice"

    def test_type_claim_is_access(self):
        token = create_access_token("u")
        payload = decode_token(token)
        assert payload["type"] == "access"

    def test_exp_greater_than_iat(self):
        token = create_access_token("u")
        payload = decode_token(token)
        assert payload["exp"] > payload["iat"]

    def test_issuer_is_set(self):
        token = create_access_token("u")
        payload = decode_token(token)
        assert payload["iss"] == settings.ISSUER

    def test_nbf_is_set(self):
        token = create_access_token("u")
        payload = decode_token(token)
        assert "nbf" in payload

    def test_extra_claims_included(self):
        token = create_access_token("u", extra={"role": "admin", "level": 3})
        payload = decode_token(token)
        assert payload["role"] == "admin"
        assert payload["level"] == 3

    def test_reserved_claims_in_extra_are_ignored(self):
        """Extra dict must not override reserved claims like sub or exp."""
        token = create_access_token("real-user", extra={"sub": "evil", "exp": 0})
        payload = decode_token(token)
        assert payload["sub"] == "real-user"
        assert payload["exp"] > 0

    def test_no_extra_by_default(self):
        token = create_access_token("u")
        payload = decode_token(token)
        # Should only contain the standard claims
        standard_claims = {"iss", "sub", "iat", "nbf", "exp", "type"}
        assert set(payload.keys()) == standard_claims


class TestCreateRefreshToken:
    def _decode_no_verify(self, token: str) -> dict:
        return pyjwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALG],
            options={"verify_exp": False},
        )

    def test_returns_string(self):
        token = create_refresh_token("u")
        assert isinstance(token, str)

    def test_type_claim_is_refresh(self):
        token = create_refresh_token("u")
        payload = self._decode_no_verify(token)
        assert payload["type"] == "refresh"

    def test_sub_is_stringified(self):
        token = create_refresh_token(99)
        payload = self._decode_no_verify(token)
        assert payload["sub"] == "99"

    def test_sub_string_preserved(self):
        token = create_refresh_token("bob")
        payload = self._decode_no_verify(token)
        assert payload["sub"] == "bob"

    def test_jti_included_when_provided(self):
        token = create_refresh_token("u", jti="some-uuid")
        payload = self._decode_no_verify(token)
        assert payload["jti"] == "some-uuid"

    def test_no_jti_when_not_provided(self):
        token = create_refresh_token("u")
        payload = self._decode_no_verify(token)
        assert "jti" not in payload

    def test_exp_greater_than_iat(self):
        token = create_refresh_token("u")
        payload = self._decode_no_verify(token)
        assert payload["exp"] > payload["iat"]

    def test_refresh_ttl_longer_than_access_ttl(self):
        access_token = create_access_token("u")
        refresh_token = create_refresh_token("u")
        access_payload = self._decode_no_verify(access_token)
        refresh_payload = self._decode_no_verify(refresh_token)
        assert refresh_payload["exp"] > access_payload["exp"]


class TestDecodeToken:
    def test_round_trip_access_token(self):
        token = create_access_token("alice")
        payload = decode_token(token)
        assert payload["sub"] == "alice"

    def test_round_trip_int_sub(self):
        token = create_access_token(7)
        payload = decode_token(token)
        assert payload["sub"] == "7"

    def test_all_required_claims_present(self):
        token = create_access_token("u")
        payload = decode_token(token)
        for claim in ("exp", "iat", "nbf", "sub"):
            assert claim in payload

    def test_wrong_secret_raises(self):
        token = create_access_token("u")
        with pytest.raises(pyjwt.InvalidSignatureError):
            pyjwt.decode(token, "wrong-secret", algorithms=[settings.JWT_ALG])

    def test_tampered_token_raises(self):
        token = create_access_token("u")
        tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
        with pytest.raises(pyjwt.exceptions.PyJWTError):
            decode_token(tampered)

    def test_garbage_string_raises(self):
        with pytest.raises(pyjwt.exceptions.PyJWTError):
            decode_token("not.a.jwt")
