#!/usr/bin/env python3
"""Verify the reviewed patient-view and directory slice on the isolated stack.

Two modes, both read-only against the site:

  --snapshot   Record hashes/counts of site configuration, permission rows,
               patient names and write-fence events before deployment
               (private state dir).
  (default)    Compare against that snapshot, then verify target identity,
               image revision, both approved Patients, the native Patient read
               permission matrix, the approved write capability surface, the
               seeded + created directory payload, and the authenticated
               patient-view HTTP matrix (DOC/NUR allowed; REC, guest, unknown
               alias and POST refused; editable DOC=True/NUR=False on the
               created alias). Confirms the read-only matrix appends no fence
               events and writes redacted evidence plus a private log line.

No site configuration, permission, account, fixture or fence row is written.
Credentials are read from the private state directory and never printed.
"""

from __future__ import annotations

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
ENDPOINT = "/api/method/clinic_ui.api.patients.view"
ALLOWED_ALIAS = "WS-SIT-1"
EXPECTED_MARLEY_COMMIT = "dda107b26e3a0fe1dbd65c7257371235de054a04"
EXPECTED_PATIENT = {
    "workspace_patient_id": "WS-SIT-1",
    "display_name": "SITSYNTH Alpha",
    "first_name": "SITSYNTH",
    "last_name": "Alpha",
    "sex": "Female",
    "dob": "1990-01-01",
    "status": "Active",
}
EXPECTED_READ = {
    "sit.doc@example.invalid": True,
    "sit.nur@example.invalid": True,
    "sit.rec@example.invalid": False,
}
EXPECTED_CREATED = {
    "first_name": "UISYNTH",
    "last_name": "BravoTwo",
    "patient_name": "UISYNTH BravoTwo",
    "sex": "Female",
    "dob": "1995-03-12",
    "status": "Active",
    "owner": "sit.doc@example.invalid",
}


def patient_shape_exact(payload: dict, expected: dict | None = None) -> bool:
    """Exact reviewed fields plus the authoritative ``modified`` string.

    ``editable`` is computed per actor by the server. The verifier requires an
    explicit boolean and does not infer write authority from the presence of
    the field.
    """

    expected = EXPECTED_PATIENT if expected is None else expected
    modified = payload.get("modified")
    return (
        isinstance(modified, str)
        and bool(modified)
        and isinstance(payload.get("editable"), bool)
        and {
            key: value
            for key, value in payload.items()
            if key not in ("modified", "editable")
        }
        == expected
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


def in_backend(script: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    args = ["exec", "-T"]
    for key, value in (env or {}).items():
        args += ["-e", f"{key}={value}"]
    args += ["backend", "bash", "-c", script]
    return compose(*args)


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


SNAPSHOT_PROBE = (
    "cd sites && ../env/bin/python - <<'PY'\n"
    "import hashlib, json, os, sqlite3\n"
    "import frappe\n"
    "from clinic_ui.core import fence_store, write_fence\n"
    "from frappe.installer import get_site_config_path\n"
    f"frappe.init(site='{SITE}', sites_path='.')\n"
    "frappe.connect()\n"
    "rows = frappe.get_all(\n"
    "    'DocPerm',\n"
    "    filters={'parent': ['in', ['Patient', 'Gender']]},\n"
    "    fields=['parent', 'role', 'permlevel', 'read', 'write', 'create', 'delete',\n"
    "            'submit', 'cancel', 'amend', 'report', 'export', 'print', 'email',\n"
    "            'share', 'select', 'import'],\n"
    "    order_by='parent, role, permlevel',\n"
    ")\n"
    "conf_bytes = open(get_site_config_path(), 'rb').read()\n"
    "counts = {dt: frappe.db.count(dt) for dt in [\n"
    "    'Patient', 'User', 'Has Role', 'DocPerm', 'Custom DocPerm', 'Role', 'Customer',\n"
    "]}\n"
    "patient_names = frappe.get_all('Patient', pluck='name', order_by='name')\n"
    "private = frappe.get_site_path('private')\n"
    "store_db = os.path.join(private, fence_store.STORE_DIRNAME, write_fence.STORE_FILENAME)\n"
    "connection = sqlite3.connect(f'file:{store_db}?mode=ro', uri=True)\n"
    "events = connection.execute(\n"
    "    'SELECT sequence, actor, kind, operation_key, state FROM events ORDER BY sequence'\n"
    ").fetchall()\n"
    "connection.close()\n"
    "print(json.dumps({\n"
    "    'site_config_sha256': hashlib.sha256(conf_bytes).hexdigest(),\n"
    "    'patient_gender_perms_sha256': hashlib.sha256(\n"
    "        json.dumps(rows, sort_keys=True, default=str).encode()\n"
    "    ).hexdigest(),\n"
    "    'permission_row_count': len(rows),\n"
    "    'counts': counts,\n"
    "    'patient_names': patient_names,\n"
    "    'fence_event_count': len(events),\n"
    "    'fence_events_sha256': hashlib.sha256(\n"
    "        json.dumps(events, default=str).encode()\n"
    "    ).hexdigest(),\n"
    "}))\n"
    "PY"
)


def snapshot() -> dict:
    run = in_backend(SNAPSHOT_PROBE)
    if run.returncode != 0:
        raise SystemExit("snapshot probe failed")
    return json.loads(run.stdout)


def write_private(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
    os.chmod(path, 0o600)


def main() -> int:
    credentials = json.loads((STATE_DIR / "credentials.json").read_text())
    if credentials["site"] != SITE or credentials["origin"] != ORIGIN:
        print("refusing: private credentials do not describe the isolated site")
        return 2

    pre_path = STATE_DIR / "evidence/patient-view-baseline.json"
    if "--snapshot" in sys.argv[1:]:
        payload = snapshot()
        payload["captured_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        write_private(pre_path, payload)
        print(f"snapshot written: {pre_path} (mode 600)")
        return 0

    if not pre_path.exists():
        print("refusing: no pre-deployment snapshot; run --snapshot first")
        return 2
    pre = json.loads(pre_path.read_text())

    # Target identity and pinned hardening.
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

    identity = in_backend(
        "set -e; "
        f"test -d sites/{SITE}; "
        "test ! -e sites/marley.local; "
        "test \"$(find sites -mindepth 1 -maxdepth 1 -type d -printf '%f\\n')\" = "
        f"{SITE}; "
        "test \"$(jq -r '.default_site' sites/common_site_config.json)\" = "
        f"{SITE}; echo ok"
    )
    record(
        "exact target site identity",
        identity.returncode == 0,
        {"exit": identity.returncode, "stdout": identity.stdout.strip()},
    )

    revisions_run = in_backend("cat /opt/sit/revisions.txt")
    revisions = dict(
        line.split("=", 1) for line in revisions_run.stdout.splitlines() if "=" in line
    )
    record(
        "deployed image is the pinned hardened build",
        revisions_run.returncode == 0
        and revisions.get("marley_commit") == EXPECTED_MARLEY_COMMIT
        and revisions.get("base_digest", "").startswith("sha256:")
        and revisions.get("clinic_source_rev") not in (None, "", "unknown"),
        {
            "base_digest": revisions.get("base_digest", "")[:20] + "…",
            "marley_commit_matches": revisions.get("marley_commit") == EXPECTED_MARLEY_COMMIT,
            "clinic_source_rev": revisions.get("clinic_source_rev"),
        },
    )

    # Site config, permission rows and fixture counts must be unchanged.
    post = snapshot()
    unchanged_fields = [
        "site_config_sha256",
        "patient_gender_perms_sha256",
        "permission_row_count",
        "counts",
        "patient_names",
        "fence_event_count",
        "fence_events_sha256",
    ]
    unchanged = {field: pre.get(field) == post.get(field) for field in unchanged_fields}
    record(
        "site config, permission rows and fixture counts unchanged",
        all(unchanged.values()),
        unchanged,
    )

    probe = (
        "cd sites && ../env/bin/python - <<'PY'\n"
        "import json\n"
        "import frappe\n"
        f"frappe.init(site='{SITE}', sites_path='.')\n"
        "frappe.connect()\n"
        "patients = frappe.get_all('Patient', fields=[\n"
        "    'name', 'first_name', 'last_name', 'patient_name', 'sex', 'dob', 'status', 'owner',\n"
        "], order_by='name')\n"
        "by_name = {row['name']: row for row in patients}\n"
        "patient = by_name.get('SITSYNTH Alpha', {})\n"
        "created = by_name.get('UISYNTH Bravo', {})\n"
        f"expected_email_read = {EXPECTED_READ!r}\n"
        "has_read = {\n"
        "    name: {\n"
        "        email: bool(frappe.has_permission('Patient', ptype='read', doc=name, user=email))\n"
        "        for email in expected_email_read\n"
        "    }\n"
        "    for name in by_name\n"
        "}\n"
        "print(json.dumps({\n"
        "    'patient_count': len(patients),\n"
        "    'patient_names': sorted(by_name),\n"
        f"    'first_name_exact': patient.get('first_name') == {'SITSYNTH'!r},\n"
        f"    'last_name_exact': patient.get('last_name') == {'Alpha'!r},\n"
        f"    'patient_name_exact': patient.get('patient_name') == {'SITSYNTH Alpha'!r},\n"
        f"    'sex_exact': patient.get('sex') == {'Female'!r},\n"
        f"    'dob_exact': str(patient.get('dob')) == {'1990-01-01'!r},\n"
        f"    'status_exact': patient.get('status') == {'Active'!r},\n"
        f"    'created_owner_exact': created.get('owner') == {EXPECTED_CREATED['owner']!r},\n"
        f"    'created_first_name_exact': created.get('first_name') == {EXPECTED_CREATED['first_name']!r},\n"
        f"    'created_last_name_exact': created.get('last_name') == {EXPECTED_CREATED['last_name']!r},\n"
        f"    'created_patient_name_exact': created.get('patient_name') == {EXPECTED_CREATED['patient_name']!r},\n"
        f"    'created_sex_exact': created.get('sex') == {EXPECTED_CREATED['sex']!r},\n"
        f"    'created_dob_exact': str(created.get('dob')) == {EXPECTED_CREATED['dob']!r},\n"
        f"    'created_status_exact': created.get('status') == {EXPECTED_CREATED['status']!r},\n"
        "    'native_read': has_read,\n"
        "    'native_read_matches': (\n"
        "        set(has_read) == {'SITSYNTH Alpha', 'UISYNTH Bravo'}\n"
        "        and all(value == expected_email_read for value in has_read.values())\n"
        "    ),\n"
        "}))\n"
        "PY"
    )
    fixture_run = in_backend(probe)
    fixture = json.loads(fixture_run.stdout) if fixture_run.returncode == 0 else {}
    record(
        "approved patient fixtures unchanged (seeded + created)",
        fixture_run.returncode == 0
        and fixture.get("patient_count") == 2
        and fixture.get("patient_names") == ["SITSYNTH Alpha", "UISYNTH Bravo"]
        and all(
            fixture.get(key)
            for key in (
                "first_name_exact",
                "last_name_exact",
                "patient_name_exact",
                "sex_exact",
                "dob_exact",
                "status_exact",
                "created_owner_exact",
                "created_first_name_exact",
                "created_last_name_exact",
                "created_patient_name_exact",
                "created_sex_exact",
                "created_dob_exact",
                "created_status_exact",
            )
        ),
        {
            "patient_count": fixture.get("patient_count"),
            "patient_names": fixture.get("patient_names"),
            "seeded_fields_exact": all(
                fixture.get(key)
                for key in (
                    "first_name_exact",
                    "last_name_exact",
                    "patient_name_exact",
                    "sex_exact",
                    "dob_exact",
                    "status_exact",
                )
            ),
            "created_fields_exact": all(
                fixture.get(key)
                for key in (
                    "created_owner_exact",
                    "created_first_name_exact",
                    "created_last_name_exact",
                    "created_patient_name_exact",
                    "created_sex_exact",
                    "created_dob_exact",
                    "created_status_exact",
                )
            ),
        },
    )
    record(
        "native Patient read permission matrix for both patients (DOC/NUR yes, REC no)",
        fixture_run.returncode == 0
        and fixture.get("native_read_matches"),
        fixture.get("native_read"),
    )

    # Read-only capability surface.
    accounts = {u["clinic_code"]: u for u in credentials["users"]}
    jars = {}

    def login(code):
        account = accounts[code]
        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        status, _, body = request(
            opener,
            "/api/method/login",
            method="POST",
            data=urllib.parse.urlencode(
                {"usr": account["email"], "pwd": account["password"]}
            ).encode(),
        )
        if status == 200 and b"Logged In" in body:
            jars[code] = opener
        return status

    record(
        "all three synthetic actors can log in",
        all(login(code) == 200 for code in ("DOC", "NUR", "REC")),
        {code: code in jars for code in ("DOC", "NUR", "REC")},
    )

    boot_status, boot_headers, boot_body = request(
        jars["DOC"], "/api/method/clinic_ui.api.session.bootstrap"
    )
    boot = json.loads(boot_body).get("message", {}) if boot_status == 200 else {}
    capabilities = boot.get("capabilities") or {}
    enabled_writes = {
        name for name, state in capabilities.items() if state == "enabled"
    }
    record(
        "DOC write capabilities are exactly the two approved patient writes",
        boot_status == 200
        and capabilities
        and enabled_writes == {"write.patient.create", "write.patient.update"}
        and all(
            value in ("read_only", "enabled") for value in capabilities.values()
        ),
        {
            "status": boot_status,
            "capabilities": len(capabilities),
            "enabled": sorted(enabled_writes),
            "states": sorted(set(capabilities.values())),
        },
    )

    expected_capabilities = set(
        json.loads((REPO_ROOT / "contracts/capabilities.json").read_text())
    )
    record(
        "capability allowlist is complete",
        set(capabilities) == expected_capabilities,
        {"missing": sorted(expected_capabilities - set(capabilities))},
    )

    # Directory: seeded allowlist plus the committed created receipt.
    directory_path = "/api/method/clinic_ui.api.directory.synthetic_patients"
    directory_items = {}
    for code in ("DOC", "NUR", "REC"):
        status, _, body = request(jars[code], directory_path)
        items = (
            json.loads(body).get("message", {}).get("items", [])
            if status == 200
            else []
        )
        directory_items[code] = items
    doc_items = directory_items["DOC"]

    def directory_shape_exact(items):
        return (
            len(items) == 2
            and items[0].get("workspace_patient_id") == ALLOWED_ALIAS
            and items[0].get("display_name") == "SITSYNTH Alpha"
            and str(items[1].get("workspace_patient_id", "")).startswith("wpa1_")
            and items[1].get("display_name") == "UISYNTH BravoTwo"
            and all(
                set(item) == {"workspace_patient_id", "display_name"}
                for item in items
            )
        )

    record(
        "DOC and NUR directory include the seeded and created patients without native IDs",
        directory_shape_exact(directory_items["DOC"])
        and directory_items["NUR"] == directory_items["DOC"],
        {
            "items": {code: len(items) for code, items in directory_items.items()},
            "shape_exact": directory_shape_exact(doc_items),
            "native_id_withheld": all(
                "native_patient_id" not in item for item in doc_items
            ),
        },
    )
    record(
        "REC directory is empty and guest directory is refused",
        directory_items["REC"] == []
        and request(urllib.request.build_opener(), directory_path)[0] == 403,
        {
            "rec_items": len(directory_items["REC"]),
            "guest_status": request(
                urllib.request.build_opener(), directory_path
            )[0],
        },
    )

    created_alias = doc_items[1]["workspace_patient_id"] if directory_shape_exact(doc_items) else ""
    expected_created_view = {
        "workspace_patient_id": created_alias,
        "display_name": EXPECTED_CREATED["patient_name"],
        "first_name": EXPECTED_CREATED["first_name"],
        "last_name": EXPECTED_CREATED["last_name"],
        "sex": EXPECTED_CREATED["sex"],
        "dob": EXPECTED_CREATED["dob"],
        "status": EXPECTED_CREATED["status"],
    }

    # HTTP matrix for the new patient-view endpoint.
    view_status, view_headers, view_body = request(
        jars["DOC"], f"{ENDPOINT}?{urllib.parse.urlencode({'workspace_patient_id': ALLOWED_ALIAS})}"
    )
    view_payload = json.loads(view_body).get("message", {}) if view_status == 200 else {}
    record(
        "DOC can view the allowlisted patient with exact display fields",
        view_status == 200
        and patient_shape_exact(view_payload)
        and "native_patient_id" not in view_payload
        and header(view_headers, "Cache-Control") == "no-store",
        {
            "status": view_status,
            "shape_exact": patient_shape_exact(view_payload),
            "modified_present": isinstance(view_payload.get("modified"), str),
            "editable": view_payload.get("editable"),
            "native_id_absent": "native_patient_id" not in view_payload,
            "cache_control": header(view_headers, "Cache-Control"),
        },
    )

    nur_status, _, nur_body = request(
        jars["NUR"], f"{ENDPOINT}?{urllib.parse.urlencode({'workspace_patient_id': ALLOWED_ALIAS})}"
    )
    nur_payload = json.loads(nur_body).get("message", {}) if nur_status == 200 else {}
    record(
        "NUR can view the same allowlisted patient",
        nur_status == 200 and patient_shape_exact(nur_payload),
        {"status": nur_status, "shape_exact": patient_shape_exact(nur_payload)},
    )

    rec_status, _, _ = request(
        jars["REC"], f"{ENDPOINT}?{urllib.parse.urlencode({'workspace_patient_id': ALLOWED_ALIAS})}"
    )
    record(
        "REC is denied by native Patient permission",
        rec_status == 403,
        {"status": rec_status},
    )

    guest = urllib.request.build_opener()
    guest_status, _, _ = request(
        guest, f"{ENDPOINT}?{urllib.parse.urlencode({'workspace_patient_id': ALLOWED_ALIAS})}"
    )
    record("guest is denied", guest_status == 403, {"status": guest_status})

    # Cross-clinician read of the created patient, with display-only editable.
    def created_view(opener):
        status, headers, body = request(
            opener,
            f"{ENDPOINT}?{urllib.parse.urlencode({'workspace_patient_id': created_alias})}",
        )
        payload = json.loads(body).get("message", {}) if status == 200 else {}
        return status, headers, payload

    created_doc_status, created_doc_headers, created_doc = created_view(jars["DOC"])
    record(
        "DOC creator can read the created patient with editable=True",
        bool(created_alias)
        and created_doc_status == 200
        and patient_shape_exact(created_doc, expected_created_view)
        and created_doc.get("editable") is True
        and "native_patient_id" not in created_doc
        and header(created_doc_headers, "Cache-Control") == "no-store",
        {
            "status": created_doc_status,
            "shape_exact": patient_shape_exact(created_doc, expected_created_view),
            "editable": created_doc.get("editable"),
            "native_id_absent": "native_patient_id" not in created_doc,
        },
    )
    created_nur_status, _, created_nur = created_view(jars["NUR"])
    record(
        "NUR can read the created patient cross-clinician with editable=False",
        bool(created_alias)
        and created_nur_status == 200
        and patient_shape_exact(created_nur, expected_created_view)
        and created_nur.get("editable") is False,
        {
            "status": created_nur_status,
            "shape_exact": patient_shape_exact(created_nur, expected_created_view),
            "editable": created_nur.get("editable"),
        },
    )
    created_rec_status, _, _ = created_view(jars["REC"])
    created_guest_status, _, _ = created_view(urllib.request.build_opener())
    record(
        "REC and guest are denied the created patient",
        created_rec_status == 403 and created_guest_status == 403,
        {"rec_status": created_rec_status, "guest_status": created_guest_status},
    )

    for alias in ("NOT-AN-ALIAS", "SITSYNTH Alpha", ""):
        status, _, body = request(
            jars["DOC"],
            f"{ENDPOINT}?{urllib.parse.urlencode({'workspace_patient_id': alias})}",
        )
        record(
            "DOC cannot read a non-allowlisted/malformed alias",
            status == 403,
            {"alias_length": len(alias), "status": status, "body_length": len(body)},
        )

    csrf = boot.get("csrfToken") or ""
    post_status, _, post_body = request(
        jars["DOC"],
        ENDPOINT,
        method="POST",
        data=b"",
        headers={"X-Frappe-CSRF-Token": csrf} if csrf else {},
    )
    record(
        "authenticated POST with valid CSRF refused",
        bool(csrf) and post_status in (403, 405),
        {"status": post_status, "body_length": len(post_body)},
    )

    final = snapshot()
    fence_unchanged = (
        final.get("fence_event_count") == post.get("fence_event_count")
        and final.get("fence_events_sha256") == post.get("fence_events_sha256")
    )
    record(
        "all read-only directory/view reads appended no fence events",
        fence_unchanged,
        {
            "fence_event_count": final.get("fence_event_count"),
            "fence_events_sha256": final.get("fence_events_sha256"),
        },
    )

    evidence_dir = STATE_DIR / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(evidence_dir, 0o700)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    evidence_path = evidence_dir / f"patient-view-verify-{stamp}.json"
    write_private(
        evidence_path,
        {
            "site": SITE,
            "origin": ORIGIN,
            "revisions": {
                "base_digest": revisions.get("base_digest"),
                "marley_commit": revisions.get("marley_commit"),
                "clinic_source_rev": revisions.get("clinic_source_rev"),
            },
            "checks": results,
        },
    )

    failed = [item["check"] for item in results if not item["ok"]]
    print(f"evidence: {evidence_path} (mode 600)")
    print(f"checks: {len(results) - len(failed)}/{len(results)} passed")
    with (STATE_DIR / "deployment.log").open("a") as log:
        log.write(
            f"{stamp} patient-view verify exit={'0' if not failed else '1'} "
            f"passed={len(results) - len(failed)}/{len(results)} "
            f"failed={failed} evidence={evidence_path}\n"
        )
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
