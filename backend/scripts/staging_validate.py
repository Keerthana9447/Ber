#!/usr/bin/env python3
"""Black-box validation of a RUNNING BEREV API (staging). Standard library only.

  python scripts/staging_validate.py --base-url https://staging.example.com
  STAGING_EMAIL=... STAGING_PASSWORD=... python scripts/staging_validate.py --base-url ... --db-checks

Run it against STAGING with a throwaway account. It logs in and out and rotates refresh tokens.
`--db-checks` also runs `python -m app.cli preflight` and `verify-integrity` using DATABASE_URL from
the environment/.env: point that at the staging database only, never at production or a dev database.

Exit code 0 = no FAIL (warnings allowed unless --fail-on-warn). A passing run proves only what it checks;
it does NOT replace the manual acceptance, backup/restore and tax-professional steps in
docs/PRODUCTION_READINESS.md.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

PROTECTED = ["/customers", "/products", "/invoices", "/payments", "/dashboard", "/settings", "/notifications"]
BACKEND_DIR = Path(__file__).resolve().parents[1]


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []

    def add(self, status: str, name: str, detail: str = "") -> None:
        self.rows.append((status, name, detail))

    def count(self, status: str) -> int:
        return sum(1 for r in self.rows if r[0] == status)


def http(method: str, url: str, body: object | None = None, headers: dict | None = None, timeout: float = 10.0):
    """Returns (status, response_headers, text). Never raises on HTTP error codes; status 0 = no connection."""
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, dict(response.headers), response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read().decode("utf-8", "replace")
    except Exception as error:  # connection refused, DNS, timeout, TLS
        return 0, {}, f"{type(error).__name__}: {error}"


def jget(text: str):
    try:
        return json.loads(text)
    except ValueError:
        return None


def run_checks(base_url: str, email: str | None, password: str | None, production: bool,
               db_checks: bool, load_requests: int = 30) -> Report:
    r = Report()
    api = base_url.rstrip("/") + "/api/v1"

    # ---- liveness and readiness
    status, headers, text = http("GET", f"{api}/health")
    if status == 0:
        r.add("FAIL", "API reachable", text)
        return r  # nothing else can work
    r.add("PASS" if status == 200 and (jget(text) or {}).get("status") == "ok" else "FAIL", "liveness /health", f"HTTP {status}")

    status, _, text = http("GET", f"{api}/health/ready")
    body = jget(text) or {}
    if status == 200 and body.get("status") == "ready":
        r.add("PASS", "readiness /health/ready (database + migrations)")
    else:
        r.add("FAIL", "readiness /health/ready", f"HTTP {status} problems={body.get('problems')}")
    if body.get("notification_scheduler_failing"):
        r.add("WARN", "notification scheduler", "reports consecutive failures; check server logs")

    # ---- unauthenticated access must be refused
    for path in PROTECTED:
        status, _, _ = http("GET", api + path)
        if status in (401, 403):
            r.add("PASS", f"unauthenticated {path} refused", f"HTTP {status}")
        elif status == 404:
            r.add("WARN", f"unauthenticated {path}", "route not found (module not deployed?)")
        else:
            r.add("FAIL", f"unauthenticated {path} must be refused", f"HTTP {status}")

    # ---- bad credentials / malformed input must not leak or crash
    status, _, text = http("POST", f"{api}/auth/login", {"email": "nobody@example.invalid", "password": "wrong-password-123"})
    r.add("PASS" if status == 401 else "FAIL", "login with wrong credentials -> 401", f"HTTP {status}")
    r.add("PASS" if "Traceback" not in text and "sqlalchemy" not in text.lower() else "FAIL",
          "login error response leaks no internals")
    status, _, text = http("POST", f"{api}/auth/login", {"email": "x"})
    r.add("PASS" if status == 422 and "wrong-password" not in text else "FAIL", "malformed login -> 422", f"HTTP {status}")
    status, headers, text = http("GET", f"{api}/does-not-exist")
    r.add("PASS" if status == 404 and jget(text) is not None else "WARN", "unknown route returns JSON 404", f"HTTP {status}")
    status, _, _ = http("GET", f"{api}/customers", headers={"Authorization": "Bearer not.a.real.token"})
    r.add("PASS" if status == 401 else "FAIL", "garbage bearer token -> 401", f"HTTP {status}")

    # ---- authenticated session lifecycle (needs a staging account)
    if email and password:
        status, _, text = http("POST", f"{api}/auth/login", {"email": email, "password": password})
        tokens = jget(text) or {}
        if status == 200 and tokens.get("access_token") and tokens.get("refresh_token"):
            r.add("PASS", "login with staging account")
            access, refresh1 = tokens["access_token"], tokens["refresh_token"]
            auth = {"Authorization": f"Bearer {access}"}
            status, _, text = http("GET", f"{api}/auth/me", headers=auth)
            me = jget(text) or {}
            r.add("PASS" if status == 200 and me.get("email", "").lower() == email.lower() else "FAIL", "/auth/me", f"HTTP {status}")
            tampered = access[:-4] + ("AAAA" if not access.endswith("AAAA") else "BBBB")
            status, _, _ = http("GET", f"{api}/auth/me", headers={"Authorization": f"Bearer {tampered}"})
            r.add("PASS" if status == 401 else "FAIL", "tampered access token -> 401", f"HTTP {status}")
            status, _, text = http("POST", f"{api}/auth/refresh", {"refresh_token": refresh1})
            rotated = jget(text) or {}
            if status == 200 and rotated.get("refresh_token") and rotated["refresh_token"] != refresh1:
                r.add("PASS", "refresh rotates the refresh token")
                status, _, _ = http("POST", f"{api}/auth/refresh", {"refresh_token": refresh1})
                r.add("PASS" if status == 401 else "FAIL", "replayed (old) refresh token rejected", f"HTTP {status}")
                status, _, _ = http("POST", f"{api}/auth/logout", {"refresh_token": rotated["refresh_token"]})
                r.add("PASS" if status in (200, 204) else "FAIL", "logout", f"HTTP {status}")
                status, _, _ = http("POST", f"{api}/auth/refresh", {"refresh_token": rotated["refresh_token"]})
                r.add("PASS" if status == 401 else "FAIL", "refresh token revoked after logout", f"HTTP {status}")
            else:
                r.add("FAIL", "refresh rotation", f"HTTP {status}")
        else:
            r.add("FAIL", "login with staging account", f"HTTP {status}")
    else:
        r.add("WARN", "authenticated checks skipped", "set STAGING_EMAIL and STAGING_PASSWORD")

    # ---- hardening observations (WARN = known gap, not a crash)
    _, headers, _ = http("GET", f"{api}/health")
    lowered = {k.lower() for k in headers}
    for header in (
        "x-content-type-options",
        "x-frame-options",
        "referrer-policy",
        "content-security-policy",
        "permissions-policy",
    ):
        r.add("PASS" if header in lowered else "WARN", f"security header {header}",
              "" if header in lowered else "missing (set it in the reverse proxy or app)")
    if base_url.lower().startswith("https://"):
        r.add("PASS" if "strict-transport-security" in lowered else "WARN", "HSTS header")
    else:
        r.add("WARN" if production else "PASS", "HTTPS", "base URL is not https" if production else "dev/staging over http")
    for path in ("/docs", "/redoc", "/openapi.json"):
        status, _, _ = http("GET", base_url.rstrip("/") + path)
        exposed = status == 200
        r.add("WARN" if (exposed and production) else "PASS", f"API docs {path}", "publicly reachable" if exposed else "not exposed")

    # ---- light concurrency / latency smoke (not a load test)
    def timed(_):
        started = time.perf_counter()
        status, _, _ = http("GET", f"{api}/health/ready")
        return status, (time.perf_counter() - started) * 1000
    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(timed, range(load_requests)))
    failed = [s for s, _ in results if s != 200]
    latencies = sorted(ms for _, ms in results)
    p95 = latencies[int(len(latencies) * 0.95) - 1] if latencies else 0
    r.add("PASS" if not failed else "FAIL", f"{load_requests} parallel readiness calls", f"{len(failed)} non-200")
    r.add("PASS" if p95 < 1000 else "WARN", "readiness p95 latency", f"{p95:.0f} ms (median {statistics.median(latencies):.0f} ms)")

    # ---- database-side checks (run locally against the staging DB)
    if db_checks:
        for label, command in (("alembic head / DB preflight", "preflight"), ("data integrity", "verify-integrity")):
            done = subprocess.run([sys.executable, "-m", "app.cli", command], cwd=BACKEND_DIR,
                                  capture_output=True, text=True, env=os.environ.copy())
            tail = (done.stdout + done.stderr).strip().splitlines()[-1:] or [""]
            r.add("PASS" if done.returncode == 0 else "FAIL", label, tail[0][:200])
    else:
        r.add("WARN", "database checks skipped", "use --db-checks with DATABASE_URL pointing at staging")
    return r


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default=os.environ.get("STAGING_BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--production-like", action="store_true", help="treat http and open API docs as warnings")
    parser.add_argument("--db-checks", action="store_true")
    parser.add_argument("--fail-on-warn", action="store_true")
    args = parser.parse_args()
    report = run_checks(args.base_url, os.environ.get("STAGING_EMAIL"), os.environ.get("STAGING_PASSWORD"),
                        args.production_like, args.db_checks)
    for status, name, detail in report.rows:
        print(f"{status:4}  {name}" + (f"  -- {detail}" if detail else ""))
    print(f"\n{report.count('PASS')} passed, {report.count('WARN')} warnings, {report.count('FAIL')} failed")
    return 1 if report.count("FAIL") or (args.fail_on_warn and report.count("WARN")) else 0


if __name__ == "__main__":
    sys.exit(main())
