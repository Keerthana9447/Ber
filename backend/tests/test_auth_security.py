import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.auth.security import (
    create_token_pair,
    decode_token,
    failed_login_state,
    hash_password,
    permissions_satisfy,
    verify_password,
)
from app.auth.dependencies import require_permissions
from app.config import Settings
from fastapi import HTTPException


def settings() -> Settings:
    return Settings(
        _env_file=None,
        database_url="postgresql+psycopg://berev_app@127.0.0.1:5432/berev_test",
        jwt_secret_key="test-key-material-not-a-production-secret-000000000000",
    )


class PasswordHashTests(unittest.TestCase):
    def test_hash_verifies_and_is_salted(self):
        first = hash_password("strong-test-password")
        second = hash_password("strong-test-password")
        self.assertNotEqual(first, second)
        self.assertTrue(verify_password("strong-test-password", first))
        self.assertFalse(verify_password("wrong-password", first))

    def test_invalid_hash_and_empty_password_fail_closed(self):
        self.assertFalse(verify_password("password", "not-an-argon2-hash"))
        self.assertFalse(verify_password("", hash_password("strong-test-password")))
        with self.assertRaises(ValueError):
            hash_password("")


class JwtTests(unittest.TestCase):
    def test_access_and_refresh_tokens_are_signed_and_typed(self):
        configured = settings()
        issued = datetime.now(timezone.utc)
        pair = create_token_pair(configured, 17, issued)
        access = decode_token(configured, pair.access_token, "access")
        refresh = decode_token(configured, pair.refresh_token, "refresh")
        self.assertEqual(access["sub"], "17")
        self.assertEqual(refresh["typ"], "refresh")
        self.assertIsNone(decode_token(configured, pair.refresh_token, "access"))

    def test_expired_and_tampered_tokens_are_rejected(self):
        configured = settings()
        pair = create_token_pair(
            configured,
            1,
            datetime.now(timezone.utc) - timedelta(days=2),
        )
        self.assertIsNone(decode_token(configured, pair.access_token, "access"))
        tampered = pair.refresh_token[:-1] + ("A" if pair.refresh_token[-1] != "A" else "B")
        self.assertIsNone(decode_token(configured, tampered, "refresh"))


class AuthorizationPolicyTests(unittest.TestCase):
    def test_permission_check_requires_all_requested_permissions(self):
        granted = {"auth:self:read", "customers:read"}
        self.assertTrue(permissions_satisfy(granted, {"customers:read"}))
        self.assertTrue(permissions_satisfy(granted, set()))
        self.assertFalse(permissions_satisfy(granted, {"customers:read", "customers:update"}))

    def test_rbac_dependency_allows_and_denies_by_role_permissions(self):
        dependency = require_permissions("customers:read")
        user = SimpleNamespace(
            role=SimpleNamespace(
                permissions=[SimpleNamespace(code="customers:read")]
            )
        )
        self.assertIs(dependency(user), user)
        with self.assertRaises(HTTPException) as error:
            require_permissions("customers:update")(user)
        self.assertEqual(error.exception.status_code, 403)


class LoginLockoutTests(unittest.TestCase):
    def test_lockout_starts_at_configured_failure_threshold(self):
        now = datetime.now(timezone.utc)
        count, deadline = failed_login_state(2, now, 3, timedelta(minutes=10))
        self.assertEqual(count, 3)
        self.assertEqual(deadline, now + timedelta(minutes=10))

    def test_failures_before_threshold_do_not_lock(self):
        count, deadline = failed_login_state(
            0,
            datetime.now(timezone.utc),
            5,
            timedelta(minutes=15),
        )
        self.assertEqual(count, 1)
        self.assertIsNone(deadline)


if __name__ == "__main__":
    unittest.main()
