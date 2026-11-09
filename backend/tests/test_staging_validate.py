"""Tests the staging validator itself against stub servers (stdlib only, no database)."""
import importlib.util
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("staging_validate", Path(__file__).resolve().parents[1] / "scripts" / "staging_validate.py")
sv = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sv)

EMAIL, PASSWORD = "qa@example.test", "correct-horse-battery"


def make_handler(open_customers: bool = False):
    state = {"valid_refresh": set(), "n": 0}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, status, payload=None, headers=None):
            raw = json.dumps(payload).encode() if payload is not None else b""
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
            self.send_header("Content-Security-Policy", "default-src 'none'")
            self.send_header("Permissions-Policy", "camera=()")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def body(self):
            length = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(length) or b"{}")

        def do_GET(self):
            path = self.path
            if path == "/api/v1/health":
                return self.send(200, {"status": "ok"})
            if path == "/api/v1/health/ready":
                return self.send(200, {"status": "ready", "problems": [], "notification_scheduler_failing": False})
            if path == "/api/v1/auth/me":
                return self.send(200, {"email": EMAIL}) if self.headers.get("Authorization") == "Bearer good-access" else self.send(401, {"detail": "Authentication required"})
            if path == "/api/v1/customers" and open_customers:
                return self.send(200, {"items": []})
            if path.startswith("/api/v1/") and path.split("/")[-1] in {"customers", "products", "invoices", "payments", "dashboard", "settings", "notifications"}:
                return self.send(401, {"detail": "Authentication required"})
            return self.send(404, {"detail": "Not Found"})

        def do_POST(self):
            data = self.body()
            if self.path == "/api/v1/auth/login":
                if "password" not in data:
                    return self.send(422, {"detail": [{"msg": "Field required"}]})
                if data.get("email") == EMAIL and data.get("password") == PASSWORD:
                    state["n"] += 1
                    token = f"refresh-{state['n']}"
                    state["valid_refresh"].add(token)
                    return self.send(200, {"access_token": "good-access", "refresh_token": token})
                return self.send(401, {"detail": "Invalid email or password"})
            if self.path == "/api/v1/auth/refresh":
                token = data.get("refresh_token")
                if token in state["valid_refresh"]:
                    state["valid_refresh"].discard(token)
                    state["n"] += 1
                    new = f"refresh-{state['n']}"
                    state["valid_refresh"].add(new)
                    return self.send(200, {"access_token": "good-access", "refresh_token": new})
                return self.send(401, {"detail": "Invalid refresh token"})
            if self.path == "/api/v1/auth/logout":
                state["valid_refresh"].discard(data.get("refresh_token"))
                return self.send(204)
            return self.send(404, {"detail": "Not Found"})

    return Handler


class StagingValidatorTests(unittest.TestCase):
    def run_against(self, **handler_kwargs):
        server = HTTPServer(("127.0.0.1", 0), make_handler(**handler_kwargs))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            return sv.run_checks(f"http://127.0.0.1:{server.server_port}", EMAIL, PASSWORD, production=False,
                                 db_checks=False, load_requests=10)
        finally:
            server.shutdown()
            server.server_close()

    def test_correct_server_has_no_failures(self):
        report = self.run_against()
        failures = [row for row in report.rows if row[0] == "FAIL"]
        self.assertEqual(failures, [])
        self.assertGreaterEqual(report.count("PASS"), 18)

    def test_open_endpoint_is_detected(self):
        report = self.run_against(open_customers=True)
        names = [name for status, name, _ in report.rows if status == "FAIL"]
        self.assertIn("unauthenticated /customers must be refused", names)

    def test_unreachable_api_fails_fast(self):
        report = sv.run_checks("http://127.0.0.1:1", None, None, False, False)
        self.assertEqual([row[0] for row in report.rows], ["FAIL"])


if __name__ == "__main__":
    unittest.main()
