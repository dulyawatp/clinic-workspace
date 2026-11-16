#!/usr/bin/env python3
"""Verify the isolated local SIT stack from the host.

Reads private credentials from $MARLEY_SIT_STATE_DIR (never prints them),
exercises the loopback frontend, and asserts site identity, deployed versions,
the /clinic shell and assets, session bootstrap, the synthetic allowlist, and
read-only capabilities. Writes a redacted evidence JSON to the private state
directory and appends one line to the private deployment log.

Exit status 0 only if every check passes.
"""

from __future__ import annotations

import http.cookiejar
import json
import os
import re
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
SITE_PROJECT = "marley-sit"
EXPECTED_BASE_DIGEST = "sha256:88654c9f5fb8e47266b492932270239d4d7c3edd4af3966d0768616c0cd20a42"
EXPECTED_MARIADB_DIGEST = "sha256:d36f18d65a0459510b8b0f643b226a6d299e3206486e2b64ff20754a6d291707"
EXPECTED_REDIS_DIGEST = "sha256:3f9c6e23ed6bea3b41d0ef709fc164c1a0b7c00d5a21a60e0e9c33ada5496881"
EXPECTED_MARLEY_COMMIT = "dda107b26e3a0fe1dbd65c7257371235de054a04"

# Fictional fixture created by provision.py. Identity is asserted field by
# field; these are the only expected values.
EXPECTED_PATIENT_FIRST_NAME = "SITSYNTH"
EXPECTED_PATIENT_LAST_NAME = "Alpha"
EXPECTED_PATIENT_SEX = "Female"
EXPECTED_PATIENT_DOB = "1990-01-01"
EXPECTED_PATIENT_NAME = "SITSYNTH Alpha"
EXPECTED_PATIENT_STATUS = "Active"

# --- write-contract wave (2026-11-16): site-gated expectations -------------
#
# The verifier is the redeploy gate for the write-contract wave. The five
# previously approved writes must stay enabled exactly as before; every other
# write capability must be read_only unless the site config opens its family
# gate. The mapped expectations below mirror the approved design and the app's
# own gates (policy role codes + bootstrap gate flags); they are site-config
# dependent on purpose so the same verifier serves every SIT window.

GATE_CONFIG_KEYS = {
    "patient": "clinic_ui_native_patient_write_enabled",
    "appointment": "clinic_ui_native_appointment_write_enabled",
    "encounter": "clinic_ui_native_encounter_write_enabled",
    "procedure": "clinic_ui_native_procedure_write_enabled",
    "stock": "clinic_ui_native_stock_write_enabled",
    "purchase": "clinic_ui_native_purchase_write_enabled",
    "finance": "clinic_ui_native_finance_write_enabled",
    "admin": "clinic_ui_native_admin_write_enabled",
}

# capability -> (family gate, clinic role codes that admit it)
WRITE_CAPABILITY_GATES = {
    "write.patient.create": ("patient", ("DOC", "NUR")),
    "write.patient.update": ("patient", ("DOC", "NUR")),
    "write.appointment.book": ("appointment", ("DOC", "REC")),
    "write.appointment.reschedule": ("appointment", ("DOC", "REC")),
    "write.appointment.cancel": ("appointment", ("DOC", "REC")),
    "write.appointment.no_show": ("appointment", ("DOC", "REC")),
    "write.appointment.checkin": ("appointment", ("DOC", "REC")),
    "write.encounter.draft": ("encounter", ("DOC", "NUR")),
    "write.medication.order": ("encounter", ("DOC", "NUR")),
    "write.lab.order": ("encounter", ("DOC", "NUR")),
    "write.encounter.complete": ("encounter", ("DOC",)),
    "write.encounter.amend": ("encounter", ("DOC",)),
    "write.procedure.complete": ("procedure", ("DOC",)),
    "write.stock.consume": ("procedure", ("DOC",)),
    "write.stock.receive": ("stock", ("PHA",)),
    "write.stock.transfer": ("stock", ("PHA",)),
    "write.stock.adjust": ("stock", ("ADM",)),
    "write.purchase_order.draft": ("purchase", ("PHA", "MGR")),
    "write.purchase_order.submit": ("purchase", ("PHA", "MGR")),
    "write.invoice.submit": ("finance", ("CAS",)),
    "write.payment.receive": ("finance", ("CAS",)),
    "write.admin.user": ("admin", ("ADM",)),
    "write.admin.master": ("admin", ("ADM",)),
}

# Cut or unimplemented surfaces: read_only whatever the site config says.
PERMANENT_READ_ONLY = frozenset(
    {
        "write.lab.review",
        "write.photo.consent",
        "write.photo.upload",
        "write.photo.link",
        "write.photo.annotate",
        "write.invoice.void_amend",
        "write.payment.refund",
    }
)

APPROVED_ENABLED_WRITES = frozenset(
    {
        "write.patient.create",
        "write.patient.update",
        "write.appointment.book",
        "write.appointment.reschedule",
        "write.appointment.checkin",
    }
)

# The seven clinic role codes the site role map must configure (policy.py).
SUPPORTED_ROLE_CODES = ("DOC", "REC", "NUR", "PHA", "CAS", "MGR", "ADM")
EXPECTED_ROLE_MAP = {
    "DOC": ["Physician"],
    "NUR": ["Nursing User"],
    "REC": ["Healthcare Administrator"],
    "PHA": ["Stock Manager"],
    "CAS": ["Accounts User"],
    "MGR": ["Purchase User"],
    "ADM": ["Item Manager"],
}

# Approved durable budget registry (write_budgets.py); duplicated here on
# purpose so a registry edit cannot silently change the approved write volume.
APPROVED_BUDGETS = {
    "appointment_cancel": 2,
    "appointment_no_show": 1,
    "encounter_draft": 2,
    "encounter_complete": 1,
    "encounter_amend": 1,
    "procedure_complete": 1,
    "lab_order": 1,
    "stock_receive": 1,
    "stock_transfer": 1,
    "stock_adjust": 1,
    "purchase_order_draft": 1,
    "purchase_order_submit": 1,
    "purchase_receipt": 1,
    "invoice_submit": 1,
    "payment_receive": 1,
    "admin_user": 4,
    "admin_master": 12,
}

# Kinds the durable fence may legitimately carry (write_fence.OPERATION_KINDS).
APPROVED_OPERATION_KINDS = frozenset(
    {
        "create",
        "update",
        "book",
        "reschedule",
        "checkin",
        "appointment_cancel",
        "appointment_no_show",
        "encounter_draft",
        "encounter_complete",
        "encounter_amend",
        "procedure_complete",
        "admin_user",
        "admin_master",
        "stock_receive",
        "stock_transfer",
        "stock_adjust",
        "purchase_order_draft",
        "purchase_order_submit",
        "purchase_receipt",
        "invoice_submit",
        "payment_receive",
    }
)

# 24 baseline events after the five approved writes (4 events per operation).
# Later SIT windows append 4 events per executed write; override at run time.
EXPECTED_FENCE_EVENTS = int(os.environ.get("MARLEY_SIT_EXPECTED_FENCE_EVENTS", "24"))

# POST-only surface the wave added. Every listed function must be whitelisted
# for POST, part of the session bootstrap's module set, and never allow_guest.
EXPECTED_POST_SURFACE = {
    "clinic_ui.api.appointments_cancel_write": ("cancel", "no_show"),
    "clinic_ui.api.encounters_write": ("save_draft", "complete", "amend", "ack"),
    "clinic_ui.api.procedures_write": ("complete", "ack"),
    "clinic_ui.api.admin_write": ("create_user", "edit_master", "ack"),
    "clinic_ui.api.stock_write": ("receive", "transfer", "adjust", "ack"),
    "clinic_ui.api.purchase_write": (
        "save_po_draft",
        "submit_po",
        "receive",
        "ack",
    ),
    "clinic_ui.api.finance_write": (
        "save_invoice_draft",
        "submit_invoice",
        "receive_payment",
        "ack",
    ),
}


def _gate_open(value) -> bool:
    return value in (1, True, "1")


def expected_enabled_writes(gates, role):
    """Return the capabilities that must be enabled for ``role``.

    A capability is enabled only when its family gate flag is on and the role
    is admitted by the family's policy set; everything else must be read_only.
    """

    enabled = set()
    for capability, (family, roles) in WRITE_CAPABILITY_GATES.items():
        if role in roles and _gate_open(gates.get(family)):
            enabled.add(capability)
    return enabled


def capability_problems(capabilities, expected_ids, gates, role):
    """Return named problems for the bootstrap capability map, or ``[]``."""

    problems = []
    if not isinstance(capabilities, dict):
        return ["capabilities are not a map"]
    expected_ids = set(expected_ids)
    present = set(capabilities)
    if expected_ids - present:
        problems.append(f"missing capabilities: {sorted(expected_ids - present)}")
    if present - expected_ids:
        problems.append(f"unexpected capabilities: {sorted(present - expected_ids)}")
    bad_states = sorted(
        name
        for name, state in capabilities.items()
        if state not in ("read_only", "enabled")
    )
    if bad_states:
        problems.append(f"unknown capability states: {bad_states}")
    enabled = {
        name for name, state in capabilities.items() if state == "enabled"
    }
    expected_enabled = expected_enabled_writes(gates, role)
    missing = sorted(expected_enabled - enabled)
    extra = sorted(enabled - expected_enabled)
    if missing:
        problems.append(f"gated writes not enabled: {missing}")
    if extra:
        problems.append(f"writes enabled outside their gate: {extra}")
    leaked = sorted(PERMANENT_READ_ONLY & enabled)
    if leaked:
        problems.append(f"permanently read_only writes enabled: {leaked}")
    if role == "DOC" and not APPROVED_ENABLED_WRITES <= enabled:
        problems.append(
            f"approved writes regressed: {sorted(APPROVED_ENABLED_WRITES - enabled)}"
        )
    return problems


def fence_ledger_problems(ledger, expected_events, approved_budgets):
    """Return named problems for the durable fence/budget read-back, or ``[]``."""

    if not isinstance(ledger, dict):
        return ["fence ledger unreadable"]
    problems = []
    if ledger.get("ready") is not True:
        problems.append("fence store is not ready")
    if ledger.get("events") != expected_events:
        problems.append(
            f"fence events {ledger.get('events')} != expected {expected_events}"
        )
    if ledger.get("unresolved") != 0:
        problems.append(f"fence has {ledger.get('unresolved')} unresolved keys")
    kinds = ledger.get("kinds")
    if not isinstance(kinds, list):
        problems.append("fence kinds unreadable")
    else:
        unknown = sorted(set(kinds) - APPROVED_OPERATION_KINDS)
        if unknown:
            problems.append(f"fence carries unknown kinds: {unknown}")
    budgets = ledger.get("budgets")
    if budgets != approved_budgets:
        problems.append("budget registry read-back differs from the approved set")
    if ledger.get("unknown_kind_refused") is not True:
        problems.append("budget_for() accepted an unknown kind")
    return problems


def role_map_problems(role_map):
    """Return named problems for the site role map, or ``[]``."""

    expected_map = EXPECTED_ROLE_MAP
    if not isinstance(role_map, dict):
        return ["role map is not a map"]
    problems = []
    if set(role_map) != set(expected_map):
        problems.append(
            f"role map codes {sorted(role_map)} != {sorted(expected_map)}"
        )
    seen_roles = {}
    for code, roles in role_map.items():
        if not isinstance(roles, list) or not roles:
            problems.append(f"{code} has no native role list")
            continue
        if any(not isinstance(role, str) or not role.strip() for role in roles):
            problems.append(f"{code} has a malformed native role")
            continue
        if len(set(roles)) != len(roles):
            problems.append(f"{code} repeats a native role")
        for role in roles:
            if role in seen_roles and seen_roles[role] != code:
                problems.append(f"{role} maps to both {seen_roles[role]} and {code}")
            seen_roles[role] = code
    for code, expected in expected_map.items():
        if list(role_map.get(code) or ()) != expected:
            problems.append(f"{code} mapping is not the approved mapping")
    return problems


def post_surface_problems(surface):
    """Return named problems for the new POST-only API surface, or ``[]``."""

    if not isinstance(surface, dict):
        return ["API surface unreadable"]
    problems = []
    for module, functions in EXPECTED_POST_SURFACE.items():
        entry = surface.get(module)
        if not isinstance(entry, dict):
            problems.append(f"{module} is not installed")
            continue
        for function in functions:
            state = entry.get(function)
            if not isinstance(state, dict):
                problems.append(f"{module}.{function} is missing")
                continue
            if state.get("whitelisted") is not True:
                problems.append(f"{module}.{function} is not whitelisted")
            if list(state.get("methods") or ()) != ["POST"]:
                problems.append(
                    f"{module}.{function} methods {state.get('methods')} != POST"
                )
            if state.get("guest") is not False:
                problems.append(f"{module}.{function} allows guest")
    return problems


def selfcheck() -> int:
    """Run the pure verifier functions against fixtures; 0 only when all pass."""

    good_gates = {family: 0 for family in GATE_CONFIG_KEYS}
    good_gates["patient"] = 1
    good_gates["appointment"] = 1
    capability_ids = list(WRITE_CAPABILITY_GATES) + sorted(PERMANENT_READ_ONLY)
    good_capabilities = {name: "read_only" for name in capability_ids}
    for name in expected_enabled_writes(good_gates, "DOC"):
        good_capabilities[name] = "enabled"

    good_ledger = {
        "ready": True,
        "events": 24,
        "unresolved": 0,
        "kinds": ["create", "update", "book", "checkin"],
        "budgets": dict(APPROVED_BUDGETS),
        "unknown_kind_refused": True,
    }
    good_surface = {
        module: {
            function: {"whitelisted": True, "methods": ["POST"], "guest": False}
            for function in functions
        }
        for module, functions in EXPECTED_POST_SURFACE.items()
    }

    cases = [
        ("capabilities match the gates", not capability_problems(
            good_capabilities, capability_ids, good_gates, "DOC"
        )),
        ("an ungated write is caught", bool(capability_problems(
            dict(good_capabilities, **{"write.stock.adjust": "enabled"}),
            capability_ids,
            good_gates,
            "DOC",
        ))),
        ("a missing approved write is caught", bool(capability_problems(
            dict(good_capabilities, **{"write.appointment.checkin": "read_only"}),
            capability_ids,
            good_gates,
            "DOC",
        ))),
        ("a missing capability id is caught", bool(capability_problems(
            good_capabilities, capability_ids + ["write.new.thing"], good_gates, "DOC"
        ))),
        ("ledger read-back matches", not fence_ledger_problems(
            good_ledger, 24, APPROVED_BUDGETS
        )),
        ("an unresolved key is caught", bool(fence_ledger_problems(
            dict(good_ledger, unresolved=1), 24, APPROVED_BUDGETS
        ))),
        ("event drift is caught", bool(fence_ledger_problems(
            good_ledger, 28, APPROVED_BUDGETS
        ))),
        ("budget drift is caught", bool(fence_ledger_problems(
            good_ledger, 24, dict(APPROVED_BUDGETS, admin_master=99)
        ))),
        ("unknown fence kind is caught", bool(fence_ledger_problems(
            dict(good_ledger, kinds=["create", "made_up"]), 24, APPROVED_BUDGETS
        ))),
        ("role map parses", not role_map_problems(
            {code: list(roles) for code, roles in EXPECTED_ROLE_MAP.items()}
        )),
        ("a missing role code is caught", bool(role_map_problems(
            {code: list(roles) for code, roles in EXPECTED_ROLE_MAP.items()
             if code != "ADM"}
        ))),
        ("an ambiguous native role is caught", bool(role_map_problems(
            dict(
                {
                    code: list(roles)
                    for code, roles in EXPECTED_ROLE_MAP.items()
                    if code != "NUR"
                },
                NUR=["Physician"],
            )
        ))),
        ("embedded backend probes compile", probes_compile()),
        ("POST surface whitelists", not post_surface_problems(good_surface)),
        ("a GET-allowed write is caught", bool(post_surface_problems(
            {module: dict(entry) for module, entry in good_surface.items()}
            | {
                "clinic_ui.api.stock_write": dict(
                    good_surface["clinic_ui.api.stock_write"],
                    transfer={
                        "whitelisted": True,
                        "methods": ["GET", "POST"],
                        "guest": False,
                    },
                )
            }
        ))),
    ]
    failures = [name for name, ok in cases if not ok]
    for name, ok in cases:
        print(f"{'PASS' if ok else 'FAIL'} selfcheck {name}")
    print(f"selfcheck: {len(cases) - len(failures)}/{len(cases)} passed")
    return 0 if not failures else 1


results: list[dict] = []


def record(name: str, ok: bool, detail: object) -> None:
    results.append({"check": name, "ok": bool(ok), "detail": detail})
    print(f"{'PASS' if ok else 'FAIL'} {name}: {detail}")


def compose(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "compose", "-p", SITE_PROJECT, "-f", str(COMPOSE_FILE), *args],
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


def gate_probe_script() -> str:
    """Backend probe: the site's eight write-gate flags, never secrets."""

    gate_keys = sorted(GATE_CONFIG_KEYS.values())
    return (
        "cd sites && ../env/bin/python - <<'PY'\n"
        "import json\n"
        f"conf = json.load(open({(SITE + '/site_config.json')!r}))\n"
        f"print(json.dumps({{key: conf.get(key) for key in {gate_keys!r}}}))\n"
        "PY"
    )


def ledger_probe_script() -> str:
    """Backend probe: durable fence state and the budget registry read-back."""

    return (
        "cd sites && ../env/bin/python - <<'PY'\n"
        "import json, os, sqlite3\n"
        "import frappe\n"
        "from clinic_ui.core import fence_store, write_budgets, write_fence\n"
        f"frappe.init(site={SITE!r}, sites_path='.')\n"
        "frappe.connect()\n"
        "private = frappe.get_site_path('private')\n"
        "ledger = fence_store.open_store(\n"
        "    os.path.join(private, fence_store.STORE_DIRNAME),\n"
        "    os.path.join(private, fence_store.ANCHOR_FILENAME),\n"
        ")\n"
        "store_db = os.path.join(\n"
        "    private, fence_store.STORE_DIRNAME, write_fence.STORE_FILENAME\n"
        ")\n"
        "connection = sqlite3.connect('file:%s?mode=ro' % store_db, uri=True)\n"
        "events = connection.execute('SELECT COUNT(*) FROM events').fetchone()[0]\n"
        "unresolved_sql = (\n"
        "    \"SELECT COUNT(*) FROM (SELECT 1 FROM events AS e\"\n"
        "    \" WHERE e.deployment_id = (SELECT deployment_id FROM meta)\"\n"
        "    \" AND e.state != 'COMMITTED'\"\n"
        "    \" AND e.sequence = (SELECT MAX(e2.sequence) FROM events AS e2\"\n"
        "    \"   WHERE e2.deployment_id = e.deployment_id\"\n"
        "    \"     AND e2.actor = e.actor AND e2.kind = e.kind\"\n"
        "    \"     AND e2.operation_key = e.operation_key))\"\n"
        ")\n"
        "unresolved = connection.execute(unresolved_sql).fetchone()[0]\n"
        "kinds = [row[0] for row in connection.execute(\n"
        "    'SELECT DISTINCT kind FROM events ORDER BY kind'\n"
        ")]\n"
        "connection.close()\n"
        "budgets = {\n"
        "    kind: write_budgets.budget_for(kind) for kind in write_budgets.ALL_KINDS\n"
        "}\n"
        "try:\n"
        "    write_budgets.budget_for('not_a_write_kind')\n"
        "    refused = False\n"
        "except ValueError:\n"
        "    refused = True\n"
        "print(json.dumps({\n"
        "    'ready': ledger.ready(),\n"
        "    'events': events,\n"
        "    'unresolved': unresolved,\n"
        "    'kinds': kinds,\n"
        "    'budgets': budgets,\n"
        "    'unknown_kind_refused': refused,\n"
        "}))\n"
        "PY"
    )


def surface_probe_script() -> str:
    """Backend probe: whitelist state of the new POST-only API surface."""

    return (
        "cd sites && ../env/bin/python - <<'PY'\n"
        "import importlib, json\n"
        "import frappe\n"
        f"expected = {EXPECTED_POST_SURFACE!r}\n"
        "surface = {}\n"
        "for module_name, functions in expected.items():\n"
        "    module = importlib.import_module(module_name)\n"
        "    entry = {}\n"
        "    for name in functions:\n"
        "        function = getattr(module, name, None)\n"
        "        entry[name] = {\n"
        "            'whitelisted': function in frappe.whitelisted,\n"
        "            'methods': list(\n"
        "                frappe.allowed_http_methods_for_whitelisted_func.get(\n"
        "                    function\n"
        "                )\n"
        "                or ()\n"
        "            ),\n"
        "            'guest': function in frappe.guest_methods,\n"
        "        }\n"
        "    surface[module_name] = entry\n"
        "print(json.dumps(surface))\n"
        "PY"
    )


def _probe_body(script: str) -> str:
    return script.split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]


def probes_compile() -> bool:
    """True when every embedded backend probe is valid Python."""

    try:
        for script in (
            gate_probe_script(),
            ledger_probe_script(),
            surface_probe_script(),
        ):
            compile(_probe_body(script), "backend-probe", "exec")
        return True
    except SyntaxError:
        return False


def header(headers, name: str) -> str | None:
    for key, value in headers.items():
        if key.lower() == name.lower():
            return value
    return None


def _parse_apps(text: str, site: str | None = None) -> list[str]:
    text = text.strip()
    if text.startswith("["):
        return [str(item) for item in json.loads(text)]
    if text.startswith("{"):
        data = json.loads(text)
        if site and site in data:
            return [str(item) for item in data[site]]
        for key in ("apps", "message", "data"):
            if key in data:
                value = data[key]
                return [str(item) for item in value] if isinstance(value, list) else []
        return [str(item) for item in data]
    return [line.split()[0] for line in text.splitlines() if line.strip()]


def request(
    opener, path: str, *, method: str = "GET", data: bytes | None = None, headers: dict | None = None
):
    req = urllib.request.Request(ORIGIN + path, data=data, method=method, headers=headers or {})
    try:
        with opener.open(req, timeout=30) as response:
            return response.status, response.headers, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.headers, error.read()


def wait_for_shell(deadline_seconds: int = 180) -> tuple[int, object, bytes]:
    opener = urllib.request.build_opener()
    deadline = time.time() + deadline_seconds
    last = (0, {}, b"")
    while time.time() < deadline:
        last = request(opener, "/clinic")
        if last[0] == 200:
            return last
        time.sleep(3)
    return last


def main() -> int:
    credentials = json.loads((STATE_DIR / "credentials.json").read_text())
    if credentials["site"] != SITE or credentials["origin"] != ORIGIN:
        print("refusing: private credentials do not describe this isolated site")
        return 2

    expected_capabilities = json.loads(
        (REPO_ROOT / "contracts/capabilities.json").read_text()
    )

    # --- Compose configuration and isolation -------------------------------
    config_run = compose("config", "-q")
    record("compose config validates", config_run.returncode == 0, f"exit={config_run.returncode}")

    config_json = subprocess.run(
        ["docker", "compose", "-p", SITE_PROJECT, "-f", str(COMPOSE_FILE), "config", "--format", "json"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    model = json.loads(config_json.stdout)
    published = []
    for service_name, service in model.get("services", {}).items():
        for port in service.get("ports", []) or []:
            published.append((service_name, port.get("host_ip"), port.get("published")))
    loopback_only = all(host_ip == "127.0.0.1" for _, host_ip, _ in published)
    record(
        "published ports bind loopback only",
        loopback_only and len(published) == 1,
        [{"service": s, "host_ip": h, "published": p} for s, h, p in published],
    )
    record("compose project name", model.get("name") == "marley-sit", model.get("name"))

    volume_run = subprocess.run(
        ["docker", "volume", "ls", "-q", "--filter", "name=marley-sit-"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    volumes = sorted(v for v in volume_run.stdout.split() if v)
    expected_volumes = {
        "marley-sit-sites",
        "marley-sit-logs",
        "marley-sit-db-data",
        "marley-sit-redis-queue-data",
    }
    record(
        "distinct named volumes",
        set(volumes) == expected_volumes,
        volumes,
    )

    preflight = subprocess.run(
        [sys.executable, str(REPO_ROOT / "deploy/local-sit/bin/preflight.py")],
        capture_output=True,
        text=True,
        timeout=180,
    )
    record(
        "project and resources owned by marley-sit (preflight)",
        preflight.returncode == 0,
        {"exit": preflight.returncode, "lines": preflight.stdout.strip().splitlines()[-1:]},
    )

    gate_probe = in_backend(gate_probe_script())
    gate_values = json.loads(gate_probe.stdout) if gate_probe.returncode == 0 else {}
    gates = {
        family: gate_values.get(key) for family, key in GATE_CONFIG_KEYS.items()
    }

    # --- Site identity and versions inside the running stack --------------
    identity = in_backend(
        "set -e; "
        f"test -d sites/{SITE}; "
        "test ! -e sites/marley.local; "
        "test \"$(find sites -mindepth 1 -maxdepth 1 -type d -printf '%f\\n' | sort | tr '\\n' ' ')\" = "
        f"\"{SITE} \"; "
        "test \"$(jq -r '.default_site' sites/common_site_config.json)\" = "
        f"{SITE}; "
        "echo ok"
    )
    record(
        "site identity is the new local site only",
        identity.returncode == 0,
        {"exit": identity.returncode, "stdout": identity.stdout.strip()},
    )

    list_apps = in_backend(f"bench --site {SITE} list-apps --format json")
    apps_ok = list_apps.returncode == 0
    apps = _parse_apps(list_apps.stdout, SITE) if apps_ok else []
    record(
        "installed apps on site",
        apps_ok and {"frappe", "erpnext", "healthcare", "clinic_ui"} <= set(apps),
        {"apps": sorted(apps), "clinic_photos_installed": "clinic_photos" in apps},
    )

    versions_run = in_backend(
        "/home/frappe/frappe-bench/env/bin/python -c "
        "\"import json,frappe,erpnext,healthcare;"
        "print(json.dumps({'frappe':frappe.__version__,"
        "'erpnext':erpnext.__version__,'healthcare':healthcare.__version__}))\""
    )
    versions = json.loads(versions_run.stdout) if versions_run.returncode == 0 else {}
    record(
        "actual versions inside the running stack",
        versions_run.returncode == 0
        and versions.get("frappe", "").startswith("16.")
        and versions.get("erpnext", "").startswith("16.")
        and versions.get("healthcare") == "16.6.1",
        versions,
    )

    revisions_run = in_backend("cat /opt/sit/revisions.txt")
    revisions = dict(
        line.split("=", 1)
        for line in revisions_run.stdout.strip().splitlines()
        if "=" in line
    )
    record(
        "pinned image revisions recorded",
        revisions_run.returncode == 0
        and revisions.get("marley_commit") == EXPECTED_MARLEY_COMMIT
        and revisions.get("marley_ref") == "v16.6.1",
        {
            "base_image": revisions.get("base_image"),
            "marley_ref": revisions.get("marley_ref"),
            "marley_commit_matches_expected": revisions.get("marley_commit") == EXPECTED_MARLEY_COMMIT,
            "clinic_source_rev": revisions.get("clinic_source_rev"),
        },
    )

    expected_images = {
        "db": f"mariadb:11.8@{EXPECTED_MARIADB_DIGEST}",
        "redis-cache": f"redis:6.2-alpine@{EXPECTED_REDIS_DIGEST}",
    }
    digest_checks = {}
    for service, expected_image in expected_images.items():
        configured = model["services"][service].get("image")
        container_id = subprocess.run(
            [
                "docker", "ps", "-q",
                "--filter", f"label=com.docker.compose.project={SITE_PROJECT}",
                "--filter", f"label=com.docker.compose.service={service}",
            ],
            capture_output=True, text=True, timeout=60,
        ).stdout.strip().splitlines()
        container_image = None
        image_id = None
        if container_id:
            container_image = subprocess.run(
                ["docker", "inspect", "--format", "{{.Image}}", container_id[0]],
                capture_output=True, text=True, timeout=60,
            ).stdout.strip()
            image_id = subprocess.run(
                ["docker", "image", "inspect", "--format", "{{.Id}}", expected_image],
                capture_output=True, text=True, timeout=60,
            ).stdout.strip()
        digest_checks[service] = {
            "configured": configured == expected_image,
            "running_matches_pinned_image": bool(image_id) and container_image == image_id,
            "digest_pinned": expected_image,
        }
    record(
        "MariaDB and Redis pinned by immutable digest and running",
        all(item["configured"] and item["running_matches_pinned_image"] for item in digest_checks.values()),
        digest_checks,
    )

    provenance_files = {
        "dockerfile_base_pinned": f"FROM frappe/erpnext:v16.52.1@{EXPECTED_BASE_DIGEST}"
        in (REPO_ROOT / "deploy/local-sit/backend.Dockerfile").read_text(),
        "dockerfile_marley_commit_enforced": EXPECTED_MARLEY_COMMIT
        in (REPO_ROOT / "deploy/local-sit/backend.Dockerfile").read_text(),
        "compose_mariadb_pinned": EXPECTED_MARIADB_DIGEST
        in COMPOSE_FILE.read_text(),
        "compose_redis_pinned": EXPECTED_REDIS_DIGEST in COMPOSE_FILE.read_text(),
    }
    record(
        "image provenance pinned in deployment files",
        all(provenance_files.values()),
        provenance_files,
    )

    # --- HTTP: shell, assets, API -----------------------------------------
    shell_status, shell_headers, shell_body = wait_for_shell()
    shell_text = shell_body.decode("utf-8", "replace")
    record(
        "/clinic shell served",
        shell_status == 200
        and header(shell_headers, "Cache-Control") == "no-store,no-cache,must-revalidate,max-age=0",
        {
            "status": shell_status,
            "cache_control": header(shell_headers, "Cache-Control"),
            "content_type": header(shell_headers, "Content-Type"),
        },
    )

    asset_urls = sorted(set(re.findall(r"/assets/clinic_ui/ui/assets/[A-Za-z0-9._-]+\.(?:js|css)", shell_text)))
    record(
        "shell references manifest-built assets",
        len(asset_urls) >= 2,
        {"assets": asset_urls},
    )

    guest = urllib.request.build_opener()
    asset_checks = []
    for url in asset_urls:
        status, headers, _ = request(guest, url)
        asset_checks.append(
            {
                "path": url,
                "status": status,
                "content_type": header(headers, "Content-Type"),
            }
        )
    record(
        "assets resolve over loopback",
        bool(asset_checks)
        and all(
            item["status"] == 200
            and item["content_type"]
            and ("javascript" in item["content_type"] or "css" in item["content_type"])
            for item in asset_checks
        ),
        asset_checks,
    )

    deep_status, _, deep_body = request(guest, "/clinic/patients/WS-SIT-1")
    record(
        "/clinic deep link serves the same shell",
        deep_status == 200 and b"clinic_ui/ui/assets/" in deep_body,
        {"status": deep_status},
    )

    guest_status, guest_headers, guest_body = request(
        guest, "/api/method/clinic_ui.api.session.bootstrap"
    )
    guest_payload = json.loads(guest_body)
    record(
        "anonymous bootstrap fails closed with 401",
        guest_status == 401
        and guest_payload == {"code": "AUTH_EXPIRED"}
        and header(guest_headers, "Cache-Control") == "no-store",
        {
            "status": guest_status,
            "body": guest_payload,
            "cache_control": header(guest_headers, "Cache-Control"),
        },
    )

    guest_dir_status, _, guest_dir_body = request(
        guest, "/api/method/clinic_ui.api.directory.synthetic_patients"
    )
    record(
        "anonymous directory request denied",
        guest_dir_status == 403,
        {"status": guest_dir_status, "body_length": len(guest_dir_body)},
    )

    # --- Authenticated session and read-only allowlist --------------------
    doc_account = next(u for u in credentials["users"] if u["clinic_code"] == "DOC")
    jar = http.cookiejar.CookieJar()
    authed = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    login_status, _, login_body = request(
        authed,
        "/api/method/login",
        method="POST",
        data=urllib.parse.urlencode(
            {"usr": doc_account["email"], "pwd": doc_account["password"]}
        ).encode(),
    )
    record(
        "synthetic account login",
        login_status == 200 and b"Logged In" in login_body,
        {"status": login_status, "email": doc_account["email"]},
    )

    boot_status, boot_headers, boot_body = request(
        authed, "/api/method/clinic_ui.api.session.bootstrap"
    )
    boot = json.loads(boot_body).get("message", {}) if boot_status == 200 else {}
    capabilities = boot.get("capabilities") or {}
    record(
        "authenticated bootstrap",
        boot_status == 200
        and boot.get("mode") == "live"
        and boot.get("user", {}).get("roles") == ["DOC"]
        and bool(boot.get("csrfToken"))
        and len(boot.get("sessionEpoch", "")) == 64
        and header(boot_headers, "Cache-Control") == "no-store",
        {
            "status": boot_status,
            "mode": boot.get("mode"),
            "roles": boot.get("user", {}).get("roles"),
            "capability_count": len(capabilities),
            "csrf_present": bool(boot.get("csrfToken")),
            "epoch_length": len(boot.get("sessionEpoch", "")),
        },
    )
    enabled_writes = {
        name for name, state in capabilities.items() if state == "enabled"
    }
    record(
        "bootstrap publishes the frozen capability contract (39 ids)",
        set(capabilities) == set(expected_capabilities)
        and len(capabilities) == 39
        and all(
            state in ("read_only", "enabled") for state in capabilities.values()
        ),
        {
            "count": len(capabilities),
            "writes": len(
                [name for name in capabilities if name.startswith("write.")]
            ),
            "missing": sorted(set(expected_capabilities) - set(capabilities)),
            "unexpected": sorted(set(capabilities) - set(expected_capabilities)),
            "states": sorted(set(capabilities.values())),
        },
    )
    capability_issues = capability_problems(
        capabilities, expected_capabilities, gates, "DOC"
    )
    record(
        "DOC write capabilities follow the site gates",
        not capability_issues,
        {
            "role": "DOC",
            "gates": gates,
            "enabled": sorted(enabled_writes),
            "expected_enabled": sorted(expected_enabled_writes(gates, "DOC")),
            "problems": capability_issues,
        },
    )

    # Method restrictions are checked with an authenticated session AND a valid
    # CSRF token, so a 403 cannot be explained by a missing session or token.
    csrf = boot.get("csrfToken") or ""
    csrf_headers = {"X-Frappe-CSRF-Token": csrf} if csrf else {}
    csrf_post_statuses = {}
    for path in (
        "/api/method/clinic_ui.api.session.bootstrap",
        "/api/method/clinic_ui.api.directory.synthetic_patients",
    ):
        status, _, body = request(
            authed, path, method="POST", data=b"", headers=csrf_headers
        )
        csrf_post_statuses[path.rsplit(".", 1)[-1]] = {
            "status": status,
            "body_length": len(body),
        }
    record(
        "authenticated POST with valid CSRF refused on both endpoints",
        bool(csrf)
        and all(
            item["status"] in (403, 405)
            for item in csrf_post_statuses.values()
        ),
        csrf_post_statuses,
    )

    post_status, _, _ = request(
        guest, "/api/method/clinic_ui.api.session.bootstrap", method="POST", data=b""
    )
    record(
        "anonymous POST refused on bootstrap",
        post_status in (403, 405),
        {"status": post_status},
    )

    dir_status, _, dir_body = request(
        authed, "/api/method/clinic_ui.api.directory.synthetic_patients"
    )
    directory = json.loads(dir_body).get("message", {}) if dir_status == 200 else {}
    items = directory.get("items") or []
    record(
        "synthetic directory resolves seeded and created patients",
        dir_status == 200
        and len(items) == 2
        and items[0].get("workspace_patient_id") == "WS-SIT-1"
        and str(items[1].get("workspace_patient_id", "")).startswith("wpa1_")
        and items[1].get("display_name") == "UISYNTH BravoTwo"
        and all(
            set(item) == {"workspace_patient_id", "display_name"}
            for item in items
        ),
        {
            "status": dir_status,
            "item_count": len(items),
            "workspace_ids": [item.get("workspace_patient_id") for item in items],
            "native_id_withheld": all(
                "native_patient_id" not in item for item in items
            ),
        },
    )

    # --- Durable fence ledger, budgets and the new POST surface -----------
    ledger_probe = in_backend(ledger_probe_script())
    ledger = (
        json.loads(ledger_probe.stdout) if ledger_probe.returncode == 0 else {}
    )
    ledger_issues = fence_ledger_problems(
        ledger, EXPECTED_FENCE_EVENTS, APPROVED_BUDGETS
    )
    record(
        "durable fence and budget registry match the approved state",
        not ledger_issues,
        {
            "events": ledger.get("events"),
            "expected_events": EXPECTED_FENCE_EVENTS,
            "unresolved": ledger.get("unresolved"),
            "kinds": ledger.get("kinds"),
            "problems": ledger_issues,
        },
    )

    surface_probe = in_backend(surface_probe_script())
    surface = (
        json.loads(surface_probe.stdout) if surface_probe.returncode == 0 else {}
    )
    surface_issues = post_surface_problems(surface)
    record(
        "new write API surface is POST-only and whitelisted",
        not surface_issues,
        {
            "modules": sorted(surface),
            "functions": {
                module: sorted(entry) for module, entry in surface.items()
            },
            "problems": surface_issues,
        },
    )

    # --- Exact synthetic identity and configuration -----------------------
    probe = (
        "cd sites && ../env/bin/python - <<'PY'\n"
        "import json, os\n"
        "import frappe\n"
        f"frappe.init(site='{SITE}', sites_path='.')\n"
        "frappe.connect()\n"
        "expected = {\n"
        "    'sit.doc@example.invalid': ['Physician'],\n"
        "    'sit.nur@example.invalid': ['Nursing User'],\n"
        "    'sit.rec@example.invalid': ['Healthcare Administrator'],\n"
        "}\n"
        "system = {'Administrator', 'Guest'}\n"
        "users = {\n"
        "    row['name']: sorted(frappe.get_all(\n"
        "        'Has Role',\n"
        "        filters={'parent': row['name'], 'parenttype': 'User'},\n"
        "        pluck='role',\n"
        "    ))\n"
        "    for row in frappe.get_all('User', fields=['name'], filters={'name': ['not in', list(system)]})\n"
        "}\n"
        "conf = frappe.conf\n"
        "allow = conf.get('clinic_ui_synthetic_patients') or []\n"
        "patients = frappe.get_all('Patient', fields=[\n"
        "    'name', 'first_name', 'last_name', 'patient_name', 'sex', 'dob', 'status', 'owner',\n"
        "])\n"
        "entry = allow[0] if len(allow) == 1 else {}\n"
        "by_name = {row['name']: row for row in patients}\n"
        "patient = by_name.get('SITSYNTH Alpha', {})\n"
        "created = by_name.get('UISYNTH Bravo', {})\n"
        "expected_role_map = {\n"
        "    'DOC': ['Physician'],\n"
        "    'NUR': ['Nursing User'],\n"
        "    'REC': ['Healthcare Administrator'],\n"
        "    'PHA': ['Stock Manager'],\n"
        "    'CAS': ['Accounts User'],\n"
        "    'MGR': ['Purchase User'],\n"
        "    'ADM': ['Item Manager'],\n"
        "}\n"
        "print(json.dumps({\n"
        "    'non_system_user_count': len(users),\n"
        "    'users_exact': sorted(users) == sorted(expected),\n"
        "    'users_enabled': all(frappe.db.get_value('User', email, 'enabled') == 1 for email in expected),\n"
        "    'user_roles_exact': all(users.get(email) == sorted(expected[email]) for email in expected),\n"
        "    'patient_count': len(patients),\n"
        "    'patient_names': sorted(by_name),\n"
        "    'allowlist_len': len(allow),\n"
        "    'workspace_alias_exact': entry.get('workspace_patient_id') == 'WS-SIT-1',\n"
        "    'allowlist_matches_patient': entry.get('native_patient_id') == patient.get('name'),\n"
        "    'allowlist_keys_exact': set(entry) == {'workspace_patient_id', 'native_patient_id'},\n"
        f"    'patient_first_name_exact': patient.get('first_name') == {EXPECTED_PATIENT_FIRST_NAME!r},\n"
        f"    'patient_last_name_exact': patient.get('last_name') == {EXPECTED_PATIENT_LAST_NAME!r},\n"
        f"    'patient_name_exact': patient.get('patient_name') == {EXPECTED_PATIENT_NAME!r},\n"
        f"    'patient_sex_exact': patient.get('sex') == {EXPECTED_PATIENT_SEX!r},\n"
        f"    'patient_dob_exact': str(patient.get('dob')) == {EXPECTED_PATIENT_DOB!r},\n"
        f"    'patient_status_exact': patient.get('status') == {EXPECTED_PATIENT_STATUS!r},\n"
        "    'created_owner_exact': created.get('owner') == 'sit.doc@example.invalid',\n"
        "    'created_first_name_exact': created.get('first_name') == 'UISYNTH',\n"
        "    'created_last_name_exact': created.get('last_name') == 'BravoTwo',\n"
        "    'created_patient_name_exact': created.get('patient_name') == 'UISYNTH BravoTwo',\n"
        "    'created_sex_exact': created.get('sex') == 'Female',\n"
        "    'created_dob_exact': str(created.get('dob')) == '1995-03-12',\n"
        "    'created_status_exact': created.get('status') == 'Active',\n"
        "    'directory_display_name_matches': os.environ.get('SIT_DIRECTORY_DISPLAY_NAME', '') == patient.get('patient_name'),\n"
        "    'config_enabled': conf.get('clinic_ui_enabled') in (1, True, '1'),\n"
        "    'config_site_identity': conf.get('clinic_ui_synthetic_site') == 'marley-sit.local',\n"
        "    'role_map_exact': conf.get('clinic_ui_role_map') == expected_role_map,\n"
        "    'role_map': conf.get('clinic_ui_role_map'),\n"
        "    'epoch_length': len(conf.get('clinic_ui_epoch_secret') or ''),\n"
        "}))\n"
        "PY"
    )
    probe_run = in_backend(
        probe,
        env={
            "SIT_DIRECTORY_DISPLAY_NAME": (
                items[0].get("display_name", "") if items else ""
            )
        },
    )
    data = json.loads(probe_run.stdout) if probe_run.returncode == 0 else {}
    record(
        "exact non-system synthetic users and roles",
        probe_run.returncode == 0
        and data.get("non_system_user_count") == 3
        and data.get("users_exact")
        and data.get("users_enabled")
        and data.get("user_roles_exact"),
        {
            "count": data.get("non_system_user_count"),
            "emails_and_roles_exact": data.get("users_exact") and data.get("user_roles_exact"),
            "all_enabled": data.get("users_enabled"),
        },
    )

    record(
        "exact provisioned fixture plus approved created patient",
        probe_run.returncode == 0
        and data.get("patient_count") == 2
        and data.get("patient_names") == ["SITSYNTH Alpha", "UISYNTH Bravo"]
        and data.get("allowlist_len") == 1
        and data.get("allowlist_keys_exact")
        and data.get("workspace_alias_exact")
        and data.get("allowlist_matches_patient")
        and data.get("patient_first_name_exact")
        and data.get("patient_last_name_exact")
        and data.get("patient_name_exact")
        and data.get("patient_sex_exact")
        and data.get("patient_dob_exact")
        and data.get("patient_status_exact")
        and data.get("created_owner_exact")
        and data.get("created_first_name_exact")
        and data.get("created_last_name_exact")
        and data.get("created_patient_name_exact")
        and data.get("created_sex_exact")
        and data.get("created_dob_exact")
        and data.get("created_status_exact")
        and data.get("directory_display_name_matches"),
        {
            "patient_count": data.get("patient_count"),
            "patient_names": data.get("patient_names"),
            "allowlist_len": data.get("allowlist_len"),
            "workspace_alias_exact": data.get("workspace_alias_exact"),
            "allowlist_points_to_the_seeded_patient": data.get("allowlist_matches_patient"),
            "fixture_fields_exact": all(
                data.get(key)
                for key in (
                    "patient_first_name_exact",
                    "patient_last_name_exact",
                    "patient_name_exact",
                    "patient_sex_exact",
                    "patient_dob_exact",
                    "patient_status_exact",
                )
            ),
            "created_fields_exact": all(
                data.get(key)
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
            "directory_display_name_matches": data.get("directory_display_name_matches"),
        },
    )

    role_map_issues = role_map_problems(data.get("role_map"))
    record(
        "site config identity, role map and epoch secret",
        probe_run.returncode == 0
        and data.get("config_enabled")
        and data.get("config_site_identity")
        and not role_map_issues
        and (data.get("epoch_length") or 0) >= 32,
        {
            "enabled": data.get("config_enabled"),
            "site_identity_exact": data.get("config_site_identity"),
            "role_codes": sorted(data.get("role_map") or {}),
            "role_map_problems": role_map_issues,
            "epoch_length": data.get("epoch_length"),
        },
    )

    # --- Evidence ---------------------------------------------------------
    evidence_dir = STATE_DIR / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(evidence_dir, 0o700)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    evidence_path = evidence_dir / f"verify-{stamp}.json"
    fd = os.open(evidence_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(
            {
                "site": SITE,
                "origin": ORIGIN,
                "versions": versions,
                "revisions": revisions,
                "checks": results,
            },
            handle,
            indent=2,
        )
    os.chmod(evidence_path, 0o600)

    failed = [item["check"] for item in results if not item["ok"]]
    print(f"evidence: {evidence_path} (mode 600)")
    print(f"checks: {len(results) - len(failed)}/{len(results)} passed")

    with (STATE_DIR / "deployment.log").open("a") as log:
        log.write(
            f"{stamp} verify exit={'0' if not failed else '1'} "
            f"passed={len(results) - len(failed)}/{len(results)} "
            f"failed={failed} evidence={evidence_path}\n"
        )

    return 0 if not failed else 1


if __name__ == "__main__":
    if "--selfcheck" in sys.argv[1:]:
        raise SystemExit(selfcheck())
    raise SystemExit(main())
