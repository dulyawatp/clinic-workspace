"""Focused tests for Task 9 current-access status, replay and recovery inspection.

The suite drives the real own code: the real journal, real exclusion owner, real
contracts/validation, the real protected writer and the real DocType controllers over
a narrow framework boundary. Two proven harnesses are reused as fixtures for the two
different execution shapes:

* ``test_async_publication_submission`` supplies the authenticated request boundary
  (native session, stored CSRF token, enabled gate, permission map) and stages
  SUBMITTED rows through the public API;
* ``test_async_publication_coordinator`` supplies the committed/transactional split,
  a real sites volume and real protected bytes, so a genuine PREPARED reservation and
  a genuine PUBLISHED outcome can be produced by the real coordinator rather than
  seeded by hand.

``inspection`` is imported inside the tests, not at module import, so the pre-Task-9
baseline still fails these tests behaviourally (status refuses where it must report a
known outcome) instead of collapsing the module with one ``ImportError``.

No test claims native acceptance: installed Frappe sessions/CSRF, MariaDB isolation,
native DocPerms, the effective hook map and the real image library remain pending
native evidence.
"""

import contextlib
import copy
import os
import tempfile
import types
import unittest
from unittest import mock

import test_async_publication_coordinator as publication_harness
import test_async_publication_submission as submission_harness

from clinic_photos.access import frappe_context
from clinic_photos.api import photo_save
from clinic_photos.publication import exclusion, journal, writer

sub = submission_harness
publication = publication_harness

AUTHOR = sub.AUTHOR
OTHER = sub.OTHER
DRAFT = sub.DRAFT
PHOTO = sub.PHOTO
PATIENT = sub.PATIENT
SITE = sub.SITE
CSRF = sub.CSRF
CSRF_HEADER = sub.CSRF_HEADER
SID = sub.SID

VERSION_DOCTYPE = publication.VERSION_DOCTYPE

AUDIT_FIELDS = frozenset(
    {
        "operation_id",
        "state",
        "kind",
        "payload_digest",
        "expected_sha256",
        "outcome",
        "consistent",
        "bytes_verified",
    }
)


def inspection():
    """Per-test import so the pre-Task-9 baseline fails behaviourally, not on import."""
    from clinic_photos.publication import inspection as module

    return module


def operation_rows(frappe):
    return frappe.db.docs.get(journal.OPERATION_DOCTYPE, {})


def copy_docs(frappe):
    return {
        doctype: {name: dict(row) for name, row in rows.items()}
        for doctype, rows in frappe.db.docs.items()
    }


def second_draft(frappe, name="DRAFT-2", author=AUTHOR):
    frappe.db.docs.setdefault("Clinic Photo Draft", {})[name] = {
        "name": name,
        "author": author,
        "photo": PHOTO,
        "current_version_number": 1,
        "current_version": None,
    }
    return name


def stored_row(
    frappe, *, actor=AUTHOR, state="SUBMITTED", request_key="operator-audit-key-01"
):
    request = sub.validation.validate_request(
        sub.annotation_payload(request_key=request_key)
    )
    row = sub.stored_operation(request, actor=actor, site=SITE, state=state)
    frappe.db.docs.setdefault(journal.OPERATION_DOCTYPE, {})[row["name"]] = row
    return row


@contextlib.contextmanager
def photo_session(fake, *, user=AUTHOR, sid=SID, csrf=CSRF, header=CSRF):
    """Native session/CSRF shape over the coordinator harness's framework double.

    Entered only after any publication call: the coordinator's child entry refuses a
    frame that already carries a request, which is exactly the property under test
    elsewhere.
    """
    fake.session = types.SimpleNamespace(
        user=user, sid=sid, data=types.SimpleNamespace(csrf_token=csrf)
    )
    headers = {CSRF_HEADER: header}
    fake.local.site = publication.SITE
    fake.local.request = types.SimpleNamespace(
        method="POST", cookies={"sid": sid}, headers=headers
    )
    fake.get_request_header = lambda name, default=None: headers.get(name, default)
    fake.form_dict = {}
    try:
        yield fake
    finally:
        fake.local.request = None


class CurrentAccessStatusTests(unittest.TestCase):
    """Ordinary status/replay is actor-only, read-only and never fabricates saved."""

    def stage(self, frappe, payload=None):
        outcome = photo_save.save(
            sub.annotation_payload() if payload is None else payload
        )
        self.assertEqual(outcome["status"], "pending")
        return outcome

    def test_current_actor_status_reports_pending_without_any_write(self):
        with sub.runtime() as frappe:
            sub.enable(frappe)
            sub.grant_target(frappe)
            outcome = self.stage(frappe)
            before = copy_docs(frappe)

            status = photo_save.status(outcome["operation_id"])

            self.assertEqual(
                status,
                {"status": "pending", "operation_id": outcome["operation_id"]},
            )
            self.assertNotIn("version", status)
            self.assertNotIn("file_name", status)
            self.assertEqual(copy_docs(frappe), before)
            self.assertNotIn("File", frappe.db.docs)

    def test_status_refuses_another_actors_pending_metadata(self):
        with sub.runtime() as frappe:
            sub.enable(frappe)
            sub.grant_target(frappe)
            other_row = stored_row(
                frappe, actor=OTHER, request_key="other-request-key-01"
            )
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
                    self.assertEqual(
                        str(caught.exception), photo_save.SAFE_STATUS_DENIED
                    )
                    refusals.append(str(caught.exception))

            self.assertEqual(len(set(refusals)), 1)
            for secret in (
                PATIENT,
                DRAFT,
                PHOTO,
                "annotation",
                "private",
                "SUBMITTED",
                OTHER,
            ):
                self.assertNotIn(secret, refusals[0])
            self.assertEqual(copy_docs(frappe), before)

    def test_status_denies_when_current_target_authority_is_gone(self):
        with sub.runtime() as frappe:
            sub.enable(frappe)
            sub.grant_target(frappe)
            outcome = self.stage(frappe)
            frappe.db.docs["Clinic Photo Draft"][DRAFT]["author"] = OTHER
            before = copy_docs(frappe)

            with self.assertRaises(frappe.PermissionError) as caught:
                photo_save.status(outcome["operation_id"])

            self.assertEqual(str(caught.exception), photo_save.SAFE_STATUS_DENIED)
            self.assertEqual(copy_docs(frappe), before)

    def test_status_refuses_inconsistent_advanced_rows_opaquely(self):
        with sub.runtime() as frappe:
            sub.enable(frappe)
            sub.grant_target(frappe)
            outcome = self.stage(frappe)
            row = operation_rows(frappe)[outcome["operation_id"]]
            original = dict(row)

            tampered = (
                {"state": "PREPARED", "reservation_key": "/private/files/clinic-1.json"},
                {"state": "PUBLISHED"},
                {"state": "ABORTED", "reservation_key": "/private/files/clinic-2.json"},
                {"state": "NOT-A-STATE"},
            )
            try:
                for patch in tampered:
                    with self.subTest(patch=patch):
                        row.update(patch)
                        with self.assertRaises(frappe.PermissionError) as caught:
                            photo_save.status(outcome["operation_id"])
                        self.assertEqual(
                            str(caught.exception), photo_save.SAFE_STATUS_DENIED
                        )
            finally:
                row.clear()
                row.update(original)
            self.assertEqual(row, original)

    def test_aborted_and_quarantined_report_unknown_without_inventing_outcomes(self):
        for state in ("ABORTED", "QUARANTINED"):
            with self.subTest(state=state), sub.runtime() as frappe:
                sub.enable(frappe)
                sub.grant_target(frappe)
                row = stored_row(frappe, state=state)
                before = copy_docs(frappe)

                status = photo_save.status(row["name"])

                self.assertEqual(
                    status, {"status": "unknown", "operation_id": row["name"]}
                )
                self.assertNotIn("version", status)
                self.assertEqual(copy_docs(frappe), before)


class ReplayAuthorizationTests(unittest.TestCase):
    """Replay resolves the original tuple only under present current authority."""

    def test_revoked_author_cannot_replay_saved(self):
        with sub.runtime() as frappe:
            sub.enable(frappe)
            sub.grant_target(frappe)

            first = photo_save.save(sub.annotation_payload())
            replay = photo_save.save(sub.annotation_payload())
            self.assertEqual(first, replay)
            self.assertEqual(first["status"], "pending")
            self.assertEqual(len(operation_rows(frappe)), 1)
            operation = operation_rows(frappe)[first["operation_id"]]
            stored = dict(operation)

            # The same tuple/digest resolves the same operation only while the
            # author's present authority holds: this call is the replay resolution.
            self.assertEqual(
                photo_save.status(first["operation_id"]),
                {"status": "pending", "operation_id": first["operation_id"]},
            )

            # The author is revoked between the accepted submission and any replay.
            frappe.db.docs["Clinic Photo Draft"][DRAFT]["author"] = OTHER
            before = copy_docs(frappe)

            changed = sub.annotation_payload(
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
            for payload in (sub.annotation_payload(), changed):
                with self.subTest(payload=payload["request_key"]):
                    with self.assertRaises(frappe.PermissionError) as caught:
                        photo_save.save(payload)
                    self.assertEqual(str(caught.exception), photo_save.SAFE_DENIAL)

            with self.assertRaises(frappe.PermissionError) as status_caught:
                photo_save.status(first["operation_id"])
            self.assertEqual(
                str(status_caught.exception), photo_save.SAFE_STATUS_DENIED
            )
            denial = str(status_caught.exception).lower()
            for secret in ("pending", "saved", "version", "annotation", "private"):
                self.assertNotIn(secret, denial)

            # Nothing was rewritten, no key was allocated and no byte was created.
            self.assertEqual(operation_rows(frappe)[first["operation_id"]], stored)
            self.assertEqual(len(operation_rows(frappe)), 1)
            self.assertEqual(operation["state"], journal.SUBMITTED_STATE)
            self.assertIsNone(operation["reservation_key"])
            self.assertNotIn("File", frappe.db.docs)
            self.assertEqual(copy_docs(frappe), before)

    def test_changed_payload_rejects_without_replacing_the_stored_request(self):
        with sub.runtime() as frappe:
            sub.enable(frappe)
            sub.grant_target(frappe)
            first = photo_save.save(sub.annotation_payload())
            stored = dict(operation_rows(frappe)[first["operation_id"]])

            conflict = photo_save.save(
                sub.annotation_payload(
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
            )

            self.assertEqual(
                conflict,
                {"status": "conflict", "operation_id": first["operation_id"]},
            )
            self.assertEqual(operation_rows(frappe)[first["operation_id"]], stored)
            self.assertEqual(len(operation_rows(frappe)), 1)
            self.assertNotIn("File", frappe.db.docs)

    def test_two_author_drafts_sharing_one_original_stay_independent(self):
        with sub.runtime() as frappe:
            sub.enable(frappe)
            sub.grant_target(frappe)
            other_draft = second_draft(frappe)
            sub.grant_target(frappe, draft=other_draft)

            first = photo_save.save(sub.annotation_payload())
            second = photo_save.save(
                sub.annotation_payload(
                    draft=other_draft, request_key="request-key-0002"
                )
            )

            self.assertEqual(first["status"], "pending")
            self.assertEqual(second["status"], "pending")
            self.assertNotEqual(first["operation_id"], second["operation_id"])
            rows = operation_rows(frappe)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[first["operation_id"]]["draft"], DRAFT)
            self.assertEqual(rows[second["operation_id"]]["draft"], other_draft)
            self.assertEqual(
                photo_save.status(first["operation_id"]),
                {"status": "pending", "operation_id": first["operation_id"]},
            )
            self.assertEqual(
                photo_save.status(second["operation_id"]),
                {"status": "pending", "operation_id": second["operation_id"]},
            )

    def test_same_tuple_from_another_actor_never_matches_the_first_actor(self):
        with sub.runtime() as frappe:
            sub.enable(frappe)
            sub.grant_target(frappe)
            first = photo_save.save(sub.annotation_payload())

        with sub.runtime(user=OTHER) as frappe:
            sub.enable(frappe)
            frappe.db.docs["Clinic Photo Draft"][DRAFT]["author"] = OTHER
            sub.grant_target(frappe, draft=DRAFT)

            other = photo_save.save(sub.annotation_payload())

            self.assertEqual(other["status"], "pending")
            self.assertNotEqual(other["operation_id"], first["operation_id"])
            rows = operation_rows(frappe)
            self.assertEqual(rows[other["operation_id"]]["actor"], OTHER)
            with self.assertRaises(frappe.PermissionError) as caught:
                photo_save.status(first["operation_id"])
            self.assertEqual(str(caught.exception), photo_save.SAFE_STATUS_DENIED)


class DurableConflictAndPublishedTests(unittest.TestCase):
    """Coordinator-produced states resolved later by a read-only status call."""

    def annotation_environment(self, root, *, version=0):
        sites_path = publication.sites_volume(root)
        fake = self.enterContext(publication.fake_runtime(sites_path=sites_path))
        publication.seed_actor(fake)
        publication.seed_patient(fake)
        publication.seed_annotation_target(fake, version=version)
        allow_saved_reads(fake)
        return fake, sites_path

    def status(self, fake, operation_id):
        with saved_runtime(fake):
            return photo_save.status(operation_id)

    def test_stale_same_draft_version_conflicts_before_bytes_then_unknown(self):
        with tempfile.TemporaryDirectory() as root:
            fake, sites_path = self.annotation_environment(root, version=3)
            operation = publication.stage_committed(
                fake, publication.annotation_request()
            )

            outcome = publication.publish(fake, operation)

            self.assertEqual(outcome.status, "conflict")
            row = operation_rows(fake)[operation.id]
            self.assertEqual(row["state"], "ABORTED")
            self.assertIsNone(row["reservation_key"])
            self.assertNotIn("File", fake.db.docs)
            self.assertEqual(os.listdir(publication.private_root(sites_path)), [])

            status = self.status(fake, operation.id)

            # ABORTED is durable, but the record does not record *which* definite
            # refusal it was, so no rejected/conflict outcome is invented here.
            self.assertEqual(
                status, {"status": "unknown", "operation_id": operation.id}
            )
            self.assertEqual(operation_rows(fake)[operation.id]["state"], "ABORTED")
            self.assertEqual(os.listdir(publication.private_root(sites_path)), [])

    def test_published_outcome_requires_the_ownerless_byte_proof(self):
        with tempfile.TemporaryDirectory() as root:
            fake, sites_path = self.annotation_environment(root)
            operation = publication.stage_committed(
                fake, publication.annotation_request()
            )

            published = publication.publish(fake, operation)
            self.assertEqual(published.status, "saved")
            files = fake.db.docs["File"]
            self.assertEqual(len(files), 1)
            file_name = next(iter(files))
            key = files[file_name]["file_url"]
            object_path = publication.target_path(sites_path, key)
            self.assertTrue(os.path.isfile(object_path))
            with open(object_path, "rb") as handle:
                bytes_before = handle.read()

            status = self.status(fake, operation.id)

            # The shared, side-effect-free descriptor/hash/identity proof now runs with
            # no publication attempt, so the known outcome is the immutable version —
            # never a re-read, re-derivation or second write.
            self.assertEqual(
                status,
                {
                    "status": "saved",
                    "operation_id": operation.id,
                    "version": 1,
                    "file_name": file_name,
                },
            )
            self.assertEqual(operation_rows(fake)[operation.id]["state"], "PUBLISHED")
            with open(object_path, "rb") as handle:
                self.assertEqual(handle.read(), bytes_before)
            self.assertEqual(len(fake.db.docs["File"]), 1)
            self.assertEqual(len(fake.db.docs[publication.VERSION_DOCTYPE]), 1)

    def test_published_tampering_never_produces_a_known_saved_outcome(self):
        with tempfile.TemporaryDirectory() as root:
            fake, sites_path = self.annotation_environment(root)
            operation = publication.stage_committed(
                fake, publication.annotation_request()
            )
            self.assertEqual(publication.publish(fake, operation).status, "saved")
            file_name = next(iter(fake.db.docs["File"]))

            for patch in (
                {"file_size": 1},
                {"is_private": 0},
                {"owner": OTHER},
                {"file_url": "/private/files/clinic-annotation-other.json"},
            ):
                with self.subTest(patch=patch):
                    original = dict(fake.db.docs["File"][file_name])
                    fake.db.docs["File"][file_name].update(patch)
                    try:
                        status = self.status(fake, operation.id)
                    finally:
                        fake.db.docs["File"][file_name].clear()
                        fake.db.docs["File"][file_name].update(original)
                    self.assertEqual(status["status"], "unknown")
                    self.assertNotIn("version", status)

            # A tampered persisted outcome is integrity evidence, not a saved claim.
            row = operation_rows(fake)[operation.id]
            original_row = dict(row)
            row["published_version"] = 99
            try:
                with self.assertRaises(fake.PermissionError) as caught:
                    self.status(fake, operation.id)
            finally:
                row.clear()
                row.update(original_row)
            self.assertEqual(str(caught.exception), photo_save.SAFE_STATUS_DENIED)
            self.assertNotIn("saved", str(caught.exception).lower())
            self.assertEqual(row, original_row)


class PreparedUncertaintyTests(unittest.TestCase):
    """An unresolved PREPARED attempt is never resumed, repaired or purged here."""

    def status(self, fake, operation_id):
        with photo_session(fake):
            return photo_save.status(operation_id)

    def test_prepared_unknown_never_resumes_or_purges(self):
        with tempfile.TemporaryDirectory() as root:
            sites_path = publication.sites_volume(root)
            fake = self.enterContext(publication.fake_runtime(sites_path=sites_path))
            publication.seed_actor(fake)
            publication.seed_patient(fake)
            publication.seed_annotation_target(fake)
            publication.allow_permissions(fake)

            operation = publication.stage_committed(
                fake, publication.annotation_request()
            )
            target = exclusion.target_identity_for_operation(operation.id)
            with exclusion.hold_exclusion(operation.id, target):
                prepared = journal.prepare(operation)
            fake.db.commit()

            row = operation_rows(fake)[operation.id]
            self.assertEqual(row["state"], "PREPARED")
            key = row["reservation_key"]
            self.assertEqual(key, prepared.key)
            self.assertEqual(os.listdir(publication.private_root(sites_path)), [])
            self.assertNotIn("File", fake.db.docs)
            self.assertNotIn(VERSION_DOCTYPE, fake.db.docs)
            snapshot = copy.deepcopy(fake.db.docs)

            for _attempt in range(2):
                status = self.status(fake, operation.id)
                self.assertEqual(
                    status, {"status": "unknown", "operation_id": operation.id}
                )
                self.assertNotIn("version", status)
                self.assertNotIn("file_name", status)

            # No resume, no repair, no key reuse, no unlink and no silent outcome.
            self.assertEqual(fake.db.docs, snapshot)
            self.assertEqual(operation_rows(fake)[operation.id]["state"], "PREPARED")
            self.assertEqual(operation_rows(fake)[operation.id]["reservation_key"], key)
            self.assertEqual(os.listdir(publication.private_root(sites_path)), [])
            self.assertEqual(writer._issued(), {})
            self.assertEqual(writer._intents(), {})

            # A second reader call cannot allocate a new operation or advance state.
            self.assertEqual(len(operation_rows(fake)), 1)
            self.assertEqual(fake.db.commit_calls, 2)  # SUBMITTED + PREPARED only
            self.assertEqual(fake.db.rollback_calls, 0)


class InspectionGateTests(unittest.TestCase):
    """Read-only recovery inspection needs a current clinician and an allowlist."""

    def staged(self, frappe):
        outcome = photo_save.save(sub.annotation_payload())
        self.assertEqual(outcome["status"], "pending")
        return outcome["operation_id"]

    def test_inspection_requires_the_default_empty_operator_allowlist(self):
        module = inspection()
        with sub.runtime() as frappe:
            sub.enable(frappe)
            sub.grant_target(frappe)
            operation_id = self.staged(frappe)
            before = copy_docs(frappe)

            for allowlist in (
                None,
                [],
                (),
                ["someone@else.test"],
                "author@example.test",
                [AUTHOR, 5],
            ):
                with self.subTest(allowlist=allowlist):
                    if allowlist is None:
                        frappe.conf.pop(module.RECOVERY_OPERATOR_CONFIG_KEY, None)
                    else:
                        frappe.conf[module.RECOVERY_OPERATOR_CONFIG_KEY] = allowlist
                    with self.assertRaises(module.InspectionDenied):
                        module.inspect_operation(operation_id, actor=AUTHOR)

            self.assertEqual(copy_docs(frappe), before)

    def test_inspection_requires_the_publication_gate_and_current_site(self):
        module = inspection()
        with sub.runtime() as frappe:
            sub.enable(frappe)
            sub.grant_target(frappe)
            operation_id = self.staged(frappe)
            frappe.conf[module.RECOVERY_OPERATOR_CONFIG_KEY] = [AUTHOR]
            before = copy_docs(frappe)

            # The same exact-True server gate as the status entry: absent, False and
            # non-boolean truthy values all deny.
            for gate in ("absent", False, 1, "true"):
                with self.subTest(gate=gate):
                    if gate == "absent":
                        frappe.conf.pop(photo_save.GATE_CONFIG_KEY, None)
                    else:
                        frappe.conf[photo_save.GATE_CONFIG_KEY] = gate
                    with self.assertRaises(module.InspectionDenied):
                        module.inspect_operation(operation_id, actor=AUTHOR)

            # The durable operation site must still be the current site.
            sub.enable(frappe)
            frappe.local.site = "another-clinic.example"
            try:
                with self.assertRaises(module.InspectionDenied):
                    module.inspect_operation(operation_id, actor=AUTHOR)
            finally:
                frappe.local.site = SITE

            self.assertEqual(copy_docs(frappe), before)

    def test_quarantined_audit_does_not_claim_unverified_consistency(self):
        module = inspection()
        with sub.runtime() as frappe:
            sub.enable(frappe)
            sub.grant_target(frappe)
            row = stored_row(frappe, state="QUARANTINED")
            frappe.conf[module.RECOVERY_OPERATOR_CONFIG_KEY] = [AUTHOR]
            before = copy_docs(frappe)

            audit = module.inspect_operation(row["name"], actor=AUTHOR)

            self.assertEqual(set(audit), AUDIT_FIELDS)
            self.assertEqual(audit["state"], "QUARANTINED")
            self.assertEqual(audit["outcome"], "unknown")
            # No key/byte/outcome shape was verified for this state.
            self.assertIsNone(audit["consistent"])
            self.assertIsNone(audit["bytes_verified"])
            self.assertEqual(copy_docs(frappe), before)

    def test_inspection_denies_non_clinician_disabled_and_administrator(self):
        module = inspection()
        with sub.runtime(role="") as frappe:
            sub.enable(frappe)
            sub.grant_target(frappe)
            row = stored_row(frappe)
            frappe.conf[module.RECOVERY_OPERATOR_CONFIG_KEY] = [AUTHOR]
            with self.assertRaises(module.InspectionDenied):
                module.inspect_operation(row["name"], actor=AUTHOR)

        with sub.runtime(enabled=0) as frappe:
            sub.enable(frappe)
            sub.grant_target(frappe)
            frappe.conf[module.RECOVERY_OPERATOR_CONFIG_KEY] = [AUTHOR]
            with self.assertRaises(module.InspectionDenied):
                module.inspect_operation("0" * 32, actor=AUTHOR)

        with sub.runtime(user="Administrator") as frappe:
            sub.enable(frappe)
            frappe.conf[module.RECOVERY_OPERATOR_CONFIG_KEY] = ["Administrator"]
            before = copy_docs(frappe)
            with self.assertRaises(module.InspectionDenied):
                module.inspect_operation("0" * 32, actor="Administrator")
            self.assertEqual(copy_docs(frappe), before)

    def test_approved_operator_gets_operation_bound_audit_only(self):
        module = inspection()
        with sub.runtime() as frappe:
            sub.enable(frappe)
            sub.grant_target(frappe)
            operation_id = self.staged(frappe)
            frappe.conf[module.RECOVERY_OPERATOR_CONFIG_KEY] = [AUTHOR]
            row = operation_rows(frappe)[operation_id]
            before = copy_docs(frappe)

            audit = module.inspect_operation(operation_id, actor=AUTHOR)

            self.assertEqual(set(audit), AUDIT_FIELDS)
            self.assertEqual(audit["operation_id"], operation_id)
            self.assertEqual(audit["state"], journal.SUBMITTED_STATE)
            self.assertEqual(audit["kind"], "annotation_append")
            self.assertEqual(audit["payload_digest"], row["payload_digest"])
            self.assertIsNone(audit["expected_sha256"])
            self.assertEqual(audit["outcome"], "pending")
            self.assertTrue(audit["consistent"])
            self.assertIsNone(audit["bytes_verified"])

            serialized = repr(audit)
            for secret in (
                PATIENT,
                DRAFT,
                PHOTO,
                "annotation_json",
                "coordinate_space",
                "private/files",
                "/private/",
                CSRF,
                SID,
                AUTHOR,
            ):
                self.assertNotIn(secret, serialized)
            self.assertEqual(copy_docs(frappe), before)

    def test_operator_inspection_of_another_actors_operation_reveals_no_domain(self):
        module = inspection()
        with sub.runtime() as frappe:
            sub.enable(frappe)
            sub.grant_target(frappe)
            other_row = stored_row(
                frappe, actor=OTHER, request_key="operator-audit-key-1"
            )
            frappe.conf[module.RECOVERY_OPERATOR_CONFIG_KEY] = [AUTHOR]

            audit = module.inspect_operation(other_row["name"], actor=AUTHOR)

            self.assertEqual(set(audit), AUDIT_FIELDS)
            self.assertEqual(audit["operation_id"], other_row["name"])
            serialized = repr(audit)
            for secret in (OTHER, PATIENT, DRAFT, PHOTO, "/private/"):
                self.assertNotIn(secret, serialized)

            frappe.conf.pop(module.RECOVERY_OPERATOR_CONFIG_KEY, None)
            with self.assertRaises(module.InspectionDenied):
                module.inspect_operation(other_row["name"], actor=AUTHOR)

    def test_inspection_never_writes(self):
        module = inspection()
        with sub.runtime() as frappe:
            sub.enable(frappe)
            sub.grant_target(frappe)
            operation_id = self.staged(frappe)
            frappe.conf[module.RECOVERY_OPERATOR_CONFIG_KEY] = [AUTHOR]
            before = copy_docs(frappe)

            from unittest import mock

            with mock.patch.object(frappe.db, "commit") as commit:
                with mock.patch.object(frappe.db, "rollback") as rollback:
                    with mock.patch.object(
                        frappe.db, "set_value", wraps=frappe.db.set_value
                    ) as set_value:
                        module.inspect_operation(operation_id, actor=AUTHOR)
                        module.resolve_operation(operation_id, actor=AUTHOR)

            commit.assert_not_called()
            rollback.assert_not_called()
            set_value.assert_not_called()
            self.assertEqual(copy_docs(frappe), before)
            self.assertNotIn("File", frappe.db.docs)

    def test_inconsistent_rows_are_reported_without_a_saved_claim(self):
        module = inspection()
        with sub.runtime() as frappe:
            sub.enable(frappe)
            sub.grant_target(frappe)
            operation_id = self.staged(frappe)
            frappe.conf[module.RECOVERY_OPERATOR_CONFIG_KEY] = [AUTHOR]
            row = operation_rows(frappe)[operation_id]
            original = dict(row)
            row["payload_digest"] = "b" * 64
            before = copy_docs(frappe)

            try:
                audit = module.inspect_operation(operation_id, actor=AUTHOR)

                self.assertEqual(set(audit), AUDIT_FIELDS)
                self.assertFalse(audit["consistent"])
                self.assertEqual(audit["outcome"], "unknown")
                self.assertIsNone(audit["bytes_verified"])
                self.assertEqual(copy_docs(frappe), before)

                # The same inconsistency keeps the ordinary status opaque.
                with self.assertRaises(module.InspectionDenied):
                    module.resolve_operation(operation_id, actor=AUTHOR)
            finally:
                row.clear()
                row.update(original)

    def test_no_recovery_or_purge_entry_point_is_exposed(self):
        module = inspection()
        forbidden = ("purge", "recover", "repair", "reset", "delete", "execute_sql")
        for source in (module, photo_save):
            for name in dir(source):
                if name.startswith("_") or not callable(getattr(source, name)):
                    continue
                lowered = name.lower()
                for token in forbidden:
                    self.assertNotIn(token, lowered, f"{source.__name__}.{name}")


# ---------------------------------------------------------------------------
# Shared ownerless committed-object proof and ordinary current-access status
# ---------------------------------------------------------------------------
class _FileDocument(publication.ExactFile):
    """Fresh persisted File stand-in whose ``get_content`` runs the real mixin.

    ``ProtectedFileMixin.get_content`` is the shipped class, so the effective
    authorization chain runs before any content is produced. The stand-in mirrors the
    pinned Frappe v16.53.0 ``File.__init__`` default (``self.content = self.get(
    "content") or b""``): ``content`` starts as ``b""`` and the native truthiness
    predicate therefore means "read from disk", not "injected content".
    """

    def __init__(self, fake, row):
        super().__init__(
            file_url=row.get("file_url"),
            file_name=row.get("file_name"),
            content=b"",
            owner=row.get("owner") or AUTHOR,
            is_private=int(row.get("is_private") or 0),
            db=fake.db,
            name=row["name"],
        )
        self.file_size = row.get("file_size") or 0
        self.content_before_call = self.content
        self.loaded_content = "not-called"
        self.get_content_calls = []

    def get_content(self, encodings=None):
        self.content_before_call = self.content
        self.get_content_calls.append(encodings)
        content = super().get_content(encodings)
        self.loaded_content = content
        return content


def _vetoing_file(error_factory):
    """A File stand-in whose effective get_content chain refuses downstream."""

    class _Vetoed(_FileDocument):
        def get_content(self, encodings=None):
            self.content_before_call = self.content
            self.get_content_calls.append(encodings)
            raise error_factory()

    return _Vetoed


@contextlib.contextmanager
def file_documents(fake, factory=None):
    """Serve ``frappe.get_doc("File", name)`` from the harness rows."""
    original = fake.get_doc

    def get_doc(values, name=None):
        if values == "File" and isinstance(name, str) and name:
            row = fake.db.docs.get("File", {}).get(name)
            if row is None:
                raise fake.ValidationError("File not found")
            return (factory or _FileDocument)(fake, row)
        return original(values, name)

    fake.get_doc = get_doc
    try:
        yield fake
    finally:
        fake.get_doc = original


@contextlib.contextmanager
def saved_runtime(fake, *, factory=None):
    with photo_session(fake):
        with file_documents(fake, factory):
            yield fake


class _RecordingOwner:
    """Stand-in live owner recording poison/abort attempts; never issues authority."""

    def __init__(self):
        self.reasons = []
        self.aborted = False

    def require_bindings(self, **_kwargs):
        return self

    def poison(self, reason="owner_uncertainty"):
        self.reasons.append(reason)

    def publication_aborted(self):
        return self.aborted

    def abort_publication(self, reason):
        self.aborted = True
        self.reasons.append("abort:" + reason)


def allow_saved_reads(fake, *, patient_read=True, file_read=True, version_read=True):
    publication.allow_permissions(fake, patient_read=patient_read)
    if file_read:
        fake.permissions.add(("File", "read"))
    if version_read:
        fake.permissions.add((publication.VERSION_DOCTYPE, "read"))


def publish_annotation(
    test,
    root,
    *,
    expected_version=0,
    request_key="request-key-0001",
    allow=None,
):
    sites_path = publication.sites_volume(root)
    fake = test.enterContext(publication.fake_runtime(sites_path=sites_path))
    publication.seed_actor(fake)
    publication.seed_patient(fake)
    publication.seed_annotation_target(fake, version=expected_version)
    allow_saved_reads(fake, **(allow or {}))
    operation = publication.stage_committed(
        fake,
        publication.annotation_request(
            expected_version=expected_version, request_key=request_key
        ),
    )
    outcome = publication.publish(fake, operation)
    return fake, sites_path, operation, outcome


REQUEST_KEY = "request-key-0001"


def annotation_payload(**overrides):
    """The raw public payload that ``publication.annotation_request()`` validates."""
    payload = {
        "kind": "annotation_append",
        "request_key": REQUEST_KEY,
        "draft": DRAFT,
        "expected_version": 0,
        "original": PHOTO,
        "annotation": {
            **publication.ENVELOPE,
            "shapes": [dict(publication.SHAPE)],
        },
    }
    payload.update(overrides)
    return payload


def published_evidence(fake, operation):
    return journal.load_published(operation.id)


def object_path(sites_path, key):
    return publication.target_path(sites_path, key)


def read_bytes(path):
    with open(path, "rb") as handle:
        return handle.read()


class CommittedByteProofTests(unittest.TestCase):
    """The shared verifier is side-effect free; the owner-bound wrapper is unchanged."""

    def test_ownerless_proof_returns_the_digest_and_changes_nothing(self):
        with tempfile.TemporaryDirectory() as root:
            fake, sites_path, operation, _ = publish_annotation(self, root)
            published = published_evidence(fake, operation)
            path = object_path(sites_path, published.reservation.key)
            bytes_before = read_bytes(path)
            snapshot = copy.deepcopy(fake.db.docs)
            commits, rollbacks = fake.db.commit_calls, fake.db.rollback_calls
            issued, intents = dict(writer._issued()), dict(writer._intents())

            digest = writer.verify_committed_object(published)

            self.assertEqual(digest, published.reservation.expected_sha256)
            self.assertEqual(fake.db.docs, snapshot)
            self.assertEqual(read_bytes(path), bytes_before)
            self.assertEqual(
                (fake.db.commit_calls, fake.db.rollback_calls), (commits, rollbacks)
            )
            self.assertFalse(
                getattr(fake.local, frappe_context.PUBLICATION_ABORTED_ATTR, False)
            )
            self.assertEqual(writer._issued(), issued)
            self.assertEqual(writer._intents(), intents)
            with self.assertRaises(exclusion.ExclusionError):
                exclusion.require_locked_owner(operation.id)

    def test_ownerless_failures_are_fixed_message_and_poison_nothing(self):
        def append_bytes(path, published):
            with open(path, "ab") as handle:
                handle.write(b" ")

        def truncate(path, published):
            with open(path, "r+b") as handle:
                handle.truncate(4)

        def chmod(path, published):
            os.chmod(path, 0o640)

        def same_bytes_new_inode(path, published):
            data = read_bytes(path)
            replacement = path + ".replacement"
            with open(replacement, "wb") as handle:
                handle.write(data)
            os.replace(replacement, path)

        def symlink(path, published):
            os.rename(path, path + ".orig")
            os.symlink(path + ".orig", path)

        def hard_link(path, published):
            os.link(path, path + ".link")

        def remove(path, published):
            os.unlink(path)

        cases = (
            ("changed_bytes", append_bytes),
            ("truncated_bytes", truncate),
            ("mode_mismatch", chmod),
            ("same_bytes_new_inode", same_bytes_new_inode),
            ("symlink", symlink),
            ("hard_link", hard_link),
            ("missing_object", remove),
        )
        allowed = {
            writer.SAFE_WRITER_UNCERTAIN,
            writer.SAFE_WRITER_DENIED,
            writer.SAFE_WRITER_RECEIPT,
        }
        for label, tamper in cases:
            with self.subTest(case=label), tempfile.TemporaryDirectory() as root:
                fake, sites_path, operation, _ = publish_annotation(self, root)
                published = published_evidence(fake, operation)
                path = object_path(sites_path, published.reservation.key)
                snapshot = copy.deepcopy(fake.db.docs)
                commits, rollbacks = fake.db.commit_calls, fake.db.rollback_calls
                tamper(path, published)
                owner = _RecordingOwner()

                with mock.patch.object(
                    exclusion, "require_locked_owner", lambda operation_id: owner
                ):
                    with self.assertRaises(writer.FirstObjectError) as caught:
                        writer.verify_committed_object(published)

                self.assertIn(str(caught.exception), allowed)
                self.assertEqual(owner.reasons, [])
                self.assertEqual(fake.db.docs, snapshot)
                self.assertEqual(
                    (fake.db.commit_calls, fake.db.rollback_calls),
                    (commits, rollbacks),
                )
                self.assertFalse(
                    getattr(
                        fake.local, frappe_context.PUBLICATION_ABORTED_ATTR, False
                    )
                )

    def test_fstat_oserror_is_a_fixed_refusal_without_mutation(self):
        with tempfile.TemporaryDirectory() as root:
            fake, _sites_path, operation, _ = publish_annotation(self, root)
            published = published_evidence(fake, operation)
            snapshot = copy.deepcopy(fake.db.docs)
            commits, rollbacks = fake.db.commit_calls, fake.db.rollback_calls
            owner = _RecordingOwner()

            with mock.patch.object(
                exclusion, "require_locked_owner", lambda operation_id: owner
            ):
                with mock.patch.object(
                    writer.os, "fstat", side_effect=OSError("fstat failed")
                ):
                    with self.assertRaises(writer.FirstObjectError) as caught:
                        writer.verify_committed_object(published)

            self.assertEqual(str(caught.exception), writer.SAFE_WRITER_UNCERTAIN)
            self.assertEqual(owner.reasons, [])
            self.assertEqual(fake.db.docs, snapshot)
            self.assertEqual(
                (fake.db.commit_calls, fake.db.rollback_calls), (commits, rollbacks)
            )
            self.assertFalse(
                getattr(fake.local, frappe_context.PUBLICATION_ABORTED_ATTR, False)
            )

    def test_owner_bound_wrapper_preserves_classification_poisoning_and_latch(self):
        with tempfile.TemporaryDirectory() as root:
            fake, sites_path, operation, _ = publish_annotation(self, root)
            published = published_evidence(fake, operation)
            path = object_path(sites_path, published.reservation.key)
            with open(path, "ab") as handle:
                handle.write(b" ")

            owner = _RecordingOwner()
            with mock.patch.object(
                exclusion, "require_locked_owner", lambda operation_id: owner
            ):
                with self.assertRaises(writer.FirstObjectUncertain) as caught:
                    writer.verify_published_object(
                        published.reservation,
                        published.file_name,
                        creation_identity=published.creation_identity,
                    )

            self.assertEqual(str(caught.exception), writer.SAFE_WRITER_UNCERTAIN)
            # The Task 8 attempt path still poisons the exact live owner.
            self.assertEqual(owner.reasons, ["published_object_size"])

        with tempfile.TemporaryDirectory() as root:
            fake, sites_path, operation, _ = publish_annotation(self, root)
            published = published_evidence(fake, operation)

            with self.assertRaises(exclusion.ExclusionError):
                writer.verify_published_object(
                    published.reservation,
                    published.file_name,
                    creation_identity=published.creation_identity,
                )
            self.assertTrue(
                getattr(fake.local, frappe_context.PUBLICATION_ABORTED_ATTR, False)
            )

    def test_missing_or_malformed_identity_and_foreign_evidence_are_refused(self):
        with tempfile.TemporaryDirectory() as root:
            fake, sites_path, operation, _ = publish_annotation(self, root)
            published = published_evidence(fake, operation)
            owner = _RecordingOwner()

            with mock.patch.object(
                exclusion, "require_locked_owner", lambda operation_id: owner
            ):
                with self.assertRaises(writer.ReceiptInvalid):
                    writer.verify_published_object(
                        published.reservation,
                        published.file_name,
                        creation_identity="not-an-identity",
                    )
            self.assertEqual(owner.reasons, ["published_identity_malformed"])

            # The ownerless entry accepts only server-loaded durable outcome evidence.
            for foreign in (None, published.reservation, "File", {"key": "x"}):
                with self.subTest(foreign=type(foreign).__name__):
                    with self.assertRaises(writer.FirstObjectError) as caught:
                        writer.verify_committed_object(foreign)
                    self.assertEqual(
                        str(caught.exception), writer.SAFE_WRITER_DENIED
                    )
            self.assertEqual(owner.reasons, ["published_identity_malformed"])


class SavedOutcomeTests(unittest.TestCase):
    """Ordinary status returns the known immutable version under current access."""

    def status(self, fake, operation_id, *, factory=None):
        with saved_runtime(fake, factory=factory):
            return photo_save.status(operation_id)

    def test_published_status_is_read_only_and_never_creates_authority(self):
        with tempfile.TemporaryDirectory() as root:
            fake, sites_path, operation, _ = publish_annotation(self, root)
            published = published_evidence(fake, operation)
            path = object_path(sites_path, published.reservation.key)
            bytes_before = read_bytes(path)
            snapshot = copy.deepcopy(fake.db.docs)
            commits, rollbacks = fake.db.commit_calls, fake.db.rollback_calls
            issued, intents = dict(writer._issued()), dict(writer._intents())

            status = self.status(fake, operation.id)

            self.assertEqual(status["status"], "saved")
            self.assertEqual(status["version"], 1)
            self.assertEqual(fake.db.docs, snapshot)
            self.assertEqual(read_bytes(path), bytes_before)
            self.assertEqual(
                (fake.db.commit_calls, fake.db.rollback_calls), (commits, rollbacks)
            )
            self.assertFalse(
                getattr(fake.local, frappe_context.PUBLICATION_ABORTED_ATTR, False)
            )
            self.assertEqual(writer._issued(), issued)
            self.assertEqual(writer._intents(), intents)
            with self.assertRaises(exclusion.ExclusionError):
                exclusion.require_locked_owner(operation.id)

    def test_annotation_status_requires_current_patient_read(self):
        with tempfile.TemporaryDirectory() as root:
            fake, _sites_path, operation, _ = publish_annotation(
                self, root, allow={"patient_read": False}
            )
            with self.assertRaises(fake.PermissionError) as caught:
                self.status(fake, operation.id)
            self.assertEqual(str(caught.exception), photo_save.SAFE_STATUS_DENIED)

    def test_native_file_and_protected_reference_denials_withhold_saved(self):
        for allow in (
            {"file_read": False},
            {"version_read": False},
        ):
            with self.subTest(allow=allow), tempfile.TemporaryDirectory() as root:
                fake, _sites_path, operation, _ = publish_annotation(
                    self, root, allow=allow
                )
                status = self.status(fake, operation.id)
                self.assertEqual(status["status"], "unknown")
                self.assertNotIn("version", status)
                self.assertNotIn("file_name", status)

    def test_downstream_get_content_veto_or_exception_withholds_saved(self):
        for label in ("runtime_error", "permission_error"):
            with self.subTest(case=label), tempfile.TemporaryDirectory() as root:
                fake, _sites_path, operation, _ = publish_annotation(self, root)
                error = (
                    RuntimeError("downstream veto")
                    if label == "runtime_error"
                    else fake.PermissionError("native veto")
                )
                observed = {}

                def factory(fake_module, row, _error=error):
                    document = _vetoing_file(lambda: _error)(fake_module, row)
                    observed["document"] = document
                    return document

                status = self.status(fake, operation.id, factory=factory)

                self.assertEqual(status["status"], "unknown")
                self.assertIn(
                    "document", observed, "the effective get_content chain did not run"
                )
                self.assertEqual(observed["document"].content_before_call, b"")
                self.assertEqual(observed["document"].get_content_calls, [None])

    def test_changed_bytes_are_not_saved_even_when_the_rows_still_agree(self):
        with tempfile.TemporaryDirectory() as root:
            fake, sites_path, operation, _ = publish_annotation(self, root)
            published = published_evidence(fake, operation)
            path = object_path(sites_path, published.reservation.key)
            with open(path, "ab") as handle:
                handle.write(b" ")

            status = self.status(fake, operation.id)
            self.assertEqual(status["status"], "unknown")
            self.assertNotIn("version", status)

    def test_current_authority_change_during_the_proof_withholds_saved(self):
        with tempfile.TemporaryDirectory() as root:
            fake, _sites_path, operation, _ = publish_annotation(self, root)

            def factory(fake_module, row):
                document = _FileDocument(fake_module, row)
                original = document.get_content

                def get_content(encodings=None):
                    # Revocation injected while the byte proof is running: the
                    # bracketing authority check must observe it.
                    fake_module.permissions.discard(("Patient", "read"))
                    return original(encodings)

                document.get_content = get_content
                return document

            status = self.status(fake, operation.id, factory=factory)
            self.assertEqual(status["status"], "unknown")
            self.assertNotIn("version", status)

    def test_corrupt_domain_linkage_withholds_saved(self):
        with tempfile.TemporaryDirectory() as root:
            fake, _sites_path, operation, _ = publish_annotation(self, root)
            version = fake.db.docs[publication.VERSION_DOCTYPE][f"{DRAFT}-v1"]
            original = dict(version)
            version["annotation_file"] = "FILE-OTHER"
            try:
                status = self.status(fake, operation.id)
            finally:
                version.clear()
                version.update(original)
            self.assertEqual(status["status"], "unknown")
            self.assertNotIn("version", status)

    def test_mid_proof_row_change_withholds_saved(self):
        with tempfile.TemporaryDirectory() as root:
            fake, _sites_path, operation, _ = publish_annotation(self, root)

            def factory(fake_module, row):
                document = _FileDocument(fake_module, row)
                original = document.get_content

                def get_content(encodings=None):
                    fake_module.db.docs["File"][row["name"]]["file_size"] = 1
                    return original(encodings)

                document.get_content = get_content
                return document

            status = self.status(fake, operation.id, factory=factory)
            self.assertEqual(status["status"], "unknown")
            self.assertNotIn("version", status)

    def test_mid_proof_durable_outcome_substitution_withholds_saved(self):
        with tempfile.TemporaryDirectory() as root:
            fake, _sites_path, operation, _ = publish_annotation(self, root)
            row = publication.operation_rows(fake)[operation.id]
            original = dict(row)

            def factory(fake_module, _row):
                document = _FileDocument(fake_module, _row)
                get_content = document.get_content

                def substituted(encodings=None):
                    # Same operation, same payload digest, changed durable outcome
                    # column while the byte proof runs: the outcome must be re-bound
                    # after the proof, not trusted from the initial read.
                    row["published_file"] = "FILE-OTHER"
                    return get_content(encodings)

                document.get_content = substituted
                return document

            try:
                status = self.status(fake, operation.id, factory=factory)
            finally:
                row.clear()
                row.update(original)
            self.assertEqual(status["status"], "unknown")
            self.assertNotIn("version", status)
            self.assertNotIn("file_name", status)

    def test_file_or_protected_reference_revocation_during_the_proof_withholds_saved(
        self,
    ):
        for revoked in (("File", "read"), (publication.VERSION_DOCTYPE, "read")):
            with self.subTest(revoked=revoked), tempfile.TemporaryDirectory() as root:
                fake, _sites_path, operation, _ = publish_annotation(self, root)
                real_proof = writer.verify_committed_object

                def revoked_proof(published, _revoked=revoked, _fake=fake):
                    digest = real_proof(published)
                    # Native File read / protected-reference revocation committed
                    # while the byte proof runs must be re-checked before `saved`.
                    _fake.permissions.discard(_revoked)
                    return digest

                with mock.patch.object(
                    writer, "verify_committed_object", revoked_proof
                ):
                    status = self.status(fake, operation.id)

                self.assertEqual(status["status"], "unknown")
                self.assertNotIn("version", status)

    def test_no_cached_content_is_injected_into_the_file_document(self):
        with tempfile.TemporaryDirectory() as root:
            fake, _sites_path, operation, _ = publish_annotation(self, root)
            observed = {}

            def factory(fake_module, row):
                document = _FileDocument(fake_module, row)
                observed["document"] = document
                return document

            status = self.status(fake, operation.id, factory=factory)

            self.assertEqual(status["status"], "saved")
            document = observed["document"]
            # The pinned native default is b""; it is not injected content and the
            # effective chain really runs with that exact pre-call value.
            self.assertEqual(document.content_before_call, b"")
            self.assertEqual(document.loaded_content, b"")
            self.assertEqual(document.get_content_calls, [None])

    def test_truthy_injected_content_is_refused_before_the_chain(self):
        with tempfile.TemporaryDirectory() as root:
            fake, _sites_path, operation, _ = publish_annotation(self, root)
            observed = {}

            def factory(fake_module, row):
                document = _FileDocument(fake_module, row)
                # A truthy pre-supplied value would short-circuit the effective chain;
                # the native truthiness predicate must refuse it.
                document.content = b"injected"
                observed["document"] = document
                return document

            status = self.status(fake, operation.id, factory=factory)

            self.assertEqual(status["status"], "unknown")
            self.assertNotIn("version", status)
            self.assertNotIn("file_name", status)
            self.assertEqual(observed["document"].get_content_calls, [])


class HistoricalReplayTests(unittest.TestCase):
    """A published N stays N after a legitimate N+1; the attempt mode is unchanged."""

    def status(self, fake, operation_id):
        with saved_runtime(fake):
            return photo_save.status(operation_id)

    def two_versions(self, root):
        fake, sites_path, first, first_outcome = publish_annotation(self, root)
        self.assertEqual(first_outcome.status, "saved")
        second = publication.stage_committed(
            fake,
            publication.annotation_request(
                expected_version=1, request_key="request-key-0002"
            ),
        )
        second_outcome = publication.publish(fake, second)
        self.assertEqual(second_outcome.status, "saved")
        self.assertEqual(second_outcome.version, 2)
        return fake, sites_path, first, second

    def test_published_n_returns_n_after_a_legitimate_n_plus_one(self):
        with tempfile.TemporaryDirectory() as root:
            fake, _sites_path, first, second = self.two_versions(root)
            self.assertEqual(
                publication.operation_rows(fake)[first.id]["published_version"], 1
            )

            first_status = self.status(fake, first.id)
            second_status = self.status(fake, second.id)

            self.assertEqual(first_status["status"], "saved")
            self.assertEqual(first_status["version"], 1)
            self.assertEqual(second_status["status"], "saved")
            self.assertEqual(second_status["version"], 2)

    def test_corrupt_or_mismatched_current_head_yields_unknown(self):
        corruptions = (
            {"current_version_number": 5},
            {"current_version_number": 1, "current_version": "DRAFT-1-v2"},
            {"current_version_number": 1, "current_version": ""},
        )
        for corruption in corruptions:
            with self.subTest(corruption=corruption), tempfile.TemporaryDirectory() as root:
                fake, _sites_path, first, _second = self.two_versions(root)
                draft = fake.db.docs[publication.DRAFT_DOCTYPE][DRAFT]
                original = dict(draft)
                draft.update(corruption)
                try:
                    status = self.status(fake, first.id)
                finally:
                    draft.clear()
                    draft.update(original)
                self.assertEqual(status["status"], "unknown")
                self.assertNotIn("version", status)


class StageSubmissionReplayTests(unittest.TestCase):
    """Same-key replay of published work resolves after current authorization."""

    def test_same_tuple_replay_returns_the_published_version_without_a_second_write(self):
        with tempfile.TemporaryDirectory() as root:
            fake, _sites_path, operation, _ = publish_annotation(self, root)
            snapshot = copy.deepcopy(fake.db.docs)
            commits, rollbacks = fake.db.commit_calls, fake.db.rollback_calls

            with saved_runtime(fake):
                replay = photo_save.save(annotation_payload())

            self.assertEqual(replay["status"], "saved")
            self.assertEqual(replay["operation_id"], operation.id)
            self.assertEqual(replay["version"], 1)
            self.assertEqual(fake.db.docs, snapshot)
            self.assertEqual(len(fake.db.docs["File"]), 1)
            self.assertEqual(len(fake.db.docs[publication.VERSION_DOCTYPE]), 1)
            self.assertEqual(
                (fake.db.commit_calls, fake.db.rollback_calls), (commits, rollbacks)
            )

    def test_replay_still_denies_revocation_and_rejects_a_changed_payload(self):
        with tempfile.TemporaryDirectory() as root:
            fake, _sites_path, operation, _ = publish_annotation(self, root)
            snapshot = copy.deepcopy(fake.db.docs)

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
            with saved_runtime(fake):
                conflict = photo_save.save(changed)
            self.assertEqual(conflict["status"], "conflict")
            self.assertEqual(conflict["operation_id"], operation.id)

            fake.db.docs[publication.DRAFT_DOCTYPE][DRAFT]["author"] = OTHER
            with saved_runtime(fake):
                with self.assertRaises(fake.PermissionError) as caught:
                    photo_save.save(annotation_payload())
            self.assertEqual(str(caught.exception), photo_save.SAFE_DENIAL)

            fake.db.docs[publication.DRAFT_DOCTYPE][DRAFT]["author"] = AUTHOR
            self.assertEqual(
                {
                    name: dict(row)
                    for name, row in fake.db.docs["File"].items()
                },
                {
                    name: dict(row)
                    for name, row in snapshot["File"].items()
                },
            )
            self.assertEqual(len(fake.db.docs[publication.VERSION_DOCTYPE]), 1)


class OperatorAuditHashTests(unittest.TestCase):
    """Cross-actor audit hashes internally without patient or File read rights."""

    def audit(self, fake, operation_id, *, actor=AUTHOR):
        module = inspection()
        fake.conf[module.RECOVERY_OPERATOR_CONFIG_KEY] = [actor]
        return module.inspect_operation(operation_id, actor=actor)

    def test_operator_audit_hashes_without_patient_or_file_rights(self):
        with tempfile.TemporaryDirectory() as root:
            fake, _sites_path, operation, _ = publish_annotation(
                self, root, allow={"patient_read": False, "file_read": False}
            )

            audit = self.audit(fake, operation.id)

            self.assertEqual(set(audit), AUDIT_FIELDS)
            self.assertEqual(audit["operation_id"], operation.id)
            self.assertEqual(audit["state"], journal.PUBLISHED_STATE)
            # A neutral audit label: this is not the ordinary clinical `saved` claim.
            self.assertEqual(audit["outcome"], "published_verified")
            self.assertTrue(audit["consistent"])
            self.assertTrue(audit["bytes_verified"])
            self.assertRegex(audit["expected_sha256"], r"[0-9a-f]{64}")
            serialized = repr(audit)
            for secret in (
                PATIENT,
                DRAFT,
                PHOTO,
                "/private/",
                "private/files",
                AUTHOR,
                OTHER,
            ):
                self.assertNotIn(secret, serialized)
            for identifier in ("file_name", "domain_name", "reservation_key", "version"):
                self.assertNotIn(identifier, audit)

            # The ordinary replay of the same operation remains denied for the same
            # actor: the audit granted no patient or File read right.
            with saved_runtime(fake):
                with self.assertRaises(fake.PermissionError):
                    photo_save.status(operation.id)

    def test_cross_actor_published_audit_hashes_without_patient_or_file_rights(self):
        with tempfile.TemporaryDirectory() as root:
            fake, _sites_path, operation, _ = publish_annotation(self, root)
            publication.seed_actor(fake, user=OTHER)
            # OTHER holds no Patient/File/version read rights at all.
            for permission in (
                ("Patient", "read"),
                ("File", "read"),
                (publication.VERSION_DOCTYPE, "read"),
            ):
                fake.permissions.discard(permission)

            with photo_session(fake, user=OTHER):
                audit = self.audit(fake, operation.id, actor=OTHER)

                self.assertEqual(set(audit), AUDIT_FIELDS)
                self.assertEqual(audit["operation_id"], operation.id)
                self.assertEqual(audit["state"], journal.PUBLISHED_STATE)
                self.assertEqual(audit["outcome"], "published_verified")
                self.assertTrue(audit["bytes_verified"])
                self.assertRegex(audit["expected_sha256"], r"[0-9a-f]{64}")
                serialized = repr(audit)
                for secret in (
                    PATIENT,
                    DRAFT,
                    PHOTO,
                    "/private/",
                    "private/files",
                    AUTHOR,
                    OTHER,
                ):
                    self.assertNotIn(secret, serialized)
                for identifier in (
                    "file_name",
                    "domain_name",
                    "reservation_key",
                    "version",
                ):
                    self.assertNotIn(identifier, audit)

                # Ordinary status/replay stays denied for the other actor.
                with self.assertRaises(fake.PermissionError):
                    photo_save.status(operation.id)

    def test_audit_withholds_verification_when_the_outcome_changes_during_proof(self):
        with tempfile.TemporaryDirectory() as root:
            fake, _sites_path, operation, _ = publish_annotation(self, root)
            row = publication.operation_rows(fake)[operation.id]
            original = dict(row)
            real_proof = writer.verify_committed_object

            def substituting_proof(published):
                digest = real_proof(published)
                # Same payload digest, changed durable outcome column during the
                # audit's own byte proof.
                row["published_file"] = "FILE-OTHER"
                return digest

            try:
                with mock.patch.object(
                    writer, "verify_committed_object", substituting_proof
                ):
                    audit = self.audit(fake, operation.id)
            finally:
                row.clear()
                row.update(original)

            self.assertEqual(audit["outcome"], "unknown")
            self.assertFalse(audit["consistent"])
            self.assertIsNone(audit["bytes_verified"])
            self.assertNotIn("published_verified", repr(audit))

    def test_operator_audit_reports_unverified_bytes_without_a_saved_claim(self):
        with tempfile.TemporaryDirectory() as root:
            fake, sites_path, operation, _ = publish_annotation(self, root)
            published = published_evidence(fake, operation)
            path = object_path(sites_path, published.reservation.key)
            with open(path, "ab") as handle:
                handle.write(b" ")

            audit = self.audit(fake, operation.id)

            self.assertEqual(audit["outcome"], "unknown")
            self.assertFalse(audit["bytes_verified"])
            self.assertTrue(audit["consistent"])
            self.assertNotIn("saved", repr(audit))




if __name__ == "__main__":
    unittest.main()
