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

    def test_cors_origins_are_normalized_and_reject_wildcards(self):
        configured = make_settings(cors_allowed_origins=" https://erp.example.test/ ,https://admin.example.test ")
        self.assertEqual(
            configured.cors_origins,
            ("https://erp.example.test", "https://admin.example.test"),
        )
        with self.assertRaises(ValidationError):
            make_settings(cors_allowed_origins="*")
        with self.assertRaises(ValidationError):
            make_settings(cors_allowed_origins="https://erp.example.test/path")


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

    def test_production_hides_api_docs_and_sets_security_headers(self):
        app = create_app(make_settings(environment="production"))

        async def make_requests():
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="https://testserver",
            ) as client:
                return (
                    await client.get("/api/v1/health"),
                    await client.get("/docs"),
                    await client.get("/redoc"),
                    await client.get("/openapi.json"),
                )

        try:
            response, docs, redoc, openapi = asyncio.run(make_requests())
        finally:
            app.state.engine.dispose()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(docs.status_code, 404)
        self.assertEqual(redoc.status_code, 404)
        self.assertEqual(openapi.status_code, 404)
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertEqual(response.headers["x-frame-options"], "DENY")
        self.assertEqual(response.headers["referrer-policy"], "strict-origin-when-cross-origin")
        self.assertIn("max-age=31536000", response.headers["strict-transport-security"])

    def test_cors_allows_configured_origin_only(self):
        app = create_app(
            make_settings(cors_allowed_origins="https://erp.example.test")
        )

        async def make_requests():
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://testserver",
            ) as client:
                allowed = await client.options(
                    "/api/v1/health",
                    headers={
                        "Origin": "https://erp.example.test",
                        "Access-Control-Request-Method": "GET",
                    },
                )
                denied = await client.options(
                    "/api/v1/health",
                    headers={
                        "Origin": "https://untrusted.example.test",
                        "Access-Control-Request-Method": "GET",
                    },
                )
                return allowed, denied

        try:
            allowed, denied = asyncio.run(make_requests())
        finally:
            app.state.engine.dispose()
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(
            allowed.headers["access-control-allow-origin"],
            "https://erp.example.test",
        )
        self.assertNotIn("access-control-allow-origin", denied.headers)

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

    def test_database_connection_failure_returns_service_unavailable(self):
        app = create_app(
            make_settings(database_url="postgresql+psycopg://berev_app@127.0.0.1:1/berev_test")
        )

        async def make_request():
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://testserver",
            ) as client:
                return await client.post(
                    "/api/v1/auth/login",
                    json={"email": "admin@example.test", "password": "valid-test-password"},
                )

        try:
            response = asyncio.run(make_request())
        finally:
            app.state.engine.dispose()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {"detail": "Database is temporarily unavailable. Please try again shortly."},
        )
        self.assertEqual(response.headers["retry-after"], "5")


if __name__ == "__main__":
    unittest.main()
