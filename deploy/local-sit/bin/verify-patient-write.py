#!/usr/bin/env python3
"""Verify the reviewed local synthetic Patient write slice on marley-sit.

Read-only checks only. No create, update or acknowledgement request is sent:
the write endpoints are exercised with a missing CSRF token, a disallowed
role, a guest session and a malformed body, all of which fail before dispatch.
The verifier then proves nothing was dispatched (the private fence has zero
events and the Patient count is unchanged).

It compares the live schema table names/count against the private pre-deploy
baseline, checks the two approved site-config keys are the only site-config
change, checks the Healthcare Settings side-effect guards and the existing
Patient target are clean, and verifies the DOC/NUR/REC/guest capability and
view matrix. Writes redacted evidence to the private state directory.
"""

from __future__ import annotations

import hashlib
import http.cookiejar
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILE = REPO_ROOT / "deploy/local-sit/compose.yaml"
STATE_DIR = Path(
    os.environ.get("MARLEY_SIT_STATE_DIR", Path.home() / ".local/state/marley-sit")
)
SITE = "marley-sit.local"
ORIGIN = "http://127.0.0.1:18081"
PROJECT = "marley-sit"
BASELINE = STATE_DIR / "evidence/patient-write-pre/baseline.json"
PRE_CONFIG = STATE_DIR / "evidence/patient-write-pre/site_config.json"
POST_CONFIG = STATE_DIR / "evidence/patient-write-pre/site_config.post.json"
ENABLED_WRITES = {"write.patient.create", "write.patient.update"}
EXPECTED_ROLES = {"DOC": ["DOC"], "NUR": ["NUR"], "REC": ["REC"]}
VIEW_PATH = "/api/method/clinic_ui.api.patients.view"
CREATE_PATH = "/api/method/clinic_ui.api.patients.create"
UPDATE_PATH = "/api/method/clinic_ui.api.patients.update"
ACK_PATH = "/api/method/clinic_ui.api.patients.ack"
BOOT_PATH = "/api/method/clinic_ui.api.session.bootstrap"
ALLOWED_ALIAS = "WS-SIT-1"
LEAK_NEEDLES = (
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
)

results: list[dict] = []


def record(name: str, ok: bool, detail: object = None) -> None:
    results.append({"check": name, "ok": bool(ok), "detail": detail})
    print(f"{'PASS' if ok else 'FAIL'} {name}: {detail}")


def compose(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "compose", "-p", PROJECT, "-f", str(COMPOSE_FILE), *args],
        capture_output=True,
        text=True,
        timeout=180,
    )


def in_backend(script: str) -> subprocess.CompletedProcess:
    return compose("exec", "-T", "backend", "bash", "-c", script)


def header(headers, name: str) -> str | None:
    for key, value in headers.items():
        if key.lower() == name.lower():
            return value
    return None


def request(opener, path: str, *, method: str = "GET", data: bytes | None = None, headers=None):
    req = urllib.request.Request(
        ORIGIN + path, data=data, method=method, headers=headers or {}
    )
    try:
        with opener.open(req, timeout=30) as response:
            return response.status, response.headers, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.headers, error.read()


def login(email: str, password: str):
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    status, _, body = request(
        opener,
        "/api/method/login",
        method="POST",
        data=urllib.parse.urlencode({"usr": email, "pwd": password}).encode(),
    )
    return opener if status == 200 and b"Logged In" in body else None


def bootstrap(opener) -> tuple[int, dict]:
    status, _, body = request(opener, BOOT_PATH)
    try:
        payload = json.loads(body)
    except ValueError:
        payload = {}
    return status, payload


def post_json(opener, path: str, payload: dict, csrf: str | None):
    headers = {"Content-Type": "application/json"}
    if csrf:
        headers["X-Frappe-CSRF-Token"] = csrf
    return request(
        opener, path, method="POST", data=json.dumps(payload).encode(), headers=headers
    )


def write_private(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
    os.chmod(path, 0o600)


PROBE = (
    "cd sites && ../env/bin/python - <<'PY'\n"
    "import hashlib, json, os, sqlite3\n"
    "import frappe\n"
    "from clinic_ui.core import fence_store, write_fence\n"
    "from clinic_ui.runtime.frappe_gateway import Gateway\n"
    f"frappe.init(site={SITE!r}, sites_path='.')\n"
    "frappe.connect()\n"
    "tables = frappe.db.sql(\n"
    "    'SELECT TABLE_NAME FROM information_schema.TABLES '\n"
    "    'WHERE TABLE_SCHEMA = DATABASE() ORDER BY TABLE_NAME', pluck=True)\n"
    "patients = frappe.get_all('Patient', fields=[\n"
    "    'name', 'first_name', 'last_name', 'patient_name', 'sex', 'dob', 'status',\n"
    "    'customer', 'user_id', 'email', 'mobile', 'phone', 'invite_user',\n"
    "])\n"
    "patient = patients[0] if len(patients) == 1 else {}\n"
    "private = frappe.get_site_path('private')\n"
    "ledger = fence_store.open_store(\n"
    "    os.path.join(private, fence_store.STORE_DIRNAME),\n"
    "    os.path.join(private, fence_store.ANCHOR_FILENAME),\n"
    ")\n"
    "store_db = os.path.join(private, fence_store.STORE_DIRNAME, write_fence.STORE_FILENAME)\n"
    "connection = sqlite3.connect(f'file:{store_db}?mode=ro', uri=True)\n"
    "events = connection.execute('SELECT COUNT(*) FROM events').fetchone()[0]\n"
    "connection.close()\n"
    "conf = frappe.get_site_config()\n"
    "hs = {field: frappe.db.get_single_value('Healthcare Settings', field) for field in (\n"
    "    'link_customer_to_patient', 'collect_registration_fee', 'send_registration_msg')}\n"
    "print(json.dumps({\n"
    "    'site': frappe.local.site,\n"
    "    'installed_apps': frappe.get_installed_apps(),\n"
    "    'table_count': len(tables),\n"
    "    'tables_sha256': hashlib.sha256('\\n'.join(tables).encode()).hexdigest(),\n"
    "    'clinic_ui_tables': [name for name in tables if 'clinic_ui' in name],\n"
    "    'patient_count': len(patients),\n"
    "    'patient': patient,\n"
    "    'healthcare_guard_unsafe': Gateway().healthcare_settings_side_effects_unsafe(),\n"
    "    'target_guard_unsafe': Gateway().native_patient_target_unsafe(patient.get('name')) if patient else None,\n"
    "    'healthcare_settings': hs,\n"
    "    'write_enabled': conf.get('clinic_ui_native_patient_write_enabled'),\n"
    "    'write_site': conf.get('clinic_ui_native_patient_write_site'),\n"
    "    'fence_ready': ledger.ready(),\n"
    "    'fence_events_sha256': hashlib.sha256(str(events).encode()).hexdigest(),\n"
    "    'fence_event_count': events,\n"
    "}, default=str))\n"
    "PY"
)

TARGET_GUARD_FIELDS = (
    "customer",
    "user_id",
    "email",
    "mobile",
    "phone",
    "invite_user",
)


def main() -> int:
    if not BASELINE.exists() or not PRE_CONFIG.exists():
        print("refusing: pre-deploy baseline/config backup is missing")
        return 2
    baseline = json.loads(BASELINE.read_text())
    credentials = json.loads((STATE_DIR / "credentials.json").read_text())
    if credentials["site"] != SITE or credentials["origin"] != ORIGIN:
        print("refusing: private credentials do not describe the isolated site")
        return 2

    preflight = subprocess.run(
        [sys.executable, str(REPO_ROOT / "deploy/local-sit/bin/preflight.py")],
        capture_output=True,
        text=True,
        timeout=180,
    )
    record(
        "preflight: project/resources owned by marley-sit",
        preflight.returncode == 0,
        {"exit": preflight.returncode, "tail": preflight.stdout.strip().splitlines()[-1:]},
    )

    revisions_run = in_backend("cat /opt/sit/revisions.txt")
    revisions = dict(
        line.split("=", 1) for line in revisions_run.stdout.splitlines() if "=" in line
    )
    image = subprocess.run(
        ["docker", "image", "inspect", "marley-sit/backend:16.6.1", "--format", "{{.Id}}"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    record(
        "deployed image is the reviewed working-tree build",
        revisions_run.returncode == 0
        and revisions.get("marley_commit") == "dda107b26e3a0fe1dbd65c7257371235de054a04"
        and revisions.get("clinic_source_rev") == "ef8b65a-dirty"
        and image.stdout.strip().startswith("sha256:"),
        {
            "image": image.stdout.strip(),
            "marley_commit_matches": revisions.get("marley_commit")
            == "dda107b26e3a0fe1dbd65c7257371235de054a04",
            "clinic_source_rev": revisions.get("clinic_source_rev"),
        },
    )

    probe_run = in_backend(PROBE)
    probe = json.loads(probe_run.stdout) if probe_run.returncode == 0 else {}
    record(
        "probe of site state succeeds",
        probe_run.returncode == 0 and probe.get("site") == SITE,
        {"exit": probe_run.returncode, "site": probe.get("site")},
    )

    record(
        "write configuration is exactly the two approved keys",
        probe.get("write_enabled") == 1
        and probe.get("write_site") == SITE
        and probe.get("installed_apps") == ["frappe", "erpnext", "healthcare", "clinic_ui"],
        {
            "write_enabled": probe.get("write_enabled"),
            "write_site": probe.get("write_site"),
            "installed_apps": probe.get("installed_apps"),
        },
    )

    # Only the two approved keys may differ from the pre-deploy site config.
    copy = compose(
        "cp",
        f"backend:/home/frappe/frappe-bench/sites/{SITE}/site_config.json",
        str(POST_CONFIG),
    )
    post_conf = json.loads(POST_CONFIG.read_text()) if copy.returncode == 0 else {}
    pre_conf = json.loads(PRE_CONFIG.read_text())
    changed = sorted(
        key for key in set(pre_conf) | set(post_conf) if pre_conf.get(key) != post_conf.get(key)
    )
    os.chmod(POST_CONFIG, 0o600)
    record(
        "site config changed only by the two approved write keys",
        copy.returncode == 0
        and changed
        == ["clinic_ui_native_patient_write_enabled", "clinic_ui_native_patient_write_site"],
        {"changed_keys": changed},
    )

    hs = probe.get("healthcare_settings") or {}
    record(
        "Healthcare Settings side-effect guards are clear",
        hs
        == {
            "link_customer_to_patient": 0,
            "collect_registration_fee": 0,
            "send_registration_msg": 0,
        }
        and probe.get("healthcare_guard_unsafe") is False,
        {**hs, "guard_unsafe": probe.get("healthcare_guard_unsafe")},
    )

    patient = probe.get("patient") or {}
    target_unsafe = {
        field: bool(patient.get(field)) for field in TARGET_GUARD_FIELDS
    }
    populated = sorted(field for field, value in target_unsafe.items() if value)
    record(
        "legacy fixture is refused by the update target guard (no side effect)",
        probe.get("patient_count") == 1
        and populated == ["customer", "invite_user"]
        and probe.get("target_guard_unsafe") is True,
        {
            "populated": populated,
            "patient_count": probe.get("patient_count"),
            "target_guard_unsafe": probe.get("target_guard_unsafe"),
        },
    )

    record(
        "private fence store is ready and empty",
        probe.get("fence_ready") is True and probe.get("fence_event_count") == 0,
        {
            "ready": probe.get("fence_ready"),
            "event_count": probe.get("fence_event_count"),
        },
    )

    record(
        "schema tables and count match the pre-deploy baseline",
        probe.get("table_count") == baseline.get("table_count")
        and probe.get("tables_sha256") == baseline.get("tables_sha256")
        and probe.get("clinic_ui_tables") == [],
        {
            "table_count": probe.get("table_count"),
            "baseline_table_count": baseline.get("table_count"),
            "tables_match": probe.get("tables_sha256") == baseline.get("tables_sha256"),
            "clinic_ui_tables": probe.get("clinic_ui_tables"),
        },
    )

    accounts = {u["clinic_code"]: u for u in credentials["users"]}
    openers = {code: login(accounts[code]["email"], accounts[code]["password"]) for code in ("DOC", "NUR", "REC")}
    record(
        "all three synthetic actors can log in",
        all(openers[code] is not None for code in ("DOC", "NUR", "REC")),
        {code: openers[code] is not None for code in ("DOC", "NUR", "REC")},
    )

    boots = {}
    for code, opener in openers.items():
        if opener is not None:
            boots[code] = bootstrap(opener)
    guest = urllib.request.build_opener()
    guest_status, guest_boot = bootstrap(guest)
    record(
        "guest bootstrap is a safe 401 envelope",
        guest_status == 401 and guest_boot == {"code": "AUTH_EXPIRED"},
        {"status": guest_status, "body": guest_boot},
    )

    for code in ("DOC", "NUR"):
        status, payload = boots.get(code, (0, {}))
        boot = payload.get("message") or {}
        capabilities = boot.get("capabilities") or {}
        enabled = {name for name, state in capabilities.items() if state == "enabled"}
        record(
            f"{code} bootstrap advertises exactly the two patient writes as enabled",
            status == 200
            and boot.get("user", {}).get("roles") == EXPECTED_ROLES[code]
            and enabled == ENABLED_WRITES
            and all(state in ("read_only", "enabled") for state in capabilities.values()),
            {
                "status": status,
                "roles": boot.get("user", {}).get("roles"),
                "enabled": sorted(enabled),
                "states": sorted(set(capabilities.values())),
            },
        )

    status, payload = boots.get("REC", (0, {}))
    boot = payload.get("message") or {}
    capabilities = boot.get("capabilities") or {}
    record(
        "REC bootstrap remains entirely read_only",
        status == 200
        and boot.get("user", {}).get("roles") == ["REC"]
        and capabilities
        and all(state == "read_only" for state in capabilities.values()),
        {
            "status": status,
            "roles": boot.get("user", {}).get("roles"),
            "states": sorted(set(capabilities.values())),
        },
    )

    view_query = urllib.parse.urlencode({"workspace_patient_id": ALLOWED_ALIAS})
    for code in ("DOC", "NUR"):
        opener = openers.get(code)
        status, headers, body = request(opener, f"{VIEW_PATH}?{view_query}")
        try:
            view = json.loads(body).get("message") or {}
        except ValueError:
            view = {}
        record(
            f"{code} can read the native patient view",
            status == 200
            and view.get("workspace_patient_id") == ALLOWED_ALIAS
            and "native_patient_id" not in view
            and isinstance(view.get("editable"), bool)
            and header(headers, "Cache-Control") == "no-store",
            {
                "status": status,
                "alias": view.get("workspace_patient_id"),
                "editable": view.get("editable"),
                "native_id_absent": "native_patient_id" not in view,
            },
        )

    status, _, body = request(openers.get("REC"), f"{VIEW_PATH}?{view_query}")
    record(
        "REC native patient view is denied",
        status == 403 and json.loads(body) == {"code": "FORBIDDEN"},
        {"status": status},
    )
    status, _, body = request(guest, f"{VIEW_PATH}?{view_query}")
    record(
        "guest native patient view is denied",
        status == 403 and json.loads(body) == {"code": "FORBIDDEN"},
        {"status": status},
    )

    doc = openers.get("DOC")
    doc_csrf = ((boots.get("DOC", (0, {}))[1].get("message") or {}).get("csrfToken"))
    rec = openers.get("REC")
    rec_csrf = ((boots.get("REC", (0, {}))[1].get("message") or {}).get("csrfToken"))

    def check_write_error(name, opener, path, payload, csrf, expected_status, expected_code):
        status, headers, body = post_json(opener, path, payload, csrf)
        text = body.decode("utf-8", "replace")
        try:
            body_payload = json.loads(text)
        except ValueError:
            body_payload = None
        leaks = [needle for needle in LEAK_NEEDLES if needle in text]
        record(
            name,
            status == expected_status
            and body_payload == {"code": expected_code}
            and not leaks
            and header(headers, "Content-Type") == "application/json",
            {"status": status, "body_exact": body_payload == {"code": expected_code}, "leaks": leaks},
        )

    check_write_error(
        "create without CSRF is refused before dispatch",
        doc,
        CREATE_PATH,
        {},
        None,
        400,
        "VALIDATION",
    )
    check_write_error(
        "REC create with a valid CSRF token is refused by role",
        rec,
        CREATE_PATH,
        {},
        rec_csrf,
        403,
        "FORBIDDEN",
    )
    check_write_error(
        "guest create is refused",
        guest,
        CREATE_PATH,
        {},
        None,
        403,
        "FORBIDDEN",
    )
    check_write_error(
        "DOC create with a malformed body validates without dispatch",
        doc,
        CREATE_PATH,
        {},
        doc_csrf,
        422,
        "VALIDATION",
    )
    check_write_error(
        "DOC update with a malformed body validates without dispatch",
        doc,
        UPDATE_PATH,
        {},
        doc_csrf,
        422,
        "VALIDATION",
    )
    check_write_error(
        "DOC acknowledgement with a malformed body validates without dispatch",
        doc,
        ACK_PATH,
        {},
        doc_csrf,
        422,
        "VALIDATION",
    )

    after_run = in_backend(PROBE)
    after = json.loads(after_run.stdout) if after_run.returncode == 0 else {}
    record(
        "no patient or fence write occurred",
        after_run.returncode == 0
        and after.get("patient_count") == baseline.get("patient_count")
        and after.get("fence_ready") is True
        and after.get("fence_event_count") == 0
        and after.get("table_count") == baseline.get("table_count")
        and after.get("tables_sha256") == baseline.get("tables_sha256"),
        {
            "patient_count": after.get("patient_count"),
            "baseline_patient_count": baseline.get("patient_count"),
            "fence_ready": after.get("fence_ready"),
            "fence_event_count": after.get("fence_event_count"),
            "schema_unchanged": after.get("tables_sha256") == baseline.get("tables_sha256"),
        },
    )

    evidence_dir = STATE_DIR / "evidence"
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    path = evidence_dir / f"patient-write-verify-{stamp}.json"
    write_private(
        path,
        {
            "origin": ORIGIN,
            "site": SITE,
            "image": image.stdout.strip(),
            "schema": {
                "table_count": probe.get("table_count"),
                "tables_sha256": probe.get("tables_sha256"),
                "baseline_table_count": baseline.get("table_count"),
                "baseline_tables_sha256": baseline.get("tables_sha256"),
            },
            "site_config_changed_keys": changed,
            "checks": results,
        },
    )

    failed = [item["check"] for item in results if not item["ok"]]
    print(f"evidence: {path} (mode 600)")
    print(f"checks: {len(results) - len(failed)}/{len(results)} passed")
    with (STATE_DIR / "deployment.log").open("a") as log:
        log.write(
            f"{stamp} patient-write verify exit={'0' if not failed else '1'} "
            f"passed={len(results) - len(failed)}/{len(results)} "
            f"failed={failed} image={image.stdout.strip()} evidence={path}\n"
        )
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
