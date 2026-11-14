"""Lab-only role setup plan and read-only verification for clinic-photo-accept.

Approved shape: create exactly five Role records (Clinic Lab Reception, Clinic Lab
Pharmacy, Clinic Lab Cashier, Clinic Lab Manager, Clinic Lab Administrator), add no
DocPerm, Custom DocPerm, User Permission, Role Profile or settings row, reuse the
existing Physician and Nursing User roles untouched, and create seven fresh synthetic
System Users (one role each, no welcome email). No Administrator or System Manager
grant, ever.

Nothing here is executed by the change that adds it: ``plan`` only validates a
read-only inventory and writes an artifact; ``verify`` performs reads only and
refuses without ``--live``. Credentials and plan artifacts live in a private
directory (mode 0700) outside any repository; the credentials file is created
exclusively with mode 0600 and its values are never printed or digested.

Commands::

    cd frappe-apps/clinic_photos
    python3 -m clinic_photos.tests.lab_roles plan \
        --inventory /private/lab/inventory.json --control-dir /private/lab \
        --run-nonce <16+ hex> --live

    cd BENCH/sites
    PYTHONPATH=../apps/clinic_photos python3 -B -m clinic_photos.tests.lab_roles verify \
        --site clinic-photo-accept.local --control-dir /private/lab \
        --photo-file <protected photo File> --private-file <private File> --live

Deliberate limits: Frappe's built-in File grants (for example ``All``) are untouched,
so this procedure never claims the five roles can read *no* file rows. It checks the
named protected photo, the named private file and the named accounts only.
"""

import argparse
import json
import os
import secrets
import stat
from pathlib import Path

SITE = "clinic-photo-accept.local"
EMAIL_DOMAIN = "clinic-photo-accept.test"
ROLE_NAMES = (
    "Clinic Lab Reception",
    "Clinic Lab Pharmacy",
    "Clinic Lab Cashier",
    "Clinic Lab Manager",
    "Clinic Lab Administrator",
)
REUSED_ROLES = ("Physician", "Nursing User")
FORBIDDEN_ROLES = frozenset({"Administrator", "System Manager"})
USER_TYPE = "System User"
MAX_JSON_BYTES = 256 * 1024

# actor key -> role; role names are the approved explicit ones.
ACTORS = (
    ("physician", "Physician", True),
    ("nursing_user", "Nursing User", True),
    ("clinic_lab_reception", ROLE_NAMES[0], False),
    ("clinic_lab_pharmacy", ROLE_NAMES[1], False),
    ("clinic_lab_cashier", ROLE_NAMES[2], False),
    ("clinic_lab_manager", ROLE_NAMES[3], False),
    ("clinic_lab_administrator", ROLE_NAMES[4], False),
)
GUEST = "guest"
CHECKS = ("login", "read_photo", "read_private", "list_photo", "write_photo")
APP_ROOT = Path(__file__).resolve().parents[2]


class LabConflict(Exception):
    """Fail-closed refusal; the message carries a code, never a secret."""


def email_for(run_nonce, key):
    return f"lab.{run_nonce}.{key}@{EMAIL_DOMAIN}"


# ---------------------------------------------------------------------------
# Setup plan (pure)
# ---------------------------------------------------------------------------
def build_plan(inventory, run_nonce):
    """Validate the read-only inventory and return the exact approved plan.

    Inventory: ``{"site": str, "roles": [existing Role names],
    "users": [existing User names]}``.
    """
    if not isinstance(inventory, dict):
        raise LabConflict("inventory_invalid")
    if inventory.get("site") != SITE:
        raise LabConflict("site_mismatch")
    nonce = str(run_nonce or "")
    if len(nonce) < 16 or any(character not in "0123456789abcdef" for character in nonce):
        raise LabConflict("run_nonce_invalid")

    existing_roles = set(inventory.get("roles") or ())
    existing_users = set(inventory.get("users") or ())
    if not all(isinstance(value, str) for value in existing_roles | existing_users):
        raise LabConflict("inventory_invalid")

    for role in REUSED_ROLES:
        if role not in existing_roles:
            raise LabConflict(f"reused_role_missing.{role}")
    for role in ROLE_NAMES:
        if role in existing_roles:
            raise LabConflict(f"role_exists.{role}")
        if role in FORBIDDEN_ROLES:
            raise LabConflict(f"role_forbidden.{role}")

    roles = tuple({"doctype": "Role", "action": "insert", "role": role} for role in ROLE_NAMES)
    users = []
    emails = set()
    for key, role, clinician in ACTORS:
        email = email_for(nonce, key)
        if email in existing_users or email in emails:
            raise LabConflict(f"user_exists.{key}")
        emails.add(email)
        users.append({
            "doctype": "User",
            "action": "insert",
            "key": key,
            "email": email,
            "role": role,
            "clinician": clinician,
            "payload": {
                "doctype": "User",
                "email": email,
                "first_name": "Synthetic Clinic Lab",
                "enabled": 1,
                "user_type": USER_TYPE,
                "send_welcome_email": 0,
                "roles": [{"role": role}],
                "flags": {"no_welcome_mail": True},
            },
        })
    plan = {"schema": 1, "site": SITE, "run_nonce": nonce,
            "roles": list(roles), "users": users}
    assert_plan(plan)
    return plan


def assert_plan(plan):
    """Fail closed unless the plan is exactly five Roles and seven Users."""
    if plan.get("site") != SITE:
        raise LabConflict("site_mismatch")
    roles = plan.get("roles") or []
    users = plan.get("users") or []
    if [entry.get("role") for entry in roles] != list(ROLE_NAMES):
        raise LabConflict("role_set")
    if len(users) != len(ACTORS):
        raise LabConflict("user_count")
    doctypes = set()
    for entry in roles:
        doctypes.add(entry.get("doctype"))
        if entry.get("action") != "insert" or entry.get("role") in FORBIDDEN_ROLES:
            raise LabConflict("role_scope")
    for entry in users:
        doctypes.add(entry.get("doctype"))
        payload = entry.get("payload") or {}
        if entry.get("role") in FORBIDDEN_ROLES or payload.get("roles") != [{"role": entry.get("role")}]:
            raise LabConflict(f"user_scope.{entry.get('key')}")
        if payload.get("user_type") != USER_TYPE or payload.get("send_welcome_email") != 0:
            raise LabConflict(f"user_payload.{entry.get('key')}")
        if "password" in payload:
            raise LabConflict("payload_secret")
    if doctypes != {"Role", "User"}:
        raise LabConflict("doctype_scope")
    return True


# ---------------------------------------------------------------------------
# Private artifacts outside the repository
# ---------------------------------------------------------------------------
def assert_external(path):
    """Refuse artifacts inside the app or inside any enclosing git repository."""
    target = Path(path).resolve()
    if target == APP_ROOT or APP_ROOT in target.parents:
        raise LabConflict("artifact_inside_app")
    for parent in target.parents:
        if (parent / ".git").exists():
            raise LabConflict("artifact_inside_repo")
    info = os.lstat(target)
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise LabConflict("control_dir_invalid")
    if info.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise LabConflict("control_dir_mode")
    if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
        raise LabConflict("control_dir_owner")
    return target


def write_json(path, document):
    """Exclusive 0600 write; refuses any existing artifact."""
    if os.path.lexists(path):
        raise LabConflict(f"artifact_exists.{Path(path).name}")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        os.write(descriptor,
                 json.dumps(document, separators=(",", ":"), sort_keys=True).encode())
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return path


def read_json(path, limit=MAX_JSON_BYTES):
    info = os.lstat(path)
    if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
        raise LabConflict(f"artifact_invalid.{Path(path).name}")
    with open(path, "rb") as handle:
        return json.loads(handle.read().decode("utf-8"))


def write_credentials(path, plan):
    """Create the 0600 credentials file for the planned accounts. Never prints."""
    records = [
        {"key": entry["key"], "user": entry["email"], "role": entry["role"],
         "password": secrets.token_urlsafe(32)}
        for entry in plan["users"]
    ]
    write_json(path, {"schema": 1, "records": records})
    return len(records)


def read_credentials(path):
    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise LabConflict("credentials_not_regular")
    if info.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise LabConflict("credentials_mode")
    if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
        raise LabConflict("credentials_owner")
    document = read_json(path)
    records = document.get("records") if isinstance(document, dict) else None
    if not isinstance(records, list) or len(records) != len(ACTORS):
        raise LabConflict("credentials_actors")
    for record in records:
        if not all(isinstance(record.get(field), str) and record.get(field)
                   for field in ("key", "user", "role", "password")):
            raise LabConflict("credentials_schema")
    return tuple(records)


# ---------------------------------------------------------------------------
# Observation matrix (pure)
# ---------------------------------------------------------------------------
def expected():
    matrix = {}
    for key, _role, clinician in ACTORS:
        matrix[key] = {"login": True, "read_photo": clinician, "read_private": clinician,
                       "list_photo": clinician, "write_photo": False}
    matrix[GUEST] = {"read_photo": False, "read_private": False, "list_photo": False,
                     "write_photo": False}
    return matrix


def evaluate(observations, invariants=()):
    """Return failure tokens; missing/extra/non-boolean observations fail closed."""
    matrix = expected()
    failures, seen = [], set()
    for (actor, check), value in observations.items():
        if actor not in matrix or check not in matrix[actor]:
            failures.append(f"unexpected.{actor}.{check}")
            continue
        seen.add((actor, check))
        if not isinstance(value, bool):
            failures.append(f"non_boolean.{actor}.{check}")
        elif value is not matrix[actor][check]:
            failures.append(f"mismatch.{actor}.{check}")
    for actor, checks in matrix.items():
        for check in checks:
            if (actor, check) not in seen:
                failures.append(f"missing.{actor}.{check}")
    return tuple(sorted(failures) + sorted(invariants))


# ---------------------------------------------------------------------------
# Apply: exactly the planned inserts, one transaction, no welcome mail
# ---------------------------------------------------------------------------
AUTOMATIC_ROLES = frozenset({"All", "Guest", "System User"})


def assert_live_inventory(frappe):
    """Re-check the plan against the live site inside the transaction."""
    roles = set(frappe.get_all("Role", pluck="name"))
    for role in REUSED_ROLES:
        if role not in roles:
            raise LabConflict(f"reused_role_missing.{role}")
    for role in ROLE_NAMES:
        if role in roles:
            raise LabConflict(f"role_exists.{role}")


def apply_plan(frappe, plan, records):
    """Insert five Roles and seven Users in one transaction; print nothing."""
    assert_plan(plan)
    if {record["user"] for record in records} != {entry["email"] for entry in plan["users"]}:
        raise LabConflict("credentials_unplanned")
    by_email = {record["user"]: record for record in records}
    assert_live_inventory(frappe)
    for entry in plan["users"]:
        if frappe.db.exists("User", entry["email"]):
            raise LabConflict(f"user_exists.{entry['key']}")

    frappe.set_user("Administrator")
    try:
        for entry in plan["roles"]:
            frappe.get_doc({
                "doctype": "Role",
                "role_name": entry["role"],
                "desk_access": 1,
            }).insert(ignore_permissions=True)
        for entry in plan["users"]:
            payload = dict(entry["payload"])
            payload.pop("flags", None)
            payload["new_password"] = by_email[entry["email"]]["password"]
            user = frappe.get_doc(payload)
            user.flags.no_welcome_mail = True
            user.flags.ignore_permissions = True
            user.insert(ignore_permissions=True)
        frappe.db.commit()
    except Exception:
        frappe.db.rollback()
        raise

    return {
        "status": "committed",
        "roles_created": frappe.db.count("Role", {"name": ["in", list(ROLE_NAMES)]}),
        "users_created": frappe.db.count("User", {"name": ["in", [e["email"] for e in plan["users"]]]}),
        "docperm_for_new_roles": frappe.db.count("DocPerm", {"role": ["in", list(ROLE_NAMES)]}),
        "custom_docperm_for_new_roles": frappe.db.count(
            "Custom DocPerm", {"role": ["in", list(ROLE_NAMES)]}),
        "user_permission_for_new_users": frappe.db.count(
            "User Permission", {"user": ["in", [e["email"] for e in plan["users"]]]}),
    }


# ---------------------------------------------------------------------------
# Native verification: reads only
# ---------------------------------------------------------------------------
class FrappeGateway:
    """Read-only views used by the matrix; no write, commit, mail or enqueue path."""

    def __init__(self, site):
        self.site = site
        self.frappe = None
        self.doc = None

    def __enter__(self):
        import frappe

        frappe.init(site=self.site)
        frappe.connect()
        frappe.set_user("Administrator")
        self.frappe = frappe
        return self

    def __exit__(self, *exception):
        if self.frappe is not None:
            self.frappe.destroy()
        return False

    def _as_user(self, user, action):
        frappe = self.frappe
        previous = frappe.session.user
        frappe.set_user(user)
        try:
            return action()
        finally:
            frappe.set_user(previous)

    def prepare(self, photo_file, private_file):
        from clinic_photos.access.storage import canonical_private_key

        if photo_file == private_file:
            raise LabConflict("private_file_not_distinct")
        photo = self.frappe.db.get_value("File", photo_file,
                                         ["file_url", "is_private"], as_dict=True)
        private = self.frappe.db.get_value("File", private_file,
                                           ["file_url", "is_private"], as_dict=True)
        if not photo or not photo.get("file_url"):
            raise LabConflict("photo_file_missing")
        if not private or not private.get("is_private") or not private.get("file_url"):
            raise LabConflict("private_file_missing")
        key = canonical_private_key(photo.file_url)
        registered = (
            self.frappe.db.exists("Clinic Photo", {"storage_key": key})
            or self.frappe.db.exists("Clinic Photo Annotation Version", {"storage_key": key})
            or self.frappe.db.exists("Clinic Photo", {"original_file": photo_file})
        )
        if not registered:
            raise LabConflict("photo_not_registered")
        self.doc = self.frappe.get_doc("File", photo_file)
        self.private = self.frappe.get_doc("File", private_file)
        self.photo_doc = self.frappe.db.get_value(
            "Clinic Photo", {"original_file": photo_file}, "name") or self.frappe.db.get_value(
            "Clinic Photo", {"storage_key": key}, "name")
        if not self.photo_doc:
            raise LabConflict("photo_doc_missing")
        return {"key": key, "photo_doc": self.photo_doc, "file_url": photo.file_url,
                "file_name": photo_file,
                "private_url": private.file_url, "private_name": private_file,
                "private_size": self.private.file_size}

    def login(self, user, password, mode):
        frappe = self.frappe
        try:
            if mode == "native":
                from frappe.auth import LoginManager

                LoginManager().authenticate(user=user, pwd=password)
            else:
                from frappe.utils.password import check_password

                check_password(user, password)
        except Exception:
            return False
        gate = frappe.db.get_value("User", user, ["enabled", "user_type"], as_dict=True)
        return bool(gate and gate.get("enabled") and gate.get("user_type") == USER_TYPE)

    def read_photo(self, user):
        return self._allowed(user, "read", self.doc)

    def read_private(self, user):
        return self._allowed(user, "read", self.private)

    def list_photo(self, user):
        rows = self._as_user(user, lambda: self.frappe.get_list(
            "File", filters={"name": self.doc.name}, fields=["name"], limit_page_length=1))
        return bool(rows)

    def write_photo(self, user):
        return self._allowed(user, "write", self.doc)

    def _allowed(self, user, ptype, doc):
        return bool(self._as_user(
            user, lambda: self.frappe.has_permission("File", ptype, doc=doc)))

    def invariants(self, key, user, role):
        """Exactly one assigned role, no data rights, no admin grant; read-only lookups."""
        problems = []
        assigned = set(self.frappe.get_all(
            "Has Role", filters={"parent": user, "parenttype": "User"}, pluck="role"))
        assigned -= AUTOMATIC_ROLES
        if assigned != {role}:
            problems.append(f"role_mismatch.{key}")
        effective = set(self.frappe.get_roles(user))
        if effective & FORBIDDEN_ROLES:
            problems.append(f"admin_role.{key}")
        if role in ROLE_NAMES:
            if self.frappe.db.count("DocPerm", {"role": role}):
                problems.append(f"docperm.{key}")
            if self.frappe.db.exists("Custom DocPerm", {"role": role}):
                problems.append(f"custom_docperm.{key}")
        if self.frappe.db.exists("User Permission", {"user": user}):
            problems.append(f"user_permission.{key}")
        if self.frappe.db.get_value("User", user, "send_welcome_email"):
            problems.append(f"welcome_mail.{key}")
        return problems


def _new_http_session():
    import requests

    return requests.Session()


class HttpProbe:
    """Real HTTP login and reads against the site frontend. Never writes.

    The photo target (metadata + listing) and the private-bytes target are kept
    separate: ``read_private`` fetches the ``--private-file`` URL, never the
    photo URL. Transport failures are recorded as failures, never raised.
    """

    def __init__(self, base_url, host, session_factory=None):
        self.base_url = base_url.rstrip("/")
        self.host = host
        self.session_factory = session_factory or _new_http_session
        self.sessions = {}
        self.keys = {}
        self.photo_doc = None
        self.photo_url = None
        self.photo_name = None
        self.private_url = None
        self.private_name = None
        self.private_size = None
        self.failures = []

    def bind(self, plan):
        self.keys = {entry["email"]: entry["key"] for entry in plan["users"]}
        self.keys[GUEST] = GUEST

    def prepare(self, info, site_path=None):
        self.photo_doc = info["photo_doc"]
        self.photo_url = info["file_url"]
        self.photo_name = info["file_name"]
        self.private_url = info["private_url"]
        self.private_name = info["private_name"]
        self.private_size = info.get("private_size")

    def _name(self, user, check):
        return f"{self.keys.get(user, GUEST)}.{check}"

    def _record(self, user, check, status):
        if status not in (200, 401, 403):
            self.failures.append(f"http_status.{self._name(user, check)}.{status}")

    def login(self, user, password):
        try:
            session = self.session_factory()
            response = session.post(
                f"{self.base_url}/api/method/login",
                data={"usr": user, "pwd": password},
                headers={"Host": self.host},
                timeout=20,
            )
        except Exception as error:
            self.failures.append(f"transport.{self._name(user, 'login')}.{type(error).__name__}")
            return False
        self._record(user, "login", response.status_code)
        if response.status_code != 200 or not session.cookies.get("sid"):
            return False
        try:
            message = response.json().get("message")
        except ValueError:
            return False
        if message != "Logged In":
            return False
        self.sessions[user] = session
        return True

    def _get(self, user, path, params=None, check="read"):
        session = None if user == GUEST else self.sessions.get(user)
        try:
            if session is None:
                session = self.session_factory()
            response = session.get(f"{self.base_url}{path}", params=params,
                                   headers={"Host": self.host}, timeout=20)
        except Exception as error:
            self.failures.append(f"transport.{self._name(user, check)}.{type(error).__name__}")
            return None
        self._record(user, check, response.status_code)
        return response

    def read_photo(self, user):
        from urllib.parse import quote

        response = self._get(
            user, f"/api/resource/Clinic Photo/{quote(self.photo_doc)}", check="read_photo")
        return bool(response) and response.status_code == 200

    def read_private(self, user):
        response = self._get(user, self.private_url, check="read_private")
        if response is None or response.status_code != 200:
            return False
        if self.private_size is not None and len(response.content) != self.private_size:
            self.failures.append(f"bytes_mismatch.{self._name(user, 'read_private')}")
            return False
        return bool(response.content)

    def list_photo(self, user):
        import json as jsonlib

        response = self._get(
            user, "/api/resource/File",
            params={"filters": jsonlib.dumps([["name", "=", self.photo_name]]),
                    "fields": jsonlib.dumps(["name"]), "limit_page_length": 1},
            check="list_photo",
        )
        if response is None or response.status_code != 200:
            return False
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if not isinstance(payload, dict):
            self.failures.append(f"list_payload.{self._name(user, 'list_photo')}")
            return False
        return bool(payload.get("data"))


def verify(gateway, plan, records, photo_file, private_file, login_mode="password",
           probe=None, report=None):
    if login_mode not in ("password", "native"):
        raise LabConflict("login_mode_invalid")
    info = gateway.prepare(photo_file, private_file)
    if probe is not None:
        probe.bind(plan)
        probe.prepare(info)
    by_email = {entry["email"]: entry for entry in plan["users"]}
    observations = {}
    problems = []
    if probe is not None:
        login = lambda user, password: probe.login(user, password)  # noqa: E731
        reader = probe
    else:
        login = lambda user, password: gateway.login(user, password, login_mode)  # noqa: E731
        reader = gateway
    for record in records:
        entry = by_email.get(record["user"])
        if entry is None or entry["role"] != record["role"]:
            problems.append("credentials_unplanned")
            continue
        key = record["key"]
        observations[(key, "login")] = login(record["user"], record["password"])
        observations[(key, "read_photo")] = reader.read_photo(record["user"])
        observations[(key, "read_private")] = reader.read_private(record["user"])
        observations[(key, "list_photo")] = reader.list_photo(record["user"])
        observations[(key, "write_photo")] = gateway.write_photo(record["user"])
        problems.extend(gateway.invariants(key, record["user"], record["role"]))
    for check in ("read_photo", "read_private", "list_photo"):
        observations[(GUEST, check)] = getattr(reader, check)(GUEST)
    observations[(GUEST, "write_photo")] = gateway.write_photo(GUEST)
    if probe is not None:
        problems.extend(probe.failures)
    if report is not None:
        for check in CHECKS:
            values = [value for (actor, name), value in observations.items() if name == check]
            report[check] = {"total": len(values), "allowed": sum(1 for v in values if v)}
    return evaluate(observations, problems)


# ---------------------------------------------------------------------------
# CLI: plan writes an artifact; verify is read-only and needs --live
# ---------------------------------------------------------------------------
def main(argv=None):
    parser = argparse.ArgumentParser(description="clinic-photo-accept lab roles")
    commands = parser.add_subparsers(dest="mode", required=True)
    plan_cmd = commands.add_parser("plan")
    plan_cmd.add_argument("--inventory", required=True)
    plan_cmd.add_argument("--control-dir", required=True)
    plan_cmd.add_argument("--run-nonce", required=True)
    plan_cmd.add_argument("--live", action="store_true")
    apply_cmd = commands.add_parser("apply")
    apply_cmd.add_argument("--site", default=SITE)
    apply_cmd.add_argument("--control-dir", required=True)
    apply_cmd.add_argument("--live", action="store_true")
    verify_cmd = commands.add_parser("verify")
    verify_cmd.add_argument("--site", default=SITE)
    verify_cmd.add_argument("--control-dir", required=True)
    verify_cmd.add_argument("--photo-file", required=True)
    verify_cmd.add_argument("--private-file", required=True)
    verify_cmd.add_argument("--login-mode", choices=("password", "native"),
                            default="password")
    verify_cmd.add_argument("--transport", choices=("inprocess", "http"),
                            default="inprocess")
    verify_cmd.add_argument("--base-url", default="http://frontend:8080")
    verify_cmd.add_argument("--host", default=SITE)
    verify_cmd.add_argument("--live", action="store_true")
    arguments = parser.parse_args(argv)

    if not arguments.live:
        raise LabConflict("live_flag_required")
    control = assert_external(arguments.control_dir)
    if getattr(arguments, "site", SITE) != SITE:
        raise LabConflict("site_mismatch")

    if arguments.mode == "plan":
        plan = build_plan(read_json(arguments.inventory), arguments.run_nonce)
        write_json(os.path.join(control, "plan.json"), plan)
        write_credentials(os.path.join(control, "credentials.json"), plan)
        print(f"plan ok roles=5 users=7 nonce={plan['run_nonce']} control={control}")
        return 0

    plan = read_json(os.path.join(control, "plan.json"))
    assert_plan(plan)
    records = read_credentials(os.path.join(control, "credentials.json"))
    if {record["user"] for record in records} != {entry["email"] for entry in plan["users"]}:
        raise LabConflict("credentials_unplanned")

    if arguments.mode == "apply":
        with FrappeGateway(arguments.site) as gateway:
            evidence = apply_plan(gateway.frappe, plan, records)
        write_json(os.path.join(control, "apply.json"), evidence)
        print(f"apply {evidence['status']} roles={evidence['roles_created']} "
              f"users={evidence['users_created']} docperm="
              f"{evidence['docperm_for_new_roles']} custom_docperm="
              f"{evidence['custom_docperm_for_new_roles']} user_permission="
              f"{evidence['user_permission_for_new_users']}")
        return 0

    probe = None
    if arguments.transport == "http":
        try:
            probe = HttpProbe(arguments.base_url, arguments.host)
        except Exception as error:
            print(f"refused transport.{type(error).__name__}")
            return 2
    report = {}
    with FrappeGateway(arguments.site) as gateway:
        failures = verify(gateway, plan, records, arguments.photo_file,
                          arguments.private_file, arguments.login_mode, probe, report)
    for failure in failures:
        print(f"FAIL {failure}")
    if failures:
        print(f"verify failed failures={len(failures)}")
        return 1
    counts = " ".join(
        f"{check}={report[check]['allowed']}/{report[check]['total']}"
        for check in CHECKS if check in report)
    print(f"verify ok transport={arguments.transport} clinicians=allow "
          f"non_clinical=deny guest=deny write=deny allowed {counts}")
    return 0


def cli(argv=None):
    try:
        return main(argv)
    except LabConflict as conflict:
        print(f"refused {conflict}")
        return 2


if __name__ == "__main__":
    raise SystemExit(cli())
