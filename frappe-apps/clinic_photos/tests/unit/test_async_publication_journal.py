"""Focused contract tests for the durable SUBMITTED operation journal.

The journal stores an accepted request before any protected key or File exists. The
tests below run the real request contracts, real digest functions and the real
repository seam against a narrow in-memory Frappe stub. That stub is a local fixture,
not a Frappe/MariaDB simulation: it cannot establish native unique constraints,
transaction rollback, DocPerm behaviour or HTTP denial. Those checks are explicitly
pending against the development site and must not be inferred from these fakes.

The schema assertions read the shipped DocType JSON. They prove the declared shape
only, not that a migration created the columns or indexes. The user-approved trusted
Administrator read/list/report/export exception is native; ordinary clinical client
access and all ordinary operation mutation remain denied.
"""

import base64
import contextlib
import copy
import hashlib
import importlib
import json
import os
import re
import shutil
import sys
import tempfile
import types
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from clinic_photos.publication import contracts, journal, validation

SITE = "clinic-photo-accept.local"
STAGE_ROOT = None


def setUpModule():
    """Real per-module site tree: staged image metadata is read back from disk."""
    global STAGE_ROOT
    STAGE_ROOT = os.path.realpath(tempfile.mkdtemp(prefix="clinic-journal-staging-"))
    os.makedirs(os.path.join(STAGE_ROOT, SITE, "private"), exist_ok=True)


def tearDownModule():
    shutil.rmtree(STAGE_ROOT, ignore_errors=True)


ACTOR = "clinician@example.test"
REQUEST_KEY = "request-key-0001"
SYNTHETIC_REQUEST_KEY = "request-key-0002"
SYNTHETIC_PATIENT = "SYNTHETIC-PATIENT-1"

# Known-good 1x1 PNG fixture from the contract tests.
PNG_1X1_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGM4wcUFAAJ6AN1n"
    "J0J9AAAAAElFTkSuQmCC"
)
PNG_1X1_BYTES = base64.b64decode(PNG_1X1_BASE64)
JPEG_2X2_BASE64 = (
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRof"
    "Hh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwh"
    "MjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wA"
    "ARCAACAAIDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QA"
    "tRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2"
    "JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eX"
    "qDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2u"
    "Hi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8"
    "QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYn"
    "LRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eH"
    "l6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2N"
    "na4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDgqKKK8M/VD//Z"
)

ENVELOPE = {"schema_version": 1, "coordinate_space": "normalized"}


def annotation(**values):
    shape = {
        "id": "shape-1",
        "type": "rectangle",
        "color": "#1a2B3c",
        "x": 0.25,
        "y": 0.5,
        "width": 0.5,
        "height": 0.25,
        "stroke_width": 0.02,
    }
    shape.update(values)
    return {**ENVELOPE, "shapes": [shape]}


def annotation_payload(**overrides):
    payload = {
        "kind": "annotation_append",
        "request_key": REQUEST_KEY,
        "draft": "DRAFT-1",
        "expected_version": 0,
        "original": "PHOTO-1",
        "annotation": annotation(),
    }
    payload.update(overrides)
    return payload


def synthetic_payload(**overrides):
    payload = {
        "kind": "synthetic_original_create",
        "request_key": SYNTHETIC_REQUEST_KEY,
        "synthetic_patient": SYNTHETIC_PATIENT,
        "image_base64": PNG_1X1_BASE64,
    }
    payload.update(overrides)
    return payload


class FakeOperationDocument(dict):
    """Only the document surface the repository seam calls."""

    def __init__(self, values, db):
        super().__init__(values)
        self.name = values.get("name") or ""
        self._db = db
        self.insert_calls = []

    def insert(self, **kwargs):
        self.insert_calls.append(kwargs)
        self._db.insert_attempts += 1
        if self._db.insert_exception is not None:
            raise self._db.insert_exception("forced unique boundary")
        for row in self._db.docs.get(journal.OPERATION_DOCTYPE, {}).values():
            if row.get("identity_digest") == self.get("identity_digest"):
                raise self._db.unique_exception_type("unique identity")
            reservation = self.get("reservation_key")
            if (
                reservation not in (None, "")
                and row.get("reservation_key") == reservation
            ):
                raise self._db.unique_exception_type("unique reservation")
        if not self.name:
            self.name = uuid.uuid4().hex
        self["name"] = self.name
        values = {
            key: value
            for key, value in self.items()
            if key not in ("doctype", "name")
        }
        self._db.add(journal.OPERATION_DOCTYPE, self.name, **values)
        return self


class FakeDB:
    def __init__(self):
        self.docs = {}
        self.commit_calls = 0
        self.rollback_calls = 0
        self.get_value_calls = []
        self.hidden_for_plain_read = set()
        self.recovery_visible = True
        self.unique_exception_type = None
        self.insert_exception = None
        self.insert_attempts = 0

    def add(self, doctype, name, **values):
        self.docs.setdefault(doctype, {})[name] = dict(values, name=name)
        return self.docs[doctype][name]

    def get_value(
        self,
        doctype,
        filters=None,
        fieldname="name",
        as_dict=False,
        for_update=False,
        **_kwargs,
    ):
        self.get_value_calls.append(
            {"doctype": doctype, "filters": filters, "for_update": for_update}
        )
        rows = list(self.docs.get(doctype, {}).values())
        if isinstance(filters, str):
            rows = [row for row in rows if row.get("name") == filters]
        elif filters:
            rows = [
                row
                for row in rows
                if all(row.get(key) == value for key, value in filters.items())
            ]
        if doctype == journal.OPERATION_DOCTYPE:
            if not for_update:
                rows = [
                    row
                    for row in rows
                    if row.get("name") not in self.hidden_for_plain_read
                ]
            elif not self.recovery_visible:
                rows = []
        if not rows:
            return None
        row = rows[0]
        fields = fieldname if isinstance(fieldname, (list, tuple)) else [fieldname]
        if as_dict:
            return {field: row.get(field) for field in fields}
        values = [row.get(field) for field in fields]
        return values if isinstance(fieldname, (list, tuple)) else values[0]

    def get_all(self, doctype, filters=None, fields=None, pluck=None, limit=None, **_kwargs):
        rows = list(self.docs.get(doctype, {}).values())
        if filters:
            rows = [
                row
                for row in rows
                if all(row.get(key) == value for key, value in filters.items())
            ]
        if limit:
            rows = rows[:limit]
        if pluck:
            return [row.get(pluck) for row in rows]
        if fields:
            return [{field: row.get(field) for field in fields} for row in rows]
        return [dict(row) for row in rows]

    def commit(self):
        self.commit_calls += 1
        raise AssertionError("journal must never commit")

    def rollback(self):
        self.rollback_calls += 1
        raise AssertionError("journal must never roll back")


class FakeFrappe(types.ModuleType):
    def __init__(self, *, user=ACTOR, site=SITE, roles=("Physician",), enabled=1):
        super().__init__("frappe")
        self.db = FakeDB()
        self.session = types.SimpleNamespace(user=user)
        self.local = types.SimpleNamespace(site=site)
        self.PermissionError = type("PermissionError", (Exception,), {})
        self.ValidationError = type("ValidationError", (Exception,), {})
        self.UniqueValidationError = type("UniqueValidationError", (Exception,), {})
        self.DuplicateEntryError = type("DuplicateEntryError", (Exception,), {})
        self.QueryDeadlockError = type("QueryDeadlockError", (Exception,), {})
        self.db.unique_exception_type = self.UniqueValidationError
        self.db.add("User", user, enabled=enabled)
        for role in roles:
            self.db.add(
                "Has Role", f"{user}:{role}", parent=user, parenttype="User", role=role
            )

    def get_doc(self, values):
        return FakeOperationDocument(values, self.db)

    def get_site_path(self, *parts):
        return os.path.join(STAGE_ROOT, self.local.site, *parts)

    def throw(self, message, exc=None):
        raise (exc or self.ValidationError)(message)


@contextlib.contextmanager
def fake_frappe(**kwargs):
    module = FakeFrappe(**kwargs)
    previous = sys.modules.get("frappe")
    sys.modules["frappe"] = module
    try:
        # Repository-shape tests do not model Appointment/Encounter/consent rows.
        # Role and visit-guard cases override this seam explicitly below.
        from clinic_photos.visit import capture
        with patch.object(capture, "check_visit_authority"):
            yield module
    finally:
        if previous is None:
            sys.modules.pop("frappe", None)
        else:
            sys.modules["frappe"] = previous


def operation_rows(fake):
    return fake.db.docs.get(journal.OPERATION_DOCTYPE, {})


def operation_read_calls(fake):
    return [
        call
        for call in fake.db.get_value_calls
        if call["doctype"] == journal.OPERATION_DOCTYPE
    ]


def seed_hidden_winner(fake, request, *, state="SUBMITTED", reservation_key=None):
    """Materialise a committed winner invisible to the first snapshot read."""
    with fake_frappe() as seed:
        operation = journal.stage_submission(request, actor=ACTOR, site=SITE)
        row = copy.deepcopy(operation_rows(seed)[operation.id])
    row["state"] = state
    row["reservation_key"] = reservation_key
    fake.db.docs.setdefault(journal.OPERATION_DOCTYPE, {})[operation.id] = row
    fake.db.hidden_for_plain_read.add(operation.id)
    return operation.id, row


class SubmittedDurabilityTests(unittest.TestCase):
    def test_visit_new_submission_callback_once_replay_never_notifies(self):
        binding = contracts.VisitBinding(
            appointment="APT", patient="PAT", slot="Before", encounter=None,
            body_area="Arm", viewpoint="Front", appointment_creation="ac",
            patient_creation="pc", encounter_creation=None, grant_audit="GRANT",
        )
        request = contracts.SaveRequest(kind=contracts.VISIT_ORIGINAL_CREATE,
            request_key="visit-notify-key-01", visit_binding=binding, image_bytes=PNG_1X1_BYTES)
        with fake_frappe():
            created = []
            first = journal.stage_submission(request, actor=ACTOR, site=SITE, on_created=created.append)
            second = journal.stage_submission(request, actor=ACTOR, site=SITE, on_created=created.append)
            self.assertEqual(first, second)
            self.assertEqual(created, [first])

    def test_visit_callback_setup_failure_escapes_after_insert(self):
        binding = contracts.VisitBinding(
            appointment="APT", patient="PAT", slot="Before", encounter=None,
            body_area="Arm", viewpoint="Front", appointment_creation="ac",
            patient_creation="pc", encounter_creation=None, grant_audit="GRANT",
        )
        request = contracts.SaveRequest(kind=contracts.VISIT_ORIGINAL_CREATE,
            request_key="visit-notify-key-02", visit_binding=binding, image_bytes=PNG_1X1_BYTES)
        with fake_frappe() as fake:
            def fail(_operation):
                raise RuntimeError("callback registration failed")
            with self.assertRaisesRegex(RuntimeError, "callback registration failed"):
                journal.stage_submission(request, actor=ACTOR, site=SITE, on_created=fail)
            self.assertEqual(len(operation_rows(fake)), 1)  # fake has no rollback

    def test_visit_admin_before_only_and_legacy_roles_remain_unchanged(self):
        from dataclasses import replace
        from clinic_photos.visit import actor
        binding = contracts.VisitBinding(
            appointment="APT", patient="PAT", slot="Before", encounter=None,
            body_area="Arm", viewpoint="Front", appointment_creation="ac",
            patient_creation="pc", encounter_creation=None, grant_audit="GRANT",
        )
        request = contracts.SaveRequest(kind=contracts.VISIT_ORIGINAL_CREATE,
            request_key="visit-role-key-0001", visit_binding=binding, image_bytes=PNG_1X1_BYTES)
        with fake_frappe(roles=("Healthcare Administrator",)) as fake:
            from clinic_photos.visit import capture
            with patch.object(fake.db, "sql", return_value=[("Healthcare Administrator",)], create=True), \
                 patch.object(capture, "check_visit_authority",
                              side_effect=lambda b: actor.require_visit_actor(doctor=b.slot == "After")):
                journal.require_visit_authorization(request, ACTOR, SITE)
                after = replace(request, visit_binding=replace(
                    binding, slot="After", encounter="ENC", encounter_creation="ec"))
                with self.assertRaises(journal.JournalAuthorityDenied):
                    journal.require_visit_authorization(after, ACTOR, SITE)
                with self.assertRaises(journal.JournalAuthorityDenied):
                    journal.require_service_authorization(ACTOR, SITE)

    def test_visit_controller_scope_is_exact_document_and_fields(self):
        binding = contracts.VisitBinding(
            appointment="APT", patient="PAT", slot="Before", encounter=None,
            body_area="Arm", viewpoint="Front", appointment_creation="ac",
            patient_creation="pc", encounter_creation=None, grant_audit="GRANT",
        )
        request = contracts.SaveRequest(kind=contracts.VISIT_ORIGINAL_CREATE,
            request_key="visit-scope-key-001", visit_binding=binding, image_bytes=PNG_1X1_BYTES)
        with fake_frappe() as fake:
            values = journal._document_values(request, actor=ACTOR, site=SITE,
                identity=journal.identity_digest(SITE, ACTOR, request.kind, request.request_key),
                payload=journal.payload_digest(request), staged=None)
            doc = FakeOperationDocument(values, fake.db)
            with self.assertRaises(journal.JournalAuthorityDenied):
                journal.require_visit_submission_insertion(doc)
            with journal._visit_submission_insertion(doc, request, ACTOR, SITE, values):
                journal.require_visit_submission_insertion(doc)
                with self.assertRaises(journal.JournalAuthorityDenied):
                    journal.require_visit_submission_insertion(FakeOperationDocument(values, fake.db))
                doc["visit_binding_json"] = "tampered"
                with self.assertRaises(journal.JournalAuthorityDenied):
                    journal.require_visit_submission_insertion(doc)

    def test_visit_prepare_rechecks_guard_before_reservation_update(self):
        from unittest.mock import Mock
        from clinic_photos.publication import exclusion
        binding = contracts.VisitBinding(
            appointment="APT", patient="PAT", slot="Before", encounter=None,
            body_area="Arm", viewpoint="Front", appointment_creation="ac",
            patient_creation="pc", encounter_creation=None, grant_audit="GRANT",
        )
        request = contracts.SaveRequest(kind=contracts.VISIT_ORIGINAL_CREATE,
            request_key="visit-recheck-0001", visit_binding=binding, image_bytes=PNG_1X1_BYTES)
        with fake_frappe() as fake:
            from clinic_photos.visit import capture
            submission = journal.stage_submission(request, actor=ACTOR, site=SITE)
            snapshot = copy.deepcopy(operation_rows(fake)[submission.id])
            owner = Mock()
            locked = types.SimpleNamespace(kind=request.kind, draft=None, original=None,
                                           synthetic_patient=None)
            with patch.object(exclusion, "require_locked_owner", return_value=owner), \
                 patch.object(exclusion, "reread_locked_operation", return_value=locked), \
                 patch.object(capture, "check_visit_authority", side_effect=PermissionError("revoked")) as check:
                with self.assertRaises(journal.JournalAuthorityDenied):
                    journal.prepare(submission)
                check.assert_called_once_with(binding)
            self.assertEqual(operation_rows(fake)[submission.id], snapshot)

    def test_visit_authorization_binds_session_and_rechecks_visit_guard(self):
        from unittest.mock import patch
        binding = contracts.VisitBinding(
            appointment="APT", patient="PAT", slot="Before", encounter=None,
            body_area="Arm", viewpoint="Front", appointment_creation="ac",
            patient_creation="pc", encounter_creation=None, grant_audit="GRANT",
        )
        request = contracts.SaveRequest(kind=contracts.VISIT_ORIGINAL_CREATE,
            request_key="visit-auth-key-001", visit_binding=binding, image_bytes=PNG_1X1_BYTES)
        with fake_frappe() as fake:
            from clinic_photos.visit import capture
            with patch.object(capture, "check_visit_authority") as check:
                journal.require_visit_authorization(request, ACTOR, SITE)
                check.assert_called_once_with(binding)
                with self.assertRaises(journal.JournalAuthorityDenied):
                    journal.require_visit_authorization(request, "other", SITE)
                with self.assertRaises(journal.JournalAuthorityDenied):
                    journal.require_visit_authorization(request, ACTOR, "other")

    def test_visit_published_outcome_requires_exact_matching_slot(self):
        binding = contracts.VisitBinding(
            appointment="APT-1", patient="PAT-1", slot="Before", encounter=None,
            body_area="Arm", viewpoint="Front", appointment_creation="ac",
            patient_creation="pc", encounter_creation=None, grant_audit="AUDIT-1",
        )
        request = contracts.SaveRequest(kind=contracts.VISIT_ORIGINAL_CREATE,
            request_key="visit-published-001", visit_binding=binding, image_bytes=PNG_1X1_BYTES)
        with fake_frappe() as fake:
            from clinic_photos.visit.capture import slot_key
            submitted = journal.stage_submission(request, actor=ACTOR, site=SITE)
            row = operation_rows(fake)[submitted.id]
            sha, size = journal._prepared_evidence(request)
            row.update(state="PUBLISHED", generation=1, expected_sha256=sha, expected_size=size,
                       reservation_key=journal._reservation_key(request, site=SITE,
                           operation_id=submitted.id, generation=1, payload=submitted.payload_digest),
                       published_file="FILE", published_domain_doctype="Clinic Photo",
                       published_domain_name="PHOTO", published_version=0, creation_identity="1:2")
            key = slot_key(binding.appointment, binding.slot)
            fake.db.add("Clinic Visit Photo Slot", key, slot_key=key, appointment="APT-1",
                        patient="PAT-1", slot="Before", appointment_creation="ac",
                        patient_creation="pc", body_area="Arm", viewpoint="Front",
                        operation=submitted.id, photo="PHOTO")
            outcome = journal._published_outcome(row)
            self.assertEqual(outcome.domain_name, "PHOTO")
            self.assertEqual(outcome.reservation.visit_binding, binding)
            fake.db.docs["Clinic Visit Photo Slot"][key]["photo"] = "OTHER"
            with self.assertRaises(journal.JournalIntegrityError):
                journal._published_outcome(row)

    def test_visit_prepared_reservation_roundtrip_and_tampering(self):
        from dataclasses import replace
        binding = contracts.VisitBinding(
            appointment="APT-1", patient="PAT-1", slot="Before", encounter=None,
            body_area="Arm", viewpoint="Front", appointment_creation="ac",
            patient_creation="pc", encounter_creation=None, grant_audit="AUDIT-1",
        )
        with fake_frappe() as fake:
            for index, current in enumerate((
                binding, replace(binding, slot="After", encounter="ENC-1", encounter_creation="ec"),
            )):
                request = contracts.SaveRequest(
                    kind=contracts.VISIT_ORIGINAL_CREATE,
                    request_key=f"visit-prep-key-{index:04d}", visit_binding=current,
                    image_bytes=PNG_1X1_BYTES,
                )
                submitted = journal.stage_submission(request, actor=ACTOR, site=SITE)
                row = operation_rows(fake)[submitted.id]
                sha, size = journal._prepared_evidence(request)
                row.update(state="PREPARED", generation=1, expected_sha256=sha,
                           expected_size=size, reservation_key=journal._reservation_key(
                               request, site=SITE, operation_id=submitted.id, generation=1,
                               payload=submitted.payload_digest))
                prepared = journal._prepared_operation(row)
                self.assertEqual(prepared.visit_binding, current)
                self.assertEqual(prepared.expected_sha256, hashlib.sha256(PNG_1X1_BYTES).hexdigest())
                self.assertEqual(prepared.expected_size, len(PNG_1X1_BYTES))
                self.assertNotIn("APT-1", prepared.key)
                self.assertNotIn("APT-1", prepared.target_identity)
                self.assertEqual(prepared.target_identity, journal.target_identity(request))
                for field, value in (("expected_size", size + 1), ("reservation_key", "/bad"),
                                     ("expected_sha256", "0" * 64),
                                     ("generation", 2), ("published_file", "file"),
                                     ("visit_binding_json", row["visit_binding_json"].replace("Arm", "Leg")),
                                     ("synthetic_patient", "other")):
                    original = row.get(field)
                    row[field] = value
                    with self.subTest(slot=current.slot, field=field), self.assertRaises(journal.JournalIntegrityError):
                        journal._prepared_operation(row)
                    row[field] = original
            after_request = replace(request, visit_binding=binding)
            self.assertEqual(journal.target_identity(request), journal.target_identity(after_request))

    def test_visit_submission_roundtrip_conflicts_and_tamper(self):
        from dataclasses import replace

        binding = contracts.VisitBinding(
            appointment="APT-1", patient="PAT-1", slot="After", encounter="ENC-1",
            body_area="Arm", viewpoint="Front", appointment_creation="2026-01-01 10:00:00",
            patient_creation="2026-01-01 09:00:00",
            encounter_creation="2026-01-01 11:00:00", grant_audit="AUDIT-1",
        )
        request = contracts.SaveRequest(
            kind=contracts.VISIT_ORIGINAL_CREATE, request_key="visit-key-0000001",
            visit_binding=binding, image_bytes=PNG_1X1_BYTES,
        )
        with fake_frappe() as fake:
            first = journal.stage_submission(request, actor=ACTOR, site=SITE)
            self.assertEqual(journal.load_submission(first.id), first)
            self.assertEqual(journal.load_submission(first.id).request, request)
            self.assertEqual(journal.stage_submission(request, actor=ACTOR, site=SITE).id, first.id)
            self.assertEqual(journal.target_identity(request),
                             journal.exclusion.target_identity_for_operation(first.id))
            from clinic_photos.publication import submission
            with self.assertRaises(submission.SubmissionTargetDenied):
                submission.require_target_authority(request, ACTOR, SITE)
            for field in binding.__dataclass_fields__:
                if field == "slot":
                    changed = replace(binding, slot="Before", encounter=None, encounter_creation=None)
                elif field == "encounter":
                    changed = replace(binding, encounter="ENC-2")
                else:
                    changed = replace(binding, **{field: getattr(binding, field) + "2"})
                with self.subTest(field=field), self.assertRaises(journal.JournalConflict):
                    journal.stage_submission(replace(request, visit_binding=changed), actor=ACTOR, site=SITE)
            with self.assertRaises(journal.JournalConflict):
                journal.stage_submission(
                    replace(request, image_bytes=base64.b64decode(JPEG_2X2_BASE64)),
                    actor=ACTOR, site=SITE,
                )
            row = operation_rows(fake)[first.id]
            row["visit_binding_json"] = row["visit_binding_json"].replace("AUDIT-1", "AUDIT-2")
            with self.assertRaises(journal.JournalIntegrityError):
                journal.load_submission(first.id)
            self.assertEqual(fake.db.commit_calls, 0)
            self.assertNotIn("File", fake.db.docs)

    def test_visit_rejects_mixed_and_noncanonical_stored_metadata(self):
        binding = contracts.VisitBinding(
            appointment="APT-1", patient="PAT-1", slot="Before", encounter=None,
            body_area="Arm", viewpoint="Front", appointment_creation="a",
            patient_creation="p", encounter_creation=None, grant_audit="AUDIT-1",
        )
        request = contracts.SaveRequest(
            kind=contracts.VISIT_ORIGINAL_CREATE, request_key="visit-key-0000002",
            visit_binding=binding, image_bytes=PNG_1X1_BYTES,
        )
        with fake_frappe() as fake:
            operation = journal.stage_submission(request, actor=ACTOR, site=SITE)
            self.assertEqual(journal.load_submission(operation.id).request, request)
            row = operation_rows(fake)[operation.id]
            for field, value in (("synthetic_patient", "P"), ("draft", "D"),
                                 ("annotation_json", "{}"), ("image_base64", "unsafe"),
                                 ("visit_binding_json", "{}")):
                old = row.get(field)
                row[field] = value
                with self.subTest(field=field), self.assertRaises(journal.JournalIntegrityError):
                    journal.load_submission(operation.id)
                row[field] = old
            row["visit_binding_json"] = " " + row["visit_binding_json"]
            with self.assertRaises(journal.JournalIntegrityError):
                journal.load_submission(operation.id)

    def test_visit_submitted_rejects_publication_evidence_but_accepts_defaults(self):
        binding = contracts.VisitBinding(
            appointment="APT-1", patient="PAT-1", slot="Before", encounter=None,
            body_area="Arm", viewpoint="Front", appointment_creation="a",
            patient_creation="p", encounter_creation=None, grant_audit="AUDIT-1",
        )
        request = contracts.SaveRequest(
            kind=contracts.VISIT_ORIGINAL_CREATE, request_key="visit-key-0000004",
            visit_binding=binding, image_bytes=PNG_1X1_BYTES,
        )
        with fake_frappe() as fake:
            operation = journal.stage_submission(request, actor=ACTOR, site=SITE)
            row = operation_rows(fake)[operation.id]
            fields = ("published_file", "published_domain_doctype", "published_domain_name",
                      "creation_identity")
            for empty in (None, ""):
                for field in fields:
                    row[field] = empty
                for empty_version in (None, "", 0):
                    row["published_version"] = empty_version
                    self.assertEqual(journal.load_submission(operation.id).request, request)
            for field in fields:
                row[field] = "unexpected"
                with self.subTest(field=field), self.assertRaises(journal.JournalIntegrityError):
                    journal.load_submission(operation.id)
                row[field] = None
            for value in (1, -1, True):
                row["published_version"] = value
                with self.subTest(published_version=value), self.assertRaises(journal.JournalIntegrityError):
                    journal.load_submission(operation.id)

    def test_legacy_submitted_publication_evidence_behavior_unchanged(self):
        request = contracts.validate_request(annotation_payload())
        with fake_frappe() as fake:
            operation = journal.stage_submission(request, actor=ACTOR, site=SITE)
            row = operation_rows(fake)[operation.id]
            row.update(published_file="legacy", published_domain_doctype="legacy",
                       published_domain_name="legacy", creation_identity="1:2",
                       published_version=1)
            self.assertEqual(journal.load_submission(operation.id).request, request)

    def test_visit_staged_byte_tampering_is_denied(self):
        binding = contracts.VisitBinding(
            appointment="APT-1", patient="PAT-1", slot="Before", encounter=None,
            body_area="Arm", viewpoint="Front", appointment_creation="a",
            patient_creation="p", encounter_creation=None, grant_audit="AUDIT-1",
        )
        request = contracts.SaveRequest(
            kind=contracts.VISIT_ORIGINAL_CREATE, request_key="visit-key-0000003",
            visit_binding=binding, image_bytes=PNG_1X1_BYTES,
        )
        with fake_frappe() as fake:
            operation = journal.stage_submission(request, actor=ACTOR, site=SITE)
            row = operation_rows(fake)[operation.id]
            row["staged_sha256"] = "0" * 64
            with self.assertRaises(journal.JournalIntegrityError):
                journal.load_submission(operation.id)

    def test_submission_survives_lost_notification_without_reserved_bytes(self):
        request = contracts.validate_request(annotation_payload())

        with fake_frappe() as fake:
            operation = journal.stage_submission(request, actor=ACTOR, site=SITE)

            self.assertIsInstance(operation, contracts.SubmittedOperation)
            self.assertEqual(operation.state, "SUBMITTED")
            self.assertRegex(operation.id, r"^[0-9a-f]{32}$")
            self.assertEqual(operation.site, SITE)
            self.assertEqual(operation.actor, ACTOR)
            self.assertEqual(operation.kind, request.kind)
            self.assertEqual(operation.request_key, request.request_key)
            self.assertEqual(operation.request, request)
            self.assertRegex(operation.payload_digest, r"^[0-9a-f]{64}$")

            rows = operation_rows(fake)
            self.assertEqual(len(rows), 1)
            row = rows[operation.id]
            self.assertEqual(
                row["identity_digest"],
                journal.identity_digest(SITE, ACTOR, request.kind, request.request_key),
            )
            self.assertEqual(row["payload_digest"], operation.payload_digest)
            self.assertEqual(row["annotation_json"], request.annotation)
            self.assertIn(row.get("image_base64"), (None, ""))
            self.assertEqual(row["state"], "SUBMITTED")
            self.assertIsNone(row.get("reservation_key"))

            # No protected key, generation, File link or receipt exists at SUBMITTED.
            self.assertNotIn("storage_key", row)
            self.assertNotIn("generation", row)
            self.assertNotIn("receipt", row)
            self.assertNotIn("original_file", row)
            self.assertNotIn("annotation_file", row)
            self.assertNotIn("File", fake.db.docs)

            # The losing notification is simply absent: the durable row stays loadable.
            self.assertEqual(fake.db.commit_calls, 0)
            self.assertEqual(fake.db.rollback_calls, 0)
            self.assertEqual(journal.load_submission(operation.id), operation)

    def test_synthetic_request_round_trips_exact_bytes_without_a_file(self):
        request = contracts.validate_request(synthetic_payload())

        with fake_frappe() as fake:
            operation = journal.stage_submission(request, actor=ACTOR, site=SITE)
            row = operation_rows(fake)[operation.id]

            # Metadata only: the row names the staged object and stores no bytes.
            self.assertEqual(
                row["staged_sha256"], hashlib.sha256(PNG_1X1_BYTES).hexdigest()
            )
            self.assertEqual(row["staged_size"], len(PNG_1X1_BYTES))
            self.assertEqual(row["staged_format"], "PNG")
            self.assertRegex(row["staged_key"], r"^[0-9a-f]{32}\.png$")
            self.assertNotIn("image_base64", row)
            self.assertIn(row.get("annotation_json"), (None, ""))
            self.assertNotIn("File", fake.db.docs)

            loaded = journal.load_submission(operation.id)
            self.assertEqual(loaded, operation)
            self.assertEqual(loaded.request.image_bytes, PNG_1X1_BYTES)
            self.assertIsNone(loaded.request.draft)

    def test_duplicate_tuple_returns_same_id_and_mismatch_cannot_replace_payload(self):
        request = contracts.validate_request(annotation_payload())

        with fake_frappe() as fake:
            first = journal.stage_submission(request, actor=ACTOR, site=SITE)
            second = journal.stage_submission(request, actor=ACTOR, site=SITE)

            self.assertEqual(first.id, second.id)
            self.assertEqual(first.payload_digest, second.payload_digest)
            self.assertEqual(len(operation_rows(fake)), 1)

            conflicting = contracts.validate_request(
                annotation_payload(annotation=annotation(id="other-shape"))
            )
            with self.assertRaises(journal.JournalConflict) as caught:
                journal.stage_submission(conflicting, actor=ACTOR, site=SITE)
            self.assertEqual(caught.exception.operation_id, first.id)

            self.assertEqual(len(operation_rows(fake)), 1)
            stored = operation_rows(fake)[first.id]
            self.assertEqual(stored["payload_digest"], first.payload_digest)
            self.assertEqual(journal.load_submission(first.id), first)

    def test_identity_tuple_is_site_actor_kind_and_request_key(self):
        with fake_frappe() as fake:
            first = journal.stage_submission(
                contracts.validate_request(annotation_payload()), actor=ACTOR, site=SITE
            )
            self.assertEqual(
                journal.identity_digest(SITE, ACTOR, "annotation_append", REQUEST_KEY),
                journal.identity_digest(SITE, ACTOR, "annotation_append", REQUEST_KEY),
            )
            for other in (
                journal.identity_digest("other.local", ACTOR, "annotation_append", REQUEST_KEY),
                journal.identity_digest(SITE, "other@example.test", "annotation_append", REQUEST_KEY),
                journal.identity_digest(SITE, ACTOR, "synthetic_original_create", REQUEST_KEY),
                journal.identity_digest(SITE, ACTOR, "annotation_append", "request-key-9999"),
            ):
                with self.subTest(other=other):
                    self.assertNotEqual(other, journal.identity_digest(
                        SITE, ACTOR, "annotation_append", REQUEST_KEY
                    ))
            self.assertEqual(len(operation_rows(fake)), 1)
            self.assertEqual(first.request_key, REQUEST_KEY)

    def test_payload_digest_uses_canonical_annotation_and_decoded_bytes(self):
        compact = contracts.validate_request(annotation_payload())
        indented = contracts.validate_request(
            json.dumps(annotation_payload(), indent=4, sort_keys=True)
        )
        self.assertEqual(journal.payload_digest(compact), journal.payload_digest(indented))

        direct_synthetic = contracts.SaveRequest(
            kind="synthetic_original_create",
            request_key=SYNTHETIC_REQUEST_KEY,
            synthetic_patient=SYNTHETIC_PATIENT,
            image_bytes=PNG_1X1_BYTES,
        )
        wire_synthetic = contracts.validate_request(synthetic_payload())
        self.assertEqual(
            journal.payload_digest(direct_synthetic), journal.payload_digest(wire_synthetic)
        )

        changed_version = contracts.validate_request(
            annotation_payload(expected_version=1)
        )
        self.assertNotEqual(
            journal.payload_digest(compact), journal.payload_digest(changed_version)
        )

        changed_image = contracts.validate_request(
            synthetic_payload(image_base64=JPEG_2X2_BASE64)
        )
        self.assertNotEqual(
            journal.payload_digest(wire_synthetic), journal.payload_digest(changed_image)
        )


class ConcurrentCollisionTests(unittest.TestCase):
    """Drive the real unique-violation recovery branch deterministically."""

    def test_unique_violation_resolves_same_payload_winner_without_mutation(self):
        request = contracts.validate_request(annotation_payload())

        for exception_name in ("UniqueValidationError", "DuplicateEntryError"):
            with self.subTest(exception=exception_name):
                with fake_frappe() as fake:
                    winner_id, winner_row = seed_hidden_winner(fake, request)
                    fake.db.unique_exception_type = getattr(fake, exception_name)

                    result = journal.stage_submission(
                        request, actor=ACTOR, site=SITE
                    )

                    self.assertEqual(result.id, winner_id)
                    self.assertEqual(result.payload_digest, winner_row["payload_digest"])
                    self.assertEqual(len(operation_rows(fake)), 1)
                    self.assertEqual(operation_rows(fake)[winner_id], winner_row)
                    self.assertEqual(fake.db.insert_attempts, 1)
                    self.assertEqual(fake.db.commit_calls, 0)
                    self.assertEqual(fake.db.rollback_calls, 0)

                    calls = operation_read_calls(fake)
                    self.assertGreaterEqual(len(calls), 2)
                    self.assertFalse(calls[0]["for_update"])
                    self.assertTrue(calls[1]["for_update"])

    def test_unique_violation_with_different_payload_conflicts_without_mutation(self):
        winner_request = contracts.validate_request(annotation_payload())
        conflicting = contracts.validate_request(
            annotation_payload(annotation=annotation(id="other-shape"))
        )

        for exception_name in ("UniqueValidationError", "DuplicateEntryError"):
            with self.subTest(exception=exception_name):
                with fake_frappe() as fake:
                    winner_id, winner_row = seed_hidden_winner(fake, winner_request)
                    fake.db.unique_exception_type = getattr(fake, exception_name)

                    with self.assertRaises(journal.JournalConflict) as caught:
                        journal.stage_submission(
                            conflicting, actor=ACTOR, site=SITE
                        )

                    self.assertEqual(caught.exception.operation_id, winner_id)
                    self.assertEqual(
                        operation_rows(fake)[winner_id], winner_row
                    )
                    self.assertEqual(len(operation_rows(fake)), 1)
                    self.assertEqual(fake.db.insert_attempts, 1)
                    self.assertEqual(fake.db.commit_calls, 0)
                    self.assertEqual(fake.db.rollback_calls, 0)

    def test_unique_violation_with_no_visible_winner_is_fixed_refusal_without_id(self):
        request = contracts.validate_request(annotation_payload())

        for exception_name in ("UniqueValidationError", "DuplicateEntryError"):
            with self.subTest(exception=exception_name):
                with fake_frappe() as fake:
                    fake.db.insert_exception = getattr(fake, exception_name)
                    fake.db.recovery_visible = False

                    with self.assertRaises(journal.JournalConflict) as caught:
                        journal.stage_submission(request, actor=ACTOR, site=SITE)

                    self.assertIsNone(caught.exception.operation_id)
                    self.assertEqual(str(caught.exception), journal.SAFE_JOURNAL_CONFLICT)
                    self.assertEqual(operation_rows(fake), {})
                    self.assertEqual(fake.db.insert_attempts, 1)
                    self.assertEqual(fake.db.commit_calls, 0)
                    self.assertEqual(fake.db.rollback_calls, 0)

                    calls = operation_read_calls(fake)
                    self.assertGreaterEqual(len(calls), 2)
                    self.assertFalse(calls[0]["for_update"])
                    self.assertTrue(calls[1]["for_update"])

    def test_deadlock_is_propagated_not_retried_or_swallowed(self):
        request = contracts.validate_request(annotation_payload())

        with fake_frappe() as fake:
            fake.db.insert_exception = fake.QueryDeadlockError

            with self.assertRaises(fake.QueryDeadlockError):
                journal.stage_submission(request, actor=ACTOR, site=SITE)

            self.assertEqual(fake.db.insert_attempts, 1)
            self.assertEqual(operation_rows(fake), {})
            self.assertEqual(fake.db.commit_calls, 0)
            self.assertEqual(fake.db.rollback_calls, 0)


class JournalAuthorityTests(unittest.TestCase):
    def assert_denied(self, **fake_kwargs):
        with fake_frappe(**fake_kwargs) as fake:
            with self.assertRaises(journal.JournalAuthorityDenied):
                journal.stage_submission(
                    contracts.validate_request(annotation_payload()),
                    actor=ACTOR,
                    site=SITE,
                )
            self.assertEqual(operation_rows(fake), {})
            self.assertEqual(fake.db.commit_calls, 0)

    def test_disabled_non_clinician_and_administrator_are_denied(self):
        self.assert_denied(enabled=0)
        self.assert_denied(roles=())
        self.assert_denied(user="Administrator", roles=("System Manager",))

    def test_site_and_session_actor_must_match_the_accepted_request(self):
        with fake_frappe() as fake:
            with self.assertRaises(journal.JournalAuthorityDenied):
                journal.stage_submission(
                    contracts.validate_request(annotation_payload()),
                    actor=ACTOR,
                    site="other.local",
                )
            with self.assertRaises(journal.JournalAuthorityDenied):
                journal.stage_submission(
                    contracts.validate_request(annotation_payload()),
                    actor="other@example.test",
                    site=SITE,
                )
            self.assertEqual(operation_rows(fake), {})

    def test_stage_rejects_a_value_that_is_not_a_typed_request(self):
        with fake_frappe():
            for value in (annotation_payload(), "{}", None):
                with self.subTest(value=type(value).__name__):
                    with self.assertRaises(journal.JournalIntegrityError):
                        journal.stage_submission(value, actor=ACTOR, site=SITE)


class JournalLoadIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.fake_context = fake_frappe()
        self.fake = self.fake_context.__enter__()
        self.request = contracts.validate_request(annotation_payload())
        self.operation = journal.stage_submission(
            self.request, actor=ACTOR, site=SITE
        )
        self.row = operation_rows(self.fake)[self.operation.id]

    def tearDown(self):
        self.fake_context.__exit__(None, None, None)

    def test_unknown_operation_is_missing(self):
        with self.assertRaises(journal.JournalOperationMissing):
            journal.load_submission("0123456789abcdef0123456789abcdef")

    def test_tampered_payload_digest_or_annotation_is_rejected(self):
        self.row["payload_digest"] = "b" * 64
        with self.assertRaises(journal.JournalIntegrityError):
            journal.load_submission(self.operation.id)

    def test_tampered_annotation_is_rejected(self):
        self.row["annotation_json"] = validation.canonical_annotation(
            annotation(id="changed")
        )
        with self.assertRaises(journal.JournalIntegrityError):
            journal.load_submission(self.operation.id)

    def test_tampered_identity_binding_is_rejected(self):
        self.row["actor"] = "other@example.test"
        with self.assertRaises(journal.JournalIntegrityError):
            journal.load_submission(self.operation.id)

    def test_non_submitted_state_is_not_loadable_as_a_submission(self):
        self.row["state"] = "PREPARED"
        with self.assertRaises(journal.JournalStateError):
            journal.load_submission(self.operation.id)

    def test_submitted_row_cannot_already_hold_a_reservation_key(self):
        self.row["reservation_key"] = "/private/files/clinic-photo-1.png"
        with self.assertRaises(journal.JournalIntegrityError):
            journal.load_submission(self.operation.id)

    def test_legacy_image_base64_on_a_staged_row_is_refused(self):
        self.fake_context.__exit__(None, None, None)
        self.fake_context = fake_frappe()
        self.fake = self.fake_context.__enter__()
        request = contracts.validate_request(synthetic_payload())
        operation = journal.stage_submission(request, actor=ACTOR, site=SITE)
        row = operation_rows(self.fake)[operation.id]
        # A legacy image-bearing shape is refused, never read back or migrated.
        row["image_base64"] = PNG_1X1_BASE64
        with self.assertRaises(journal.JournalIntegrityError):
            journal.load_submission(operation.id)


class StagedColumnDefaultTests(unittest.TestCase):
    """The persisted Int column materialises an unset ``staged_size`` as ``0``.

    A real ``Int`` column returns ``0`` (not ``None``) for a row that never set it, so
    an annotation row must read that as *absent* staged evidence while a synthetic row
    must still refuse it. Every other staged field keeps its presence rule.
    """

    def setUp(self):
        self.fake_context = fake_frappe()
        self.fake = self.fake_context.__enter__()
        self.request = contracts.validate_request(annotation_payload())
        self.operation = journal.stage_submission(
            self.request, actor=ACTOR, site=SITE
        )
        self.row = operation_rows(self.fake)[self.operation.id]

    def tearDown(self):
        self.fake_context.__exit__(None, None, None)

    def test_annotation_row_with_database_default_zero_still_loads(self):
        # Simulate the value an installed Int column returns for an unset field.
        self.row["staged_size"] = 0

        loaded = journal.load_submission(self.operation.id)

        self.assertEqual(loaded, self.operation)
        self.assertIsNone(loaded.request.image_bytes)
        self.assertEqual(loaded.request.annotation, self.request.annotation)

    def test_annotation_replay_with_database_default_zero_stays_idempotent(self):
        self.row["staged_size"] = 0

        again = journal.stage_submission(self.request, actor=ACTOR, site=SITE)

        self.assertEqual(again, self.operation)
        self.assertEqual(len(operation_rows(self.fake)), 1)

    def test_annotation_absent_variants_are_all_accepted(self):
        for value in (0, None, ""):
            with self.subTest(staged_size=repr(value)):
                self.row["staged_size"] = value
                self.assertEqual(
                    journal.load_submission(self.operation.id), self.operation
                )

    def test_annotation_staged_evidence_is_still_refused(self):
        for overrides in (
            {"staged_key": f"{'a' * 32}.png"},
            {"staged_sha256": "b" * 64},
            {"staged_format": "PNG"},
            {"staged_size": 1024},
            {"staged_size": "12"},
            {"staged_size": True},
            {"staged_size": False},
        ):
            with self.subTest(overrides=overrides):
                row = self.row
                for fieldname, value in overrides.items():
                    row[fieldname] = value
                with self.assertRaises(journal.JournalIntegrityError):
                    journal.load_submission(self.operation.id)
                for fieldname in overrides:
                    row[fieldname] = None

    def test_annotation_row_with_padded_legacy_payload_is_refused(self):
        self.row["staged_size"] = 0
        self.row["image_base64"] = "   "

        with self.assertRaises(journal.JournalIntegrityError):
            journal.load_submission(self.operation.id)

    def test_synthetic_row_with_database_default_zero_is_refused(self):
        for value in (0, None, "", False, True, -1):
            with self.subTest(staged_size=repr(value)):
                with fake_frappe() as fake:
                    request = contracts.validate_request(synthetic_payload())
                    operation = journal.stage_submission(
                        request, actor=ACTOR, site=SITE
                    )
                    row = operation_rows(fake)[operation.id]
                    row["staged_size"] = value
                    with self.assertRaises(journal.JournalIntegrityError):
                        journal.load_submission(operation.id)


class AdvancedStateResolutionTests(unittest.TestCase):
    """A retried tuple must resolve its original ID after the row advances."""

    ADVANCED_STATES = ("PREPARED", "PUBLISHED", "ABORTED", "QUARANTINED")
    RESERVATIONS = (None, "/private/files/clinic-photo-1.png")

    def test_same_payload_advanced_state_is_explicit_state_error_with_original_id(self):
        request = contracts.validate_request(annotation_payload())

        for state in self.ADVANCED_STATES:
            for reservation in self.RESERVATIONS:
                with self.subTest(state=state, reservation=reservation):
                    with fake_frappe() as fake:
                        operation = journal.stage_submission(
                            request, actor=ACTOR, site=SITE
                        )
                        row = operation_rows(fake)[operation.id]
                        row["state"] = state
                        row["reservation_key"] = reservation

                        with self.assertRaises(journal.JournalStateError) as caught:
                            journal.stage_submission(
                                request, actor=ACTOR, site=SITE
                            )

                        self.assertEqual(caught.exception.operation_id, operation.id)
                        self.assertEqual(caught.exception.state, state)
                        self.assertEqual(
                            str(caught.exception), journal.SAFE_JOURNAL_STATE
                        )
                        self.assertNotIsInstance(
                            caught.exception, journal.JournalIntegrityError
                        )
                        self.assertNotIn("shape-1", str(caught.exception))
                        self.assertNotIn(request.annotation, str(caught.exception))
                        self.assertEqual(row["state"], state)
                        self.assertEqual(row["reservation_key"], reservation)
                        self.assertEqual(
                            row["payload_digest"], operation.payload_digest
                        )
                        self.assertEqual(fake.db.commit_calls, 0)
                        self.assertEqual(fake.db.rollback_calls, 0)

    def test_changed_payload_advanced_state_is_conflict_with_original_id(self):
        request = contracts.validate_request(annotation_payload())
        conflicting = contracts.validate_request(
            annotation_payload(annotation=annotation(id="other-shape"))
        )

        for state in self.ADVANCED_STATES:
            with self.subTest(state=state):
                with fake_frappe() as fake:
                    operation = journal.stage_submission(
                        request, actor=ACTOR, site=SITE
                    )
                    row = operation_rows(fake)[operation.id]
                    row["state"] = state
                    row["reservation_key"] = "/private/files/clinic-photo-1.png"

                    with self.assertRaises(journal.JournalConflict) as caught:
                        journal.stage_submission(
                            conflicting, actor=ACTOR, site=SITE
                        )

                    self.assertEqual(caught.exception.operation_id, operation.id)
                    self.assertEqual(
                        str(caught.exception), journal.SAFE_JOURNAL_CONFLICT
                    )
                    self.assertEqual(row["state"], state)
                    self.assertEqual(
                        row["payload_digest"], operation.payload_digest
                    )
                    self.assertEqual(fake.db.commit_calls, 0)
                    self.assertEqual(fake.db.rollback_calls, 0)

    def test_load_submission_remains_strict_submitted_type(self):
        request = contracts.validate_request(annotation_payload())

        for state in self.ADVANCED_STATES:
            for reservation in self.RESERVATIONS:
                with self.subTest(state=state, reservation=reservation):
                    with fake_frappe() as fake:
                        operation = journal.stage_submission(
                            request, actor=ACTOR, site=SITE
                        )
                        row = operation_rows(fake)[operation.id]
                        row["state"] = state
                        row["reservation_key"] = reservation

                        with self.assertRaises(journal.JournalStateError) as caught:
                            journal.load_submission(operation.id)

                        self.assertEqual(caught.exception.operation_id, operation.id)
                        self.assertEqual(caught.exception.state, state)
                        self.assertEqual(
                            str(caught.exception), journal.SAFE_JOURNAL_STATE
                        )

    def test_unknown_state_is_integrity_refusal_not_a_state_outcome(self):
        request = contracts.validate_request(annotation_payload())

        with fake_frappe() as fake:
            operation = journal.stage_submission(
                request, actor=ACTOR, site=SITE
            )
            row = operation_rows(fake)[operation.id]
            row["state"] = "BOGUS"

            for call in (
                lambda: journal.stage_submission(
                    request, actor=ACTOR, site=SITE
                ),
                lambda: journal.load_submission(operation.id),
            ):
                with self.subTest(call=call):
                    with self.assertRaises(journal.JournalIntegrityError):
                        call()

    def test_submitted_row_with_allocated_reservation_is_integrity_refusal(self):
        request = contracts.validate_request(annotation_payload())

        with fake_frappe() as fake:
            operation = journal.stage_submission(
                request, actor=ACTOR, site=SITE
            )
            row = operation_rows(fake)[operation.id]
            row["reservation_key"] = "/private/files/clinic-photo-1.png"

            with self.assertRaises(journal.JournalIntegrityError):
                journal.stage_submission(request, actor=ACTOR, site=SITE)
            with self.assertRaises(journal.JournalIntegrityError):
                journal.load_submission(operation.id)


class LaterStageRefusalTests(unittest.TestCase):
    def test_publish_refuses_a_non_prepared_operation_and_stages_nothing(self):
        with fake_frappe() as fake:
            operation = journal.stage_submission(
                contracts.validate_request(annotation_payload()),
                actor=ACTOR,
                site=SITE,
            )
            # Task 8 publication is a transition of this attempt's *prepared* evidence
            # under its live owner only; a SUBMITTED handle is not publishable.
            with self.assertRaises(journal.JournalIntegrityError):
                journal.publish(operation, None, "unused", None)

            self.assertEqual(len(operation_rows(fake)), 1)
            self.assertEqual(
                operation_rows(fake)[operation.id]["reservation_key"], None
            )
            self.assertEqual(operation_rows(fake)[operation.id]["state"], "SUBMITTED")
            self.assertEqual(fake.db.commit_calls, 0)

    def test_prepare_refuses_without_the_locked_owner(self):
        from clinic_photos.publication import exclusion

        with fake_frappe() as fake:
            operation = journal.stage_submission(
                contracts.validate_request(annotation_payload()),
                actor=ACTOR,
                site=SITE,
            )
            # Preparation is implemented, but only inside the owner's
            # operation/target exclusion. An unowned call is a refusal and stages
            # nothing.
            with self.assertRaises(exclusion.ExclusionDenied):
                journal.prepare(operation)

            self.assertEqual(operation_rows(fake)[operation.id]["state"], "SUBMITTED")
            self.assertIn(
                operation_rows(fake)[operation.id].get("reservation_key"), (None, "")
            )
            self.assertEqual(fake.db.commit_calls, 0)
            self.assertEqual(fake.db.rollback_calls, 0)


class OperationSchemaTests(unittest.TestCase):
    def test_visit_controller_requires_exact_repository_scope(self):
        controller_name = (
            "clinic_photos.clinic_photos.doctype."
            "clinic_photo_save_operation.clinic_photo_save_operation"
        )
        binding = contracts.VisitBinding(
            appointment="APT", patient="PAT", slot="Before", encounter=None,
            body_area="Arm", viewpoint="Front", appointment_creation="ac",
            patient_creation="pc", encounter_creation=None, grant_audit="GRANT",
        )
        request = contracts.SaveRequest(kind=contracts.VISIT_ORIGINAL_CREATE,
            request_key="visit-controller-01", visit_binding=binding, image_bytes=PNG_1X1_BYTES)
        model = types.ModuleType("frappe.model")
        document = types.ModuleType("frappe.model.document")
        document.Document = type("Document", (), {})
        with fake_frappe(roles=("Healthcare Administrator",)) as fake, \
             patch.dict(sys.modules, {"frappe.model": model, "frappe.model.document": document}):
            sys.modules.pop(controller_name, None)
            controller = importlib.import_module(controller_name)
            class Doc(controller.ClinicPhotoSaveOperation):
                kind = contracts.VISIT_ORIGINAL_CREATE
                def get(self, key):
                    return values.get(key)
            values = journal._document_values(request, actor=ACTOR, site=SITE,
                identity=journal.identity_digest(SITE, ACTOR, request.kind, request.request_key),
                payload=journal.payload_digest(request), staged=None)
            doc = Doc()
            with self.assertRaises(fake.PermissionError):
                doc.before_insert()
            with journal._visit_submission_insertion(doc, request, ACTOR, SITE, values):
                doc.before_insert()
                with self.assertRaises(fake.PermissionError):
                    Doc().before_insert()
            sys.modules.pop(controller_name, None)

    @staticmethod
    def schema():
        path = (
            Path(__file__).resolve().parents[2]
            / "clinic_photos"
            / "clinic_photos"
            / "doctype"
            / "clinic_photo_save_operation"
            / "clinic_photo_save_operation.json"
        )
        return json.loads(path.read_text(encoding="utf-8"))

    def test_schema_declares_no_client_permissions_and_server_naming(self):
        data = self.schema()

        self.assertEqual(data["doctype"], "DocType")
        self.assertEqual(data["name"], "Clinic Photo Save Operation")
        self.assertEqual(data["module"], "Clinic Photos")
        self.assertEqual(data["permissions"], [])
        self.assertEqual(data["allow_rename"], 0)
        self.assertEqual(data["allow_import"], 0)
        self.assertEqual(data["naming_rule"], "By script")
        self.assertFalse(data.get("autoname"))

    def test_schema_has_identity_uniqueness_and_nullable_reservation_key(self):
        fields = {field["fieldname"]: field for field in self.schema()["fields"]}

        for fieldname in (
            "site",
            "actor",
            "kind",
            "request_key",
            "identity_digest",
            "payload_digest",
            "state",
            "draft",
            "expected_version",
            "original",
            "synthetic_patient",
            "annotation_json",
            "staged_key",
            "staged_sha256",
            "staged_size",
            "staged_format",
            "image_base64",
            "reservation_key",
        ):
            self.assertIn(fieldname, fields)

        self.assertEqual(fields["identity_digest"]["unique"], 1)
        self.assertEqual(fields["reservation_key"]["unique"], 1)
        self.assertNotIn("default", fields["reservation_key"])
        self.assertEqual(fields["annotation_json"]["fieldtype"], "Long Text")
        self.assertEqual(fields["state"]["default"], "SUBMITTED")

        # Metadata-only staged reference: server-owned, never defaulted, never bytes.
        self.assertEqual(fields["staged_key"]["fieldtype"], "Data")
        self.assertEqual(fields["staged_sha256"]["length"], 64)
        self.assertEqual(fields["staged_size"]["fieldtype"], "Int")
        self.assertEqual(fields["staged_format"]["fieldtype"], "Data")
        for fieldname in ("staged_key", "staged_sha256", "staged_size", "staged_format"):
            with self.subTest(fieldname=fieldname):
                self.assertEqual(fields[fieldname]["read_only"], 1)
                self.assertNotIn("default", fields[fieldname])

        # The legacy image-bearing column stays declared and read-only so existing
        # data is neither cleared nor silently interpreted.
        self.assertEqual(fields["image_base64"]["fieldtype"], "Long Text")
        self.assertEqual(fields["image_base64"]["read_only"], 1)

        # Task 6 persists the approved PREPARED evidence on this same record; the
        # fields exist but a SUBMITTED row never populates them (asserted below and in
        # the boundary tests).
        for evidence in ("generation", "expected_sha256", "expected_size"):
            with self.subTest(evidence=evidence):
                self.assertIn(evidence, fields)
                self.assertEqual(fields[evidence]["read_only"], 1)
                self.assertNotIn("default", fields[evidence])

        for absent in (
            "storage_key",
            "receipt",
            "file",
            "original_file",
            "annotation_file",
        ):
            self.assertNotIn(absent, fields)

    def test_schema_declares_the_durable_publication_outcome_fields(self):
        """PUBLISHED carries File/domain/version/creation evidence on the same row."""
        fields = {field["fieldname"]: field for field in self.schema()["fields"]}

        for fieldname in (
            "published_file",
            "published_domain_doctype",
            "published_domain_name",
            "published_version",
            "creation_identity",
        ):
            with self.subTest(fieldname=fieldname):
                self.assertIn(fieldname, fields)
                self.assertEqual(fields[fieldname]["read_only"], 1)
                self.assertNotIn("default", fields[fieldname])
                self.assertEqual(fields[fieldname].get("reqd", 0), 0)

        self.assertEqual(fields["published_version"]["fieldtype"], "Int")
        for text in (
            "published_file",
            "published_domain_doctype",
            "published_domain_name",
            "creation_identity",
        ):
            with self.subTest(fieldname=text):
                self.assertEqual(fields[text]["fieldtype"], "Data")

        # The expected byte evidence is reused, never duplicated: equality with the
        # actual object is what makes the reuse safe.
        for duplicate in ("published_sha256", "published_size"):
            self.assertNotIn(duplicate, fields)

    def test_controller_generates_server_hex_name_and_rejects_mutation(self):
        controller_name = (
            "clinic_photos.clinic_photos.doctype."
            "clinic_photo_save_operation.clinic_photo_save_operation"
        )
        model = types.ModuleType("frappe.model")
        document = types.ModuleType("frappe.model.document")
        base = type("Document", (), {})
        document.Document = base
        model.document = document

        with fake_frappe() as fake:
            fake.model = model
            sys.modules["frappe.model"] = model
            sys.modules["frappe.model.document"] = document
            sys.modules.pop(controller_name, None)
            try:
                controller = importlib.import_module(controller_name)

                class FakeDocument(controller.ClinicPhotoSaveOperation):
                    def __init__(self):
                        self.name = None
                        self._new = True

                    def is_new(self):
                        return self._new

                document_instance = FakeDocument()
                document_instance.autoname()
                self.assertRegex(document_instance.name, r"^[0-9a-f]{32}$")

                controller.ClinicPhotoSaveOperation.before_insert(document_instance)
                with self.assertRaises(fake.PermissionError) as caught:
                    controller.ClinicPhotoSaveOperation.on_trash(document_instance)
                self.assertEqual(
                    str(caught.exception), controller.SAFE_NOT_DELETABLE
                )

                document_instance._new = False
                with self.assertRaises(fake.PermissionError):
                    document_instance.validate()

                fake.session.user = "Administrator"
                with self.assertRaises(fake.PermissionError) as caught:
                    controller.ClinicPhotoSaveOperation.before_insert(
                        document_instance
                    )
                self.assertEqual(
                    str(caught.exception), controller.SAFE_SERVER_OWNED
                )
                with self.assertRaises(fake.PermissionError) as caught:
                    controller.ClinicPhotoSaveOperation.on_trash(
                        document_instance
                    )
                self.assertEqual(
                    str(caught.exception), controller.SAFE_NOT_DELETABLE
                )
                fake.session.user = ACTOR

                request = contracts.validate_request(annotation_payload())
                class FakeNewRow(dict):
                    def is_new(self):
                        return True

                valid_row = FakeNewRow(
                    {
                        "name": uuid.uuid4().hex,
                        "site": SITE,
                        "actor": ACTOR,
                        "kind": request.kind,
                        "request_key": request.request_key,
                        "identity_digest": journal.identity_digest(
                            SITE, ACTOR, request.kind, request.request_key
                        ),
                        "payload_digest": journal.payload_digest(request),
                        "state": "SUBMITTED",
                        "draft": request.draft,
                        "expected_version": request.expected_version,
                        "original": request.original,
                        "synthetic_patient": None,
                        "annotation_json": request.annotation,
                        "image_base64": None,
                        "reservation_key": None,
                    }
                )
                controller.ClinicPhotoSaveOperation.validate(valid_row)
                valid_row["payload_digest"] = "b" * 64
                with self.assertRaises(journal.JournalIntegrityError):
                    controller.ClinicPhotoSaveOperation.validate(valid_row)
            finally:
                sys.modules.pop(controller_name, None)
                sys.modules.pop("frappe.model.document", None)
                sys.modules.pop("frappe.model", None)


class NativeJournalChecksPending(unittest.TestCase):
    """These require the real development site; local fakes prove nothing here."""

    @unittest.skip(
        "pending native migration/check: identity_digest unique constraint and "
        "multiple NULL reservation_key values must be proved on the development site"
    )
    def test_native_unique_identity_and_nullable_reservation_key(self):
        self.fail("native unique-constraint check has not run")

    @unittest.skip(
        "pending native transaction check: rollback must leave no runnable SUBMITTED row"
    )
    def test_native_rollback_leaves_no_runnable_submission(self):
        self.fail("native rollback check has not run")

    @unittest.skip(
        "pending native permission check: ordinary clinical client list/read/export/CRUD "
        "denial must be observed through installed APIs"
    )
    def test_native_ordinary_clinical_client_access_is_denied(self):
        self.fail("native ordinary-client permission check has not run")

    @unittest.skip(
        "pending native permission check: trusted Administrator operation "
        "read/list/report/export is permitted, while Administrator create/update/delete "
        "remain denied"
    )
    def test_native_administrator_read_exception_and_mutation_denial(self):
        self.fail("native Administrator permission check has not run")

    @unittest.skip(
        "pending native concurrency check: simultaneous same-tuple and "
        "different-tuple submissions must not deadlock; the unique winner remains "
        "loadable and the loser resolves or refuses without blind retry"
    )
    def test_native_simultaneous_staging_does_not_deadlock(self):
        self.fail("native simultaneous-staging check has not run")

    @unittest.skip(
        "pending native transaction check: a failed Document.insert must not roll "
        "back or otherwise finalize the caller's unrelated work, and the unique "
        "competition winner must remain loadable"
    )
    def test_native_failed_insert_preserves_caller_transaction(self):
        self.fail("native failed-insert transaction check has not run")


if __name__ == "__main__":
    unittest.main()
