#!/usr/bin/env python3
"""Verify the safe error envelope for clinic API failures.

Exercises the clinic patient endpoint on the v1, unversioned and v2 method
paths with authenticated REC, an unknown alias as DOC, guest access, and guest
session bootstrap. Every error must keep its HTTP status and return exactly
``{"code": "<classification>"}`` with no traceback, source path, server
message, message log or fixture text. Writes redacted evidence privately.
"""

from __future__ import annotations

import http.cookiejar
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

STATE_DIR = Path(
    os.environ.get("MARLEY_SIT_STATE_DIR", Path.home() / ".local/state/marley-sit")
)
ORIGIN = "http://127.0.0.1:18081"
ALLOWED_ALIAS = "WS-SIT-1"
PREFIXES = ("/api/method", "/api/v1/method", "/api/v2/method")
FORBIDDEN_TEXT = (
    "Traceback (most recent call last)",
    "Traceback",
    'File "',
    "/home/frappe",
    "clinic_ui/",
    "_server_messages",
    "_exc_source",
    "_debug_messages",
    "message_log",
    "_error_message",
    "SITSYNTH",
    "Alpha",
    "WS-SIT-1",
    "patient_name",
)

results: list[dict] = []


def record(name: str, ok: bool, detail: object = None) -> None:
    results.append({"check": name, "ok": bool(ok), "detail": detail})
    print(f"{'PASS' if ok else 'FAIL'} {name}: {detail}")


def request(opener, path: str):
    try:
        with opener.open(ORIGIN + path, timeout=30) as response:
            return response.status, response.headers, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.headers, error.read()


def header(headers, name: str) -> str | None:
    for key, value in headers.items():
        if key.lower() == name.lower():
            return value
    return None


def login(email: str, password: str):
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    data = urllib.parse.urlencode({"usr": email, "pwd": password}).encode()
    req = urllib.request.Request(ORIGIN + "/api/method/login", data=data, method="POST")
    with opener.open(req, timeout=30) as response:
        if response.status != 200:
            raise SystemExit("login failed")
    return opener


def check_error(name: str, opener, path: str, expected_status: int, expected_code: str) -> None:
    status, headers, body = request(opener, path)
    text = body.decode("utf-8", "replace")
    try:
        payload = json.loads(text)
    except Exception:
        payload = None
    leaks = [needle for needle in FORBIDDEN_TEXT if needle in text]
    cache_control = header(headers, "Cache-Control") or ""
    record(
        name,
        status == expected_status
        and payload == {"code": expected_code}
        and not leaks
        and header(headers, "Content-Type") == "application/json"
        and "no-store" in cache_control.lower(),
        {
            "status": status,
            "body_exact": payload == {"code": expected_code},
            "leaks": leaks,
            "content_type": header(headers, "Content-Type"),
            "cache_control": cache_control,
        },
    )


def main() -> int:
    credentials = json.loads((STATE_DIR / "credentials.json").read_text())
    accounts = {u["clinic_code"]: u for u in credentials["users"]}
    doc = login(accounts["DOC"]["email"], accounts["DOC"]["password"])
    rec = login(accounts["REC"]["email"], accounts["REC"]["password"])
    guest = urllib.request.build_opener()

    alias_query = urllib.parse.urlencode({"workspace_patient_id": ALLOWED_ALIAS})
    unknown_query = urllib.parse.urlencode({"workspace_patient_id": "NOT-AN-ALIAS"})

    for prefix in PREFIXES:
        version = {"": "unversioned", "/api/v1/method": "v1", "/api/v2/method": "v2"}.get(
            prefix, prefix
        )
        check_error(
            f"DOC unknown alias denied with safe envelope ({version})",
            doc,
            f"{prefix}/clinic_ui.api.patients.view?{unknown_query}",
            403,
            "FORBIDDEN",
        )
        check_error(
            f"REC allowlisted alias denied with safe envelope ({version})",
            rec,
            f"{prefix}/clinic_ui.api.patients.view?{alias_query}",
            403,
            "FORBIDDEN",
        )
        check_error(
            f"Guest patient read denied with safe envelope ({version})",
            guest,
            f"{prefix}/clinic_ui.api.patients.view?{alias_query}",
            403,
            "FORBIDDEN",
        )
        check_error(
            f"Guest session bootstrap is a safe 401 envelope ({version})",
            guest,
            f"{prefix}/clinic_ui.api.session.bootstrap",
            401,
            "AUTH_EXPIRED",
        )

    check_error(
        "Legacy cmd dispatch denied with safe envelope",
        guest,
        "/api/method?cmd=clinic_ui.api.patients.view&"
        + alias_query,
        403,
        "FORBIDDEN",
    )

    evidence_dir = STATE_DIR / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(evidence_dir, 0o700)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    path = evidence_dir / f"error-envelope-verify-{stamp}.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump({"origin": ORIGIN, "checks": results}, handle, indent=2)
    os.chmod(path, 0o600)

    failed = [item["check"] for item in results if not item["ok"]]
    print(f"evidence: {path} (mode 600)")
    print(f"checks: {len(results) - len(failed)}/{len(results)} passed")
    with (STATE_DIR / "deployment.log").open("a") as log:
        log.write(
            f"{stamp} error-envelope verify exit={'0' if not failed else '1'} "
            f"passed={len(results) - len(failed)}/{len(results)} "
            f"failed={failed} evidence={path}\n"
        )
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
