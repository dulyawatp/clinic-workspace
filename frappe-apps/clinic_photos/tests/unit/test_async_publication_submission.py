"""Public-seam tests for native authenticated submission and actor-only status.

The tests drive ``clinic_photos.api.photo_save`` and
``clinic_photos.publication.submission`` through a narrow fake Frappe module at
the framework boundary. They prove local behaviour only: request session/CSRF
shape, fresh domain authorization before staging, the absence of explicit
transaction control, and the absence of File/content writes. Installed Frappe
HTTP session/CSRF processing, the request transaction and native permissions
remain separate native gates.
"""

import base64
import contextlib
import hashlib
import os
import re
import sys
import tempfile
import types
import unittest
import uuid
from unittest import mock

from fake_frappe import fake_frappe

from clinic_photos.api import photo_save
from clinic_photos.publication import journal, submission, validation

AUTHOR = "clinician@example.test"
OTHER = "other@example.test"
DRAFT = "DRAFT-1"
PHOTO = "PHOTO-1"
PATIENT = "SYNTHETIC-PATIENT-1"
SITE = "clinic-photo-accept.local"
SID = "sid-current-session"
CSRF = "csrf-current-session"
CSRF_HEADER = "X-Frappe-CSRF-Token"

PNG_1X1_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGM4wcUFAAJ6AN1n"
    "J0J9AAAAAElFTkSuQmCC"
)
PNG_1X1_BYTES = base64.b64decode(PNG_1X1_BASE64)
OPERATION_ID = re.compile(r"[0-9a-f]{32}")


def docs(user=AUTHOR, enabled=1, role="Physician", user_present=True):
    rows = {
        "Clinic Photo Draft": {
            DRAFT: {
                "name": DRAFT,
                "author": AUTHOR,
                "photo": PHOTO,
                "current_version_number": 1,
                "current_version": None,
            }
        },
        "Clinic Photo": {
            PHOTO: {
                "name": PHOTO,
                "patient_reference": PATIENT,
                "original_file": "FILE-1",
                "storage_key": "",
            }
        },
        "Patient": {PATIENT: {"name": PATIENT}},
    }
    if user_present:
        rows["User"] = {user: {"name": user, "enabled": enabled}}
        rows["Has Role"] = {
            f"has-role-{user}": {
                "name": f"has-role-{user}",
                "parent": user,
                "parenttype": "User",
                "role": role,
            }
        }
    return rows


class FakeOperationDocument:
    """Narrow Document stand-in: server-owned values plus an insert boundary."""

    def __init__(self, db, values):
        self.db = db
        self.values = dict(values)

    def get(self, fieldname, default=None):
        return self.values.get(fieldname, default)

    def insert(self, ignore_permissions=False):
        self.values["name"] = uuid.uuid4().hex
        self.db.docs.setdefault(journal.OPERATION_DOCTYPE, {})[
            self.values["name"]
        ] = dict(self.values)
        return self


class NativePermissionCalls(dict):
    """Permission map plus the installed implementation callable boundary.

    ``frappe.permissions.has_permission`` is the supported callable that accepts
    ``print_logs``. The signature here is the real keyword-only one, so an
    unsupported argument raises ``TypeError`` instead of being swallowed.
    """

    def __init__(self, initial=None):
        super().__init__(initial or {})
        self.calls = []

    def has_permission(
        self,
        doctype,
        ptype="read",
        doc=None,
        user=None,
        *,
        parent_doctype=None,
        print_logs=True,
        debug=False,
        ignore_share_permissions=False,
    ):
        name = getattr(doc, "name", doc)
        self.calls.append(
            {
                "doctype": doctype,
                "ptype": ptype,
                "doc": name,
                "user": user,
                "print_logs": print_logs,
            }
        )
        return bool(self.get((doctype, ptype, name), False))


def make_facade_has_permission(frappe):
    """Mirror of the installed ``frappe.has_permission`` facade signature.

    The installed facade accepts no ``print_logs`` argument and forwards
    ``print_logs=throw`` to the implementation callable, which is how a quiet
    decision is requested through the public entry point.
    """

    def has_permission(
        doctype=None,
        ptype="read",
        doc=None,
        user=None,
        throw=False,
        *,
        parent_doctype=None,
        debug=False,
        ignore_share_permissions=False,
    ):
        if not doctype and doc is not None:
            doctype = getattr(doc, "doctype", None)
        return frappe.permissions.has_permission(
            doctype,
            ptype,
            doc=doc,
            user=user,
            print_logs=throw,
            parent_doctype=parent_doctype,
            debug=debug,
            ignore_share_permissions=ignore_share_permissions,
        )

    return has_permission


@contextlib.contextmanager
def _site_tree():
    """Real temporary site tree: staged image metadata is read back from disk."""
    with tempfile.TemporaryDirectory(prefix="clinic-submission-site-") as root:
        site_path = os.path.join(os.path.realpath(root), SITE)
        os.makedirs(os.path.join(site_path, "private"), exist_ok=True)
        yield site_path


@contextlib.contextmanager
def runtime(
    user=AUTHOR,
    enabled=1,
    role="Physician",
    user_present=True,
    method="POST",
    site=SITE,
    conf=None,
    sid=SID,
    csrf=CSRF,
    header=CSRF,
    authorization=None,
    cookie_sid=None,
    session_sid=None,
    form_sid=None,
    request=True,
):
    with _site_tree() as site_path, fake_frappe(
        docs=docs(user, enabled, role, user_present), user=user, site_path=site_path
    ) as frappe:
        frappe.conf = dict(conf or {})
        frappe.local.site = site
        frappe.session.sid = SID if session_sid is None else session_sid
        frappe.session.data = types.SimpleNamespace(csrf_token=csrf)
        frappe.form_dict = {"sid": form_sid} if form_sid is not None else {}

        headers = {CSRF_HEADER: header} if header is not None else {}
        if authorization is not None:
            headers["Authorization"] = authorization
        cookies = {"sid": SID if cookie_sid is None else cookie_sid}
        if request:
            frappe.local.request = types.SimpleNamespace(
                method=method,
                cookies=cookies,
                headers=headers,
                path="/api/method/save",
            )
        else:
            frappe.local.request = None

        def get_request_header(name, default=None):
            return headers.get(name, default)

        frappe.get_request_header = get_request_header
        frappe.get_doc = lambda values: FakeOperationDocument(frappe.db, values)
        frappe.db.count = lambda doctype, filters=None: len(
            frappe.db.docs.get(doctype, {})
        )

        frappe.permissions = NativePermissionCalls(frappe.permissions)
        frappe.has_permission = make_facade_has_permission(frappe)
        yield frappe


def enable(frappe, synthetic=False):
    frappe.conf[photo_save.GATE_CONFIG_KEY] = True
    if synthetic:
        frappe.conf[submission.SYNTHETIC_MODE_CONFIG_KEY] = True


def grant_target(frappe, user=AUTHOR, draft=DRAFT, photo=PHOTO, patient=PATIENT):
    frappe.permissions.update(
        {
            ("Clinic Photo Draft", "read", draft): True,
            ("Clinic Photo Draft", "write", draft): True,
            ("Clinic Photo", "read", photo): True,
            ("Patient", "read", patient): True,
        }
    )


def annotation_payload(**overrides):
    payload = {
        "kind": "annotation_append",
        "request_key": "request-key-0001",
        "draft": DRAFT,
        "expected_version": 1,
        "original": PHOTO,
        "annotation": {
            "schema_version": 1,
            "coordinate_space": "normalized",
            "shapes": [],
        },
    }
    payload.update(overrides)
    return payload


def synthetic_payload(**overrides):
    payload = {
        "kind": "synthetic_original_create",
        "request_key": "request-key-0002",
        "synthetic_patient": PATIENT,
        "image_base64": PNG_1X1_BASE64,
    }
    payload.update(overrides)
    return payload


def operation_rows(frappe):
    return frappe.db.docs.get(journal.OPERATION_DOCTYPE, {})


def copy_docs(frappe):
    return {
        doctype: {name: dict(row) for name, row in rows.items()}
        for doctype, rows in frappe.db.docs.items()
    }


class NativeSessionSubmissionTests(unittest.TestCase):
    def test_framework_transaction_only_stages_pending_without_writes(self):
        with runtime() as frappe:
            enable(frappe)
            grant_target(frappe)
            frappe.db.add(
                "File", "PENDING-1", file_url="/private/files/pending-file.png"
            )
            files_before = {
                name: dict(row)
                for name, row in frappe.db.docs["File"].items()
            }
            executed = []
            frappe.db.before_commit.add(lambda: executed.append("unrelated"))
            queued = len(frappe.db.before_commit.callbacks)

            with mock.patch.object(frappe.db, "commit") as commit:
                with mock.patch.object(frappe.db, "rollback") as rollback:
                    first = photo_save.save(annotation_payload())
                    second = photo_save.save(annotation_payload())

            self.assertEqual(first["status"], "pending")
            self.assertRegex(first["operation_id"], OPERATION_ID)
            self.assertEqual(second, first)

            rows = operation_rows(frappe)
            self.assertEqual(len(rows), 1)
            row = rows[first["operation_id"]]
            self.assertEqual(row["state"], journal.SUBMITTED_STATE)
            self.assertEqual(row["actor"], AUTHOR)
            self.assertEqual(row["site"], SITE)
            self.assertIsNone(row["reservation_key"])
            self.assertEqual(
                row["annotation_json"],
                '{"coordinate_space":"normalized","schema_version":1,"shapes":[]}',
            )

            self.assertEqual(
                {
                    name: dict(row)
                    for name, row in frappe.db.docs["File"].items()
                },
                files_before,
            )
            self.assertEqual(len(frappe.db.before_commit.callbacks), queued)
            self.assertEqual(executed, [])
            self.assertEqual(len(frappe.db.after_commit.callbacks), 1)
            commit.assert_not_called()
            rollback.assert_not_called()

    def test_post_is_registered_for_native_dispatch_and_enforced_at_runtime(self):
        with runtime(method="GET") as frappe:
            enable(frappe)
            with self.assertRaises(frappe.PermissionError) as caught:
                photo_save.save(annotation_payload())
            self.assertEqual(str(caught.exception), photo_save.SAFE_DENIAL)
            self.assertEqual(operation_rows(frappe), {})

        with runtime() as frappe:
            enable(frappe)
            calls = []

            def whitelist(**kwargs):
                def decorate(function):
                    calls.append((function.__name__, kwargs))
                    return function

                return decorate

            original_save = photo_save.save
            original_status = photo_save.status
            frappe.whitelist = whitelist
            try:
                photo_save._register_site_contract()
                self.assertEqual(
                    [name for name, _ in calls], ["save", "status"]
                )
                self.assertEqual(
                    [kwargs for _, kwargs in calls],
                    [{"methods": ["POST"]}, {"methods": ["POST"]}],
                )
            finally:
                photo_save.save = original_save
                photo_save.status = original_status

    def test_alternate_authorization_is_denied_even_with_valid_cookie_session(self):
        with runtime(authorization="token key:secret") as frappe:
            enable(frappe)
            grant_target(frappe)
            with self.assertRaises(frappe.PermissionError) as caught:
                photo_save.save(annotation_payload())

            self.assertEqual(str(caught.exception), photo_save.SAFE_DENIAL)
            self.assertEqual(operation_rows(frappe), {})

    def test_cookie_session_identity_cannot_be_replaced_by_form_sid(self):
        with runtime(
            cookie_sid="sid-from-form",
            session_sid=SID,
            form_sid="sid-from-form",
        ) as frappe:
            enable(frappe)
            grant_target(frappe)
            with self.assertRaises(frappe.PermissionError):
                photo_save.save(annotation_payload())
            self.assertEqual(operation_rows(frappe), {})

    def test_missing_empty_or_mismatched_csrf_token_is_denied(self):
        cases = (
            {"csrf": "", "header": CSRF},
            {"csrf": None, "header": CSRF},
            {"csrf": CSRF, "header": None},
            {"csrf": CSRF, "header": ""},
            {"csrf": CSRF, "header": "csrf-other"},
        )
        for options in cases:
            with self.subTest(options=options):
                with runtime(**options) as frappe:
                    enable(frappe)
                    grant_target(frappe)
                    with self.assertRaises(frappe.PermissionError) as caught:
                        photo_save.save(annotation_payload())
                    self.assertEqual(str(caught.exception), photo_save.SAFE_DENIAL)
                    self.assertEqual(operation_rows(frappe), {})

    def test_form_csrf_field_does_not_replace_the_explicit_header(self):
        with runtime(header=None) as frappe:
            enable(frappe)
            grant_target(frappe)
            frappe.form_dict["csrf_token"] = CSRF
            with self.assertRaises(frappe.PermissionError) as caught:
                photo_save.save(annotation_payload())
            self.assertEqual(str(caught.exception), photo_save.SAFE_DENIAL)
            self.assertEqual(operation_rows(frappe), {})

    def test_guest_administrator_non_clinician_and_revoked_actors_are_denied(self):
        cases = (
            {"user": "Guest"},
            {"user": "Administrator"},
            {"user": AUTHOR, "role": "Accounts User"},
            {"user": AUTHOR, "enabled": 0},
            {"user": AUTHOR, "user_present": False},
        )
        for options in cases:
            with self.subTest(options=options):
                with runtime(**options) as frappe:
                    enable(frappe)
                    grant_target(frappe)
                    with self.assertRaises(frappe.PermissionError) as caught:
                        photo_save.save(annotation_payload())
                    self.assertEqual(str(caught.exception), photo_save.SAFE_DENIAL)
                    self.assertEqual(operation_rows(frappe), {})

    def test_client_identity_site_path_and_flag_fields_are_rejected(self):
        for fieldname, value in (
            ("actor", AUTHOR),
            ("site", SITE),
            ("path", "/private/files/forged.png"),
            ("ignore_permissions", True),
            ("synthetic_mode", True),
            ("generation", 1),
            ("storage_key", "/private/files/x"),
        ):
            with self.subTest(fieldname=fieldname):
                with runtime() as frappe:
                    enable(frappe)
                    grant_target(frappe)
                    payload = annotation_payload()
                    payload[fieldname] = value
                    with self.assertRaises(frappe.ValidationError) as caught:
                        photo_save.save(payload)
                    self.assertEqual(str(caught.exception), photo_save.SAFE_VALIDATION)
                    self.assertEqual(operation_rows(frappe), {})

    def test_synthetic_requires_exact_site_true_mode_and_existing_authorized_patient(self):
        for site, mode in (
            ("other.local", True),
            (SITE, False),
            (SITE, 1),
            (SITE, "true"),
        ):
            with self.subTest(site=site, mode=mode):
                with runtime(
                    site=site,
                    conf={submission.SYNTHETIC_MODE_CONFIG_KEY: mode},
                ) as frappe:
                    frappe.conf[photo_save.GATE_CONFIG_KEY] = True
                    grant_target(frappe)
                    with self.assertRaises(frappe.PermissionError) as caught:
                        photo_save.save(synthetic_payload())
                    self.assertEqual(
                        str(caught.exception), photo_save.SAFE_SYNTHETIC_DENIED
                    )
                    self.assertEqual(operation_rows(frappe), {})

        with runtime() as frappe:
            enable(frappe, synthetic=True)
            grant_target(frappe)
            frappe.db.docs["Patient"] = {}
            with self.assertRaises(frappe.PermissionError) as caught:
                photo_save.save(synthetic_payload())
            self.assertEqual(str(caught.exception), photo_save.SAFE_DENIAL)
            self.assertEqual(operation_rows(frappe), {})

        with runtime() as frappe:
            enable(frappe, synthetic=True)
            with self.assertRaises(frappe.PermissionError) as caught:
                photo_save.save(synthetic_payload())
            self.assertEqual(str(caught.exception), photo_save.SAFE_DENIAL)
            self.assertEqual(operation_rows(frappe), {})

        with runtime() as frappe:
            enable(frappe, synthetic=True)
            grant_target(frappe)
            outcome = photo_save.save(synthetic_payload())
            self.assertEqual(outcome["status"], "pending")
            self.assertRegex(outcome["operation_id"], OPERATION_ID)
            row = operation_rows(frappe)[outcome["operation_id"]]
            self.assertEqual(row["synthetic_patient"], PATIENT)
            # Metadata-only staged reference; the accepted bytes are never persisted.
            self.assertNotIn("image_base64", row)
            self.assertEqual(row["staged_format"], "PNG")
            self.assertEqual(
                row["staged_sha256"], hashlib.sha256(PNG_1X1_BYTES).hexdigest()
            )
            self.assertEqual(row["staged_size"], len(PNG_1X1_BYTES))
            self.assertRegex(row["staged_key"], r"^[0-9a-f]{32}\.png$")
            self.assertIsNone(row["draft"])
            self.assertNotIn("File", frappe.db.docs)

    def test_unavailable_image_inspection_is_fixed_not_rejected_or_fabricated(self):
        with runtime() as frappe:
            enable(frappe, synthetic=True)
            grant_target(frappe)
            with mock.patch.object(
                validation,
                "validate_request",
                side_effect=validation.ImageInspectionUnavailable(
                    "Native image inspection is unavailable"
                ),
            ):
                with self.assertRaises(frappe.ValidationError) as caught:
                    photo_save.save(synthetic_payload())

            self.assertEqual(
                str(caught.exception), photo_save.SAFE_IMAGE_UNAVAILABLE
            )
            self.assertNotEqual(str(caught.exception), photo_save.SAFE_VALIDATION)
            self.assertEqual(operation_rows(frappe), {})
            self.assertNotIn("File", frappe.db.docs)

    def test_stale_target_authority_is_denied_before_staging(self):
        def without_draft(frappe):
            frappe.db.docs["Clinic Photo Draft"] = {}

        def other_author(frappe):
            frappe.db.docs["Clinic Photo Draft"][DRAFT]["author"] = OTHER

        def photo_mismatch(frappe):
            frappe.db.docs["Clinic Photo Draft"][DRAFT]["photo"] = "PHOTO-OTHER"

        def missing_photo(frappe):
            frappe.db.docs["Clinic Photo"] = {}

        def missing_patient_binding(frappe):
            frappe.db.docs["Clinic Photo"][PHOTO]["patient_reference"] = ""

        def no_native_read(frappe):
            frappe.permissions[("Clinic Photo Draft", "read", DRAFT)] = False

        def no_native_write(frappe):
            frappe.permissions[("Clinic Photo Draft", "write", DRAFT)] = False

        def no_photo_read(frappe):
            frappe.permissions[("Clinic Photo", "read", PHOTO)] = False

        for label, arrange in (
            ("missing draft", without_draft),
            ("other author", other_author),
            ("photo mismatch", photo_mismatch),
            ("missing photo", missing_photo),
            ("missing patient binding", missing_patient_binding),
            ("no native draft read", no_native_read),
            ("no native draft write", no_native_write),
            ("no native photo read", no_photo_read),
        ):
            with self.subTest(label=label):
                with runtime() as frappe:
                    enable(frappe)
                    grant_target(frappe)
                    arrange(frappe)
                    with self.assertRaises(frappe.PermissionError) as caught:
                        photo_save.save(annotation_payload())
                    self.assertEqual(str(caught.exception), photo_save.SAFE_DENIAL)
                    self.assertEqual(operation_rows(frappe), {})

    def test_missing_after_commit_seam_refuses_before_staging(self):
        with runtime() as frappe:
            enable(frappe)
            grant_target(frappe)
            delattr(frappe.db, "after_commit")
            with self.assertRaises(frappe.ValidationError) as caught:
                photo_save.save(annotation_payload())
            self.assertEqual(
                str(caught.exception), photo_save.SAFE_STAGE_UNAVAILABLE
            )
            self.assertEqual(operation_rows(frappe), {})

    def test_request_transaction_outcome_is_owned_by_the_framework(self):
        with runtime() as frappe:
            enable(frappe)
            grant_target(frappe)
            with mock.patch.object(frappe.db, "commit") as commit:
                with mock.patch.object(frappe.db, "rollback") as rollback:
                    outcome = photo_save.save(annotation_payload())
                    self.assertEqual(outcome["status"], "pending")
                    self.assertEqual(len(frappe.db.after_commit.callbacks), 1)

            self.assertEqual(
                operation_rows(frappe)[outcome["operation_id"]]["state"],
                journal.SUBMITTED_STATE,
            )
            self.assertNotIn("File", frappe.db.docs)
            commit.assert_not_called()
            rollback.assert_not_called()


class NativePermissionBoundaryTests(unittest.TestCase):
    """Target authority must use the installed permission API that accepts a
    quiet decision, and must keep the native veto and fixed denial message."""

    def test_synthetic_target_uses_supported_native_permission_callable(self):
        with runtime() as frappe:
            enable(frappe, synthetic=True)
            grant_target(frappe)

            outcome = photo_save.save(synthetic_payload())

            self.assertEqual(outcome["status"], "pending")
            self.assertEqual(frappe.session.user, AUTHOR)
            self.assertEqual(
                frappe.permissions.calls,
                [
                    {
                        "doctype": "Patient",
                        "ptype": "read",
                        "doc": PATIENT,
                        "user": None,
                        "print_logs": False,
                    }
                ],
            )

    def test_annotation_target_checks_each_document_through_native_callable(self):
        with runtime() as frappe:
            enable(frappe)
            grant_target(frappe)

            photo_save.save(annotation_payload())

            self.assertEqual(
                [
                    (
                        call["doctype"],
                        call["ptype"],
                        call["doc"],
                        call["user"],
                        call["print_logs"],
                    )
                    for call in frappe.permissions.calls
                ],
                [
                    ("Clinic Photo Draft", "read", DRAFT, None, False),
                    ("Clinic Photo Draft", "write", DRAFT, None, False),
                    ("Clinic Photo", "read", PHOTO, None, False),
                ],
            )

    def test_native_veto_denies_before_journal_without_identifying_message(self):
        with runtime() as frappe:
            enable(frappe, synthetic=True)
            grant_target(frappe)
            frappe.permissions[("Patient", "read", PATIENT)] = False
            frappe.flags = types.SimpleNamespace()
            messages = []
            frappe.msgprint = lambda *args, **kwargs: messages.append((args, kwargs))

            with self.assertRaises(frappe.PermissionError) as caught:
                photo_save.save(synthetic_payload())

            self.assertEqual(str(caught.exception), photo_save.SAFE_DENIAL)
            self.assertNotIn(PATIENT, str(caught.exception))
            self.assertEqual(operation_rows(frappe), {})
            self.assertEqual(messages, [])
            self.assertFalse(hasattr(frappe.flags, "has_permission_check_logs"))
            self.assertIsNone(getattr(frappe.local, "message_log", None))


def stored_operation(request, *, actor, site, operation_id=None, state="SUBMITTED"):
    return {
        "name": operation_id or uuid.uuid4().hex,
        "site": site,
        "actor": actor,
        "kind": request.kind,
        "request_key": request.request_key,
        "identity_digest": journal.identity_digest(
            site, actor, request.kind, request.request_key
        ),
        "payload_digest": journal.payload_digest(request),
        "state": state,
        "draft": request.draft,
        "expected_version": request.expected_version,
        "original": request.original,
        "synthetic_patient": request.synthetic_patient,
        "annotation_json": request.annotation,
        "image_base64": None,
        "reservation_key": None,
    }


def launcher(enqueue):
    module = types.ModuleType(submission.LAUNCHER_MODULE)
    module.enqueue_submission = enqueue
    return module


class StatusAndReplayTests(unittest.TestCase):
    def stage(self, frappe, payload=None):
        outcome = photo_save.save(annotation_payload() if payload is None else payload)
        self.assertEqual(outcome["status"], "pending")
        return outcome

    def test_same_key_replay_resolves_one_original_operation(self):
        with runtime() as frappe:
            enable(frappe)
            grant_target(frappe)
            first = self.stage(frappe)
            second = self.stage(frappe)

            self.assertEqual(second, first)
            self.assertEqual(len(operation_rows(frappe)), 1)
            self.assertEqual(len(frappe.db.after_commit.callbacks), 1)

            before = copy_docs(frappe)
            status = photo_save.status(first["operation_id"])

            # Task 9 resolves the same original operation as a read-only pending
            # status; it still never claims saved and writes nothing.
            self.assertEqual(
                status,
                {"status": "pending", "operation_id": first["operation_id"]},
            )
            self.assertNotEqual(status["status"], "saved")
            self.assertEqual(copy_docs(frappe), before)
            self.assertNotIn("File", frappe.db.docs)

    def test_ordinary_status_is_author_only_and_opaque(self):
        with runtime() as frappe:
            enable(frappe)
            grant_target(frappe)
            other_request = validation.validate_request(
                annotation_payload(request_key="other-request-key-01")
            )
            other_row = stored_operation(
                other_request, actor=OTHER, site=SITE
            )
            frappe.db.docs.setdefault(journal.OPERATION_DOCTYPE, {})[
                other_row["name"]
            ] = other_row
            before = copy_docs(frappe)

            refusals = []
            for operation_id in (
                other_row["name"],
                "0" * 32,
                "not-an-operation-id",
                "../private/files/patient",
                None,
            ):
                with self.subTest(operation_id=operation_id):
                    with self.assertRaises(frappe.PermissionError) as caught:
                        photo_save.status(operation_id)
                    refusals.append(str(caught.exception))
                    self.assertEqual(
                        str(caught.exception), photo_save.SAFE_STATUS_DENIED
                    )

            self.assertEqual(len(set(refusals)), 1)
            for secret in (PATIENT, DRAFT, PHOTO, "annotation", "private"):
                self.assertNotIn(secret, refusals[0])
            self.assertEqual(copy_docs(frappe), before)
            self.assertNotIn("File", frappe.db.docs)

    def test_status_requires_the_same_native_session_and_enabled_gate(self):
        with runtime() as frappe:
            grant_target(frappe)
            with self.assertRaises(frappe.PermissionError) as disabled:
                photo_save.status("0" * 32)
            self.assertEqual(str(disabled.exception), photo_save.SAFE_DISABLED)

        with runtime(header="csrf-other") as frappe:
            enable(frappe)
            grant_target(frappe)
            with self.assertRaises(frappe.PermissionError) as denied:
                photo_save.status("0" * 32)
            self.assertEqual(str(denied.exception), photo_save.SAFE_DENIAL)

    def test_status_recheck_of_target_authority_denies_opaque(self):
        with runtime() as frappe:
            enable(frappe)
            grant_target(frappe)
            outcome = self.stage(frappe)
            frappe.db.docs["Clinic Photo Draft"][DRAFT]["author"] = OTHER
            with self.assertRaises(frappe.PermissionError) as caught:
                photo_save.status(outcome["operation_id"])
            self.assertEqual(str(caught.exception), photo_save.SAFE_STATUS_DENIED)

    def test_replay_requires_fresh_authority_before_lookup_or_stage(self):
        with runtime() as frappe:
            enable(frappe)
            grant_target(frappe)
            outcome = self.stage(frappe)
            row = operation_rows(frappe)[outcome["operation_id"]]
            frappe.db.docs["Clinic Photo Draft"][DRAFT]["author"] = OTHER

            with self.assertRaises(frappe.PermissionError) as caught:
                photo_save.save(annotation_payload())

            self.assertEqual(str(caught.exception), photo_save.SAFE_DENIAL)
            self.assertEqual(row["state"], journal.SUBMITTED_STATE)
            self.assertEqual(
                row["annotation_json"],
                '{"coordinate_space":"normalized","schema_version":1,"shapes":[]}',
            )

    def test_advanced_same_payload_maps_to_unknown_without_saved_claim(self):
        with runtime() as frappe:
            enable(frappe)
            grant_target(frappe)
            outcome = self.stage(frappe)
            row = operation_rows(frappe)[outcome["operation_id"]]
            row["state"] = "PREPARED"
            row["reservation_key"] = "/private/files/clinic-annotation-1.json"

            replay = photo_save.save(annotation_payload())

            self.assertEqual(
                replay,
                {"status": "unknown", "operation_id": outcome["operation_id"]},
            )
            self.assertNotEqual(replay["status"], "saved")
            self.assertEqual(row["state"], "PREPARED")
            self.assertNotIn("File", frappe.db.docs)
            with self.assertRaises(frappe.PermissionError) as caught:
                photo_save.status(outcome["operation_id"])
            self.assertEqual(str(caught.exception), photo_save.SAFE_STATUS_DENIED)

    def test_changed_payload_conflict_maps_the_verified_original_identity(self):
        with runtime() as frappe:
            enable(frappe)
            grant_target(frappe)
            outcome = self.stage(frappe)
            stored = dict(operation_rows(frappe)[outcome["operation_id"]])

            changed = annotation_payload(
                annotation={
                    "schema_version": 1,
                    "coordinate_space": "normalized",
                    "shapes": [
                        {
                            "id": "shape-1",
                            "type": "text",
                            "color": "#112233",
                            "x": 0.1,
                            "y": 0.1,
                            "text": "note",
                            "font_size": 0.1,
                        }
                    ],
                }
            )
            conflict = photo_save.save(changed)

            self.assertEqual(
                conflict,
                {"status": "conflict", "operation_id": outcome["operation_id"]},
            )
            self.assertEqual(len(operation_rows(frappe)), 1)
            self.assertEqual(
                operation_rows(frappe)[outcome["operation_id"]], stored
            )

    def test_conflict_without_verified_identity_is_a_fixed_exception(self):
        with runtime() as frappe:
            enable(frappe)
            grant_target(frappe)
            with mock.patch.object(
                journal,
                "stage_submission",
                side_effect=journal.JournalConflict(journal.SAFE_JOURNAL_CONFLICT),
            ):
                with self.assertRaises(frappe.ValidationError) as caught:
                    photo_save.save(annotation_payload())

            self.assertEqual(
                str(caught.exception), journal.SAFE_JOURNAL_CONFLICT
            )
            self.assertEqual(operation_rows(frappe), {})

    def test_after_commit_module_or_queue_failure_leaves_submitted(self):
        with runtime() as frappe:
            enable(frappe)
            grant_target(frappe)
            outcome = self.stage(frappe)
            sys.modules.pop(submission.LAUNCHER_MODULE, None)
            frappe.db.after_commit.run()
            self.assertEqual(
                operation_rows(frappe)[outcome["operation_id"]]["state"],
                journal.SUBMITTED_STATE,
            )
            self.assertNotIn("File", frappe.db.docs)

        attempts = []

        def unavailable_queue(operation_id):
            attempts.append(operation_id)
            raise RuntimeError("queue unavailable")

        with runtime() as frappe:
            enable(frappe)
            grant_target(frappe)
            outcome = self.stage(frappe)
            with mock.patch.dict(
                sys.modules, {submission.LAUNCHER_MODULE: launcher(unavailable_queue)}
            ):
                frappe.db.after_commit.run()

            self.assertEqual(attempts, [outcome["operation_id"]])
            self.assertEqual(
                operation_rows(frappe)[outcome["operation_id"]]["state"],
                journal.SUBMITTED_STATE,
            )
            self.assertNotIn("File", frappe.db.docs)

    def test_request_rollback_never_notifies_the_launcher(self):
        attempts = []

        with runtime() as frappe:
            enable(frappe)
            grant_target(frappe)
            outcome = self.stage(frappe)
            with mock.patch.dict(
                sys.modules,
                {
                    submission.LAUNCHER_MODULE: launcher(
                        lambda operation_id: attempts.append(operation_id)
                    )
                },
            ):
                frappe.db.rollback()

            self.assertEqual(attempts, [])
            self.assertEqual(
                operation_rows(frappe)[outcome["operation_id"]]["state"],
                journal.SUBMITTED_STATE,
            )
            self.assertNotIn("File", frappe.db.docs)


class NativeDiagnosticRegressionTests(unittest.TestCase):
    """Execute the actual native diagnostic body under the shared fake boundary.

    This does not prove installed native behaviour. It only proves that the
    env-gated diagnostic itself no longer calls a removed entry and that, with
    the exact site, opt-in and no HTTP request, the current public contract
    refuses before gate/session/staging and creates no operation.
    """

    def test_native_defaultoff_no_request_diagnostic_body_runs(self):
        from clinic_photos.tests.test_publication import (
            APPROVED_SITE,
            DIAGNOSTIC_ENV,
            PublicationEntryDiagnostic,
        )

        method_name = "test_default_off_entry_refuses_without_http_request"
        with mock.patch.dict(os.environ, {DIAGNOSTIC_ENV: "1"}):
            with runtime(site=APPROVED_SITE, request=False) as frappe:
                case = PublicationEntryDiagnostic(methodName=method_name)
                case.setUp()
                getattr(case, method_name)()
                self.assertEqual(
                    frappe.db.count("Clinic Photo Save Operation"), 0
                )
