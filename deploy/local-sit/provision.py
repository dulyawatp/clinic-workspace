#!/usr/bin/env python3
"""Provision synthetic-only state on the isolated local SIT site.

Runs inside the backend container after `bench new-site` has created
marley-sit.local with erpnext + healthcare + clinic_ui installed. It:

  * refuses to run unless the target site is exactly the configured SIT site;
  * creates one fictional Gender/Patient and a fixed workspace alias;
  * creates three fictional users, each with exactly one clinic role;
  * writes the clinic_ui site configuration (enabled, own-site identity,
    epoch secret, role map, synthetic patient allowlist).

Writes on the site are limited to this synthetic provisioning. No clinical
write capability is enabled: clinic_ui emits every capability as read_only.
Credentials arrive as environment variables and are never printed.
"""

from __future__ import annotations

import os
import sys

import frappe
from frappe.installer import get_site_config_path, update_site_config

SITE = os.environ["SIT_SITE"]
SITES_PATH = "/home/frappe/frappe-bench/sites"

EXPECTED_SITE = "marley-sit.local"
if SITE != EXPECTED_SITE:
    sys.exit(f"refusing: SIT_SITE must be {EXPECTED_SITE!r}, got {SITE!r}")

EPOCH_SECRET = os.environ["SIT_EPOCH_SECRET"]
if len(EPOCH_SECRET) < 32:
    sys.exit("refusing: SIT_EPOCH_SECRET must be at least 32 characters")

ROLE_MAP = {
    "DOC": ["Physician"],
    "NUR": ["Nursing User"],
    "REC": ["Healthcare Administrator"],
    # The write-contract wave codes. They map to built-in module roles that the
    # admin contract may assign (System Manager / Healthcare Administrator are
    # never assignable through the contract). REC stays mapped to the existing
    # native role of sit.rec; creating an REC user through the contract is
    # refused by design (privilege boundary), and no lane does so.
    "PHA": ["Stock Manager"],
    "CAS": ["Accounts User"],
    "MGR": ["Purchase User"],
    "ADM": ["Item Manager"],
}

ACCOUNTS = [
    ("sit.doc@example.invalid", "SIT Doctor", "Physician", "SIT_DOC_PASSWORD"),
    ("sit.nur@example.invalid", "SIT Nurse", "Nursing User", "SIT_NUR_PASSWORD"),
    ("sit.rec@example.invalid", "SIT Reception", "Healthcare Administrator", "SIT_REC_PASSWORD"),
]

WORKSPACE_PATIENT_ID = "WS-SIT-1"
PATIENT_FIRST_NAME = "SITSYNTH"
PATIENT_LAST_NAME = "Alpha"


def main() -> None:
    frappe.init(site=SITE, sites_path=SITES_PATH)
    frappe.connect()
    frappe.set_user("Administrator")

    installed = set(frappe.get_installed_apps())
    for required in ("frappe", "erpnext", "healthcare", "clinic_ui"):
        if required not in installed:
            sys.exit(f"refusing: app {required!r} is not installed on {SITE}")

    roles = {row["name"] for row in frappe.get_all("Role", fields=["name"])}
    missing = {role for roles_ in ROLE_MAP.values() for role in roles_} - roles
    if missing:
        sys.exit(f"refusing: required native roles missing: {sorted(missing)}")

    ensure_gender("Female")
    patient = ensure_patient()

    for email, first_name, role, password_var in ACCOUNTS:
        ensure_user(email, first_name, role, os.environ[password_var])

    for key, value in {
        "clinic_ui_enabled": 1,
        "clinic_ui_synthetic_site": SITE,
        "clinic_ui_epoch_secret": EPOCH_SECRET,
        "clinic_ui_role_map": ROLE_MAP,
        "clinic_ui_synthetic_patients": [
            {"workspace_patient_id": WORKSPACE_PATIENT_ID, "native_patient_id": patient.name}
        ],
    }.items():
        update_site_config(key, value)

    config_path = get_site_config_path()
    os.chmod(config_path, 0o600)

    frappe.db.commit()
    frappe.clear_cache()

    print(f"provisioned {SITE}: 1 synthetic patient, 3 synthetic users, clinic_ui config")
    print("workspace alias configured; native patient identifier withheld from output")


def ensure_gender(gender: str) -> None:
    if frappe.db.exists("Gender", gender):
        return
    doc = frappe.new_doc("Gender")
    doc.gender = gender
    doc.insert(ignore_permissions=True)


def ensure_patient():
    existing = frappe.get_all(
        "Patient",
        filters={"first_name": PATIENT_FIRST_NAME, "last_name": PATIENT_LAST_NAME},
        pluck="name",
        limit=1,
    )
    if existing:
        return frappe.get_doc("Patient", existing[0])

    doc = frappe.new_doc("Patient")
    doc.first_name = PATIENT_FIRST_NAME
    doc.last_name = PATIENT_LAST_NAME
    doc.sex = "Female"
    doc.dob = "1990-01-01"
    doc.insert(ignore_permissions=True)
    return doc


def ensure_user(email: str, first_name: str, role: str, password: str) -> None:
    if frappe.db.exists("User", email):
        user = frappe.get_doc("User", email)
        user.enabled = 1
        if not any(row.role == role for row in user.roles):
            user.append("roles", {"role": role})
    else:
        user = frappe.new_doc("User")
        user.email = email
        user.first_name = first_name
        user.enabled = 1
        user.user_type = "System User"
        user.send_welcome_email = 0
        user.append("roles", {"role": role})
        user.insert(ignore_permissions=True)

    user.new_password = password
    user.save(ignore_permissions=True)


if __name__ == "__main__":
    main()
