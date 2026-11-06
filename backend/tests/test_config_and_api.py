import asyncio
import unittest
from unittest.mock import patch

from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.config import Settings
from app.main import create_app


def make_settings(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "database_url": "postgresql+psycopg://berev_app@127.0.0.1:5432/berev_test",
        "jwt_secret_key": "test-key-material-not-a-production-secret-000000000000",
    }
    values.update(overrides)
    return Settings(**values)


class SettingsTests(unittest.TestCase):
    def test_settings_validate_and_normalize_environment_values(self):
        configured = make_settings(log_level="debug", access_token_minutes=20)
        self.assertEqual(configured.log_level, "DEBUG")
        self.assertEqual(configured.access_token_minutes, 20)
        self.assertEqual(
            configured.database_url.get_secret_value(),
            "postgresql+psycopg://berev_app@127.0.0.1:5432/berev_test",
        )

    def test_settings_read_environment_variables(self):
        env = {
            "DATABASE_URL": "postgresql+psycopg://berev_app@127.0.0.1:5432/berev_test",
            "JWT_SECRET_KEY": "test-key-material-not-a-production-secret-000000000000",
            "ACCESS_TOKEN_MINUTES": "25",
            "LOG_LEVEL": "warning",
        }
        with patch.dict("os.environ", env, clear=True):
            configured = Settings(_env_file=None)
        self.assertEqual(configured.access_token_minutes, 25)
        self.assertEqual(configured.log_level, "WARNING")

    def test_settings_reject_non_postgresql_driver(self):
        with self.assertRaises(ValidationError):
            make_settings(database_url="sqlite:///not-for-this-application.db")

    def test_settings_reject_short_jwt_secret(self):
        with self.assertRaises(ValidationError):
            make_settings(jwt_secret_key="short")

    def test_settings_reject_invalid_log_level(self):
        with self.assertRaises(ValidationError):
            make_settings(log_level="TRACE")


class HealthApiTests(unittest.TestCase):
    def test_versioned_health_endpoint_is_app_liveness_only(self):
        app = create_app(make_settings())
        async def make_request():
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://testserver",
            ) as client:
                return await client.get("/api/v1/health")

        try:
            response = asyncio.run(make_request())
        finally:
            app.state.engine.dispose()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        self.assertEqual(app.state.engine.url.database, "berev_test")

    def test_validation_errors_do_not_echo_password_input(self):
        app = create_app(make_settings())
        sensitive_value = "non-secret-test-value-" * 50

        async def make_request():
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://testserver",
            ) as client:
                return await client.post(
                    "/api/v1/auth/login",
                    json={"email": "admin@example.test", "password": sensitive_value},
                )

        try:
            response = asyncio.run(make_request())
        finally:
            app.state.engine.dispose()
        self.assertEqual(response.status_code, 422)
        self.assertNotIn(sensitive_value, response.text)
        self.assertNotIn('"input"', response.text)


if __name__ == "__main__":
    unittest.main()
