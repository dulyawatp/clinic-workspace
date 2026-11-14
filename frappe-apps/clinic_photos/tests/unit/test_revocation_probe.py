"""Behavioral tests for the private revocation-probe protocol.

Task 1 pins the pure input/request/response/baseline boundary. Task 2 adds the
fresh native probe bodies through narrow framework doubles only: no Frappe import,
no probe subprocess, no database and no native evidence. The doubles model the
boundary this module calls; they are local controls, never installed-behaviour proof.
"""

import base64
import contextlib
import hashlib
import io
import json
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

from clinic_photos.access import frappe_context
from clinic_photos.publication import (
    contracts,
    inspection,
    journal,
    staging,
    submission,
    validation,
    writer,
)
from clinic_photos.tests import revocation_probe as probe

# Known-good 1x1 PNG fixture used by the existing publication contract tests.
PNG_1X1_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGM4wcUFAAJ6AN1n"
    "J0J9AAAAAElFTkSuQmCC"
)
PNG_1X1_BYTES = base64.b64decode(PNG_1X1_BASE64)

SITE = "clinic-photo-accept.local"
ACTOR = "synthetic-clinician@example.test"
VERIFIER = "synthetic-verifier@example.test"
RUN_NONCE = "a" * 32
INVOCATION_NONCE = "b" * 32
OPERATION_ID = "c" * 32
SOURCE_DIGEST = "d" * 64
BASELINE_DIGEST = "4" * 64
EVIDENCE_DIGEST = "5" * 64

REQUEST_KEY = "request-key-0000000000000001"
SYNTHETIC_PATIENT = "SYNTHETIC-PATIENT-0001"

# Durable rows carry staged image metadata only, so the fixture stages the accepted
# bytes once into a real temporary site tree for this module.
STAGE_ROOT = None
STAGED_IMAGE = None


def _stage_fixture_image():
    """Stage the fixture bytes in a throwaway site tree; returns its metadata."""
    global STAGE_ROOT, STAGED_IMAGE
    STAGE_ROOT = os.path.realpath(tempfile.mkdtemp(prefix="clinic-probe-staging-"))
    os.makedirs(os.path.join(STAGE_ROOT, SITE, "private"), exist_ok=True)

    module = types.ModuleType("frappe")
    module.local = types.SimpleNamespace(site=SITE)
    module.get_site_path = lambda *parts: os.path.join(STAGE_ROOT, SITE, *parts)
    previous = sys.modules.get("frappe")
    sys.modules["frappe"] = module
    try:
        STAGED_IMAGE = staging.stage_image(PNG_1X1_BYTES)
    finally:
        if previous is None:
            sys.modules.pop("frappe", None)
        else:
            sys.modules["frappe"] = previous


def setUpModule():
    _stage_fixture_image()


def tearDownModule():
    import shutil

    if STAGE_ROOT:
        shutil.rmtree(STAGE_ROOT, ignore_errors=True)


def staged_row_fields():
    """Metadata-only staged reference fields for a durable fixture row."""
    return {
        "staged_key": STAGED_IMAGE.key,
        "staged_sha256": STAGED_IMAGE.sha256,
        "staged_size": STAGED_IMAGE.size,
        "staged_format": STAGED_IMAGE.format,
    }

ANNOTATION = {
    "schema_version": 1,
    "coordinate_space": "normalized",
    "shapes": [
        {
            "id": "shape-1",
            "type": "rectangle",
            "color": "#1a2B3c",
            "x": 0.25,
            "y": 0.5,
            "width": 0.25,
            "height": 0.25,
            "stroke_width": 0.01,
        }
    ],
}


def wire_request():
    return {
        "kind": "synthetic_original_create",
        "request_key": REQUEST_KEY,
        "synthetic_patient": SYNTHETIC_PATIENT,
        "image_base64": PNG_1X1_BASE64,
    }


def annotation_wire_request():
    return {
        "kind": "annotation_append",
        "request_key": REQUEST_KEY,
        "draft": "DRAFT-0001",
        "expected_version": 0,
        "original": "ORIGINAL-0001",
        "annotation": ANNOTATION,
    }


def baseline():
    return {
        "enabled": True,
        "roles": [
            {"role": "Physician", "parenttype": "User", "parentfield": "roles"},
            {"role": "Nursing User", "parenttype": "User", "parentfield": "roles"},
        ],
        "actor_digest": "1" * 64,
        "operation_digest": "2" * 64,
        "evidence_digest": "3" * 64,
        "counts": {"operations": 1, "files": 1, "photos": 1, "versions": 1},
    }


def input_document(**overrides):
    document = {
        "schema": 1,
        "run_nonce": RUN_NONCE,
        "invocation_nonce": INVOCATION_NONCE,
        "site": SITE,
        "source_digest": SOURCE_DIGEST,
        "operation_id": OPERATION_ID,
        "actor": ACTOR,
        "verifier": VERIFIER,
        "arm": "disabled",
        "mode": "replay_denial",
        "baseline": baseline(),
        "request": wire_request(),
    }
    document.update(overrides)
    return document


def raw(document):
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")


def expected_response():
    return {
        "run_nonce": RUN_NONCE,
        "invocation_nonce": INVOCATION_NONCE,
        "site_digest": probe.site_digest(SITE),
        "source_digest": SOURCE_DIGEST,
        "operation_id": OPERATION_ID,
        "arm": "disabled",
        "mode": "verify",
        "baseline_digest": BASELINE_DIGEST,
        "evidence_digest": EVIDENCE_DIGEST,
    }


def response_document(**overrides):
    document = {
        "schema": 1,
        "run_nonce": RUN_NONCE,
        "invocation_nonce": INVOCATION_NONCE,
        "site_digest": probe.site_digest(SITE),
        "source_digest": SOURCE_DIGEST,
        "operation_id": OPERATION_ID,
        "arm": "disabled",
        "mode": "verify",
        "result": "observed",
        "enabled": False,
        "clinician": True,
        "baseline_digest": BASELINE_DIGEST,
        "evidence_digest": EVIDENCE_DIGEST,
        "counts": {"operations": 1, "files": 1, "photos": 1, "versions": 1},
        "clean": True,
    }
    document.update(overrides)
    return document


class RequestRoundTripTest(unittest.TestCase):
    def test_exact_request_roundtrip(self):
        original = contracts.validate_request(wire_request())
        encoded = probe.encode_request(original)
        self.assertEqual(encoded, wire_request())

        rebuilt = contracts.validate_request(encoded)
        self.assertEqual(rebuilt.image_bytes, PNG_1X1_BYTES)
        self.assertEqual(rebuilt.kind, original.kind)
        self.assertEqual(rebuilt.request_key, original.request_key)
        self.assertEqual(
            journal.payload_digest(rebuilt), journal.payload_digest(original)
        )

        decoded = probe.decode_input(raw(input_document()))
        self.assertEqual(decoded["site"], SITE)
        self.assertEqual(decoded["actor"], ACTOR)
        self.assertEqual(decoded["verifier"], VERIFIER)
        self.assertEqual(decoded["operation_id"], OPERATION_ID)
        self.assertEqual(decoded["source_digest"], SOURCE_DIGEST)
        self.assertEqual(decoded["run_nonce"], RUN_NONCE)
        self.assertEqual(decoded["invocation_nonce"], INVOCATION_NONCE)
        self.assertEqual(decoded["arm"], "disabled")
        self.assertEqual(decoded["mode"], "replay_denial")
        self.assertIsInstance(decoded["request"], contracts.SaveRequest)
        self.assertEqual(decoded["request"].image_bytes, PNG_1X1_BYTES)
        self.assertEqual(
            journal.payload_digest(decoded["request"]),
            journal.payload_digest(original),
        )
        self.assertEqual(
            journal.identity_digest(
                decoded["site"],
                decoded["actor"],
                decoded["request"].kind,
                decoded["request"].request_key,
            ),
            journal.identity_digest(SITE, ACTOR, original.kind, original.request_key),
        )

        annotation = contracts.validate_request(annotation_wire_request())
        encoded_annotation = probe.encode_request(annotation)
        self.assertEqual(
            encoded_annotation["annotation"], json.loads(annotation.annotation)
        )
        rebuilt_annotation = contracts.validate_request(encoded_annotation)
        self.assertEqual(
            rebuilt_annotation.annotation, annotation.annotation
        )
        self.assertEqual(
            journal.payload_digest(rebuilt_annotation),
            journal.payload_digest(annotation),
        )


class MismatchRefusalTest(unittest.TestCase):
    def test_payload_or_identity_mismatch_refuses(self):
        with self.assertRaises(probe.RevocationProtocolError):
            probe.decode_input(
                raw(input_document(request=annotation_wire_request()))
            )

        for fieldname, value in (
            ("operation_id", "e" * 32),
            ("site_digest", "f" * 64),
            ("source_digest", "9" * 64),
            ("evidence_digest", "6" * 64),
            ("baseline_digest", "7" * 64),
        ):
            with self.subTest(fieldname=fieldname):
                with self.assertRaises(probe.RevocationProtocolError):
                    probe.validate_response(
                        response_document(**{fieldname: value}),
                        expected_response(),
                    )


class InputRefusalTest(unittest.TestCase):
    def test_duplicate_unknown_deep_oversize_input_refuses(self):
        valid = raw(input_document())
        self.assertEqual(valid.count(b'"mode":"replay_denial"'), 1)
        duplicate_root = valid.replace(
            b'"mode":"replay_denial"',
            b'"mode":"replay_denial","mode":"replay_denial"',
            1,
        )
        with self.assertRaises(probe.RevocationProtocolError):
            probe.decode_input(duplicate_root)

        self.assertEqual(valid.count(b'"enabled":true'), 1)
        duplicate_nested = valid.replace(
            b'"enabled":true', b'"enabled":true,"enabled":true', 1
        )
        with self.assertRaises(probe.RevocationProtocolError):
            probe.decode_input(duplicate_nested)

        unknown = input_document(extra="value")
        with self.assertRaises(probe.RevocationProtocolError):
            probe.decode_input(raw(unknown))

        oversize = valid + b" " * (probe.MAX_INPUT_BYTES + 1 - len(valid))
        with self.assertRaises(probe.RevocationProtocolError):
            probe.decode_input(oversize)

        with self.assertRaises(probe.RevocationProtocolError):
            probe.decode_input(valid + b"{}")

        with self.assertRaises(probe.RevocationProtocolError):
            probe.decode_input(b"{")

        with self.assertRaises(probe.RevocationProtocolError):
            probe.decode_input("{}")

        with self.assertRaises(probe.RevocationProtocolError) as caught:
            probe.decode_input(duplicate_root)
        self.assertEqual(str(caught.exception), probe.SAFE_INPUT)

    def test_depth_cap_refuses_valid_shaped_document(self):
        # A valid-shaped input cannot reach the real cap, so lower the shared cap:
        # only the depth check can refuse this otherwise-valid document.
        with mock.patch.object(validation, "MAX_JSON_CONTAINERS", 2):
            with self.assertRaises(probe.RevocationProtocolError):
                probe.decode_input(raw(input_document()))

    def test_actor_site_nonce_and_mode_nullability_refuses(self):
        decoded = probe.decode_input(
            raw(input_document(mode="baseline", baseline=None, request=None))
        )
        self.assertIsNone(decoded["baseline"])
        self.assertIsNone(decoded["request"])

        cases = (
            (
                "baseline mode with a baseline object",
                {"mode": "baseline", "baseline": baseline(), "request": None},
            ),
            (
                "non-baseline mode with a null baseline",
                {"mode": "status_denial", "baseline": None, "request": None},
            ),
            (
                "replay mode without a request",
                {"mode": "replay_denial", "request": None},
            ),
            (
                "status mode with a request",
                {"mode": "status_denial", "request": wire_request()},
            ),
            (
                "verify mode with a request",
                {"mode": "verify", "request": wire_request()},
            ),
            ("actor equals verifier", {"verifier": ACTOR}),
            ("Guest actor", {"actor": "Guest"}),
            ("Administrator verifier", {"verifier": "Administrator"}),
            ("non-approved site", {"site": "other.local"}),
            ("reused run/invocation nonce", {"invocation_nonce": RUN_NONCE}),
            ("141-character actor", {"actor": "x" * 141}),
        )
        for label, overrides in cases:
            with self.subTest(label=label):
                with self.assertRaises(probe.RevocationProtocolError):
                    probe.decode_input(raw(input_document(**overrides)))


class ResponseRefusalTest(unittest.TestCase):
    def test_bool_counts_and_wrong_bindings_refuse(self):
        with self.assertRaises(probe.RevocationProtocolError):
            probe.validate_response(
                response_document(
                    counts={"operations": True, "files": 1, "photos": 1, "versions": 1}
                ),
                expected_response(),
            )

        with self.assertRaises(probe.RevocationProtocolError):
            probe.validate_response(
                response_document(
                    counts={"operations": -1, "files": 1, "photos": 1, "versions": 1}
                ),
                expected_response(),
            )

        with self.assertRaises(probe.RevocationProtocolError):
            probe.baseline_digest(
                {
                    **baseline(),
                    "counts": {
                        "operations": 1,
                        "files": True,
                        "photos": 1,
                        "versions": 1,
                    },
                }
            )

        for fieldname, value in (
            ("run_nonce", "e" * 32),
            ("invocation_nonce", "f" * 32),
            ("arm", "role_removed"),
            ("mode", "restored"),
        ):
            with self.subTest(fieldname=fieldname):
                with self.assertRaises(probe.RevocationProtocolError):
                    probe.validate_response(
                        response_document(**{fieldname: value}),
                        expected_response(),
                    )

        with self.assertRaises(probe.RevocationProtocolError):
            probe.validate_response(
                response_document(result="observed", counts=None),
                expected_response(),
            )

        unknown_expected = {**expected_response(), "payload_digest": "0" * 64}
        with self.assertRaises(probe.RevocationProtocolError):
            probe.validate_response(response_document(), unknown_expected)


class BaselineDigestTest(unittest.TestCase):
    def test_private_baseline_digest_binds_all_roles(self):
        digest = probe.baseline_digest(baseline())
        self.assertEqual(len(digest), 64)
        self.assertEqual(digest, probe.baseline_digest(baseline()))

        reordered = baseline()
        reordered["roles"] = list(reversed(reordered["roles"]))
        self.assertEqual(digest, probe.baseline_digest(reordered))
        self.assertEqual(
            reordered["roles"][0]["role"], "Nursing User"
        )  # input not mutated

        key_reordered = baseline()
        key_reordered["roles"] = [
            {"parentfield": "roles", "role": "Physician", "parenttype": "User"},
            {"parentfield": "roles", "role": "Nursing User", "parenttype": "User"},
        ]
        self.assertEqual(digest, probe.baseline_digest(key_reordered))

        duplicated = baseline()
        duplicated["roles"].append(dict(duplicated["roles"][0]))
        self.assertNotEqual(digest, probe.baseline_digest(duplicated))

        removed = baseline()
        removed["roles"] = removed["roles"][:1]
        self.assertNotEqual(digest, probe.baseline_digest(removed))

        for fieldname, value in (
            ("enabled", False),
            ("actor_digest", "6" * 64),
            ("operation_digest", "7" * 64),
            ("evidence_digest", "8" * 64),
        ):
            with self.subTest(fieldname=fieldname):
                changed = baseline()
                changed[fieldname] = value
                self.assertNotEqual(digest, probe.baseline_digest(changed))

        changed_counts = baseline()
        changed_counts["counts"]["files"] = 2
        self.assertNotEqual(digest, probe.baseline_digest(changed_counts))

        with self.assertRaises(probe.RevocationProtocolError):
            probe.baseline_digest({**baseline(), "extra": True})
        with self.assertRaises(probe.RevocationProtocolError):
            probe.baseline_digest({**baseline(), "actor_digest": "not-a-digest"})

        decoded = probe.decode_input(raw(input_document()))
        self.assertEqual(
            probe.baseline_digest(decoded["baseline"]), digest
        )


class PublicResponseTest(unittest.TestCase):
    def test_public_response_contains_no_private_identity(self):
        document = response_document()
        validated = probe.validate_response(document, expected_response())
        self.assertEqual(validated, document)
        self.assertEqual(set(validated), probe.OUTPUT_KEYS)

        serialized = json.dumps(
            validated, sort_keys=True, separators=(",", ":")
        )
        self.assertLessEqual(
            len(serialized.encode("utf-8")), probe.MAX_RESPONSE_BYTES
        )
        for private in (ACTOR, VERIFIER, SITE, SYNTHETIC_PATIENT, "Physician"):
            self.assertNotIn(private, serialized)

        with self.assertRaises(probe.RevocationProtocolError):
            probe.validate_response(
                {**document, "actor": ACTOR}, expected_response()
            )

        refused = probe.validate_response(
            response_document(
                result="refused",
                enabled=None,
                clinician=None,
                baseline_digest=None,
                evidence_digest=None,
                counts=None,
            ),
            expected_response(),
        )
        self.assertEqual(refused["result"], "refused")
        self.assertIsNone(refused["baseline_digest"])


PUBLICATION_GATE = probe.PUBLICATION_GATE_CONFIG_KEY
SYNTHETIC_GATE = probe.SYNTHETIC_MODE_CONFIG_KEY
OPERATION_DOCTYPE = journal.OPERATION_DOCTYPE
PHOTO_DOCTYPE = inspection.PHOTO_DOCTYPE
VERSION_DOCTYPE = inspection.VERSION_DOCTYPE
PHOTO_NAME = "PHOTO-0001"
FILE_NAME = "FILE-0001"
CREATION_IDENTITY = "1000:2000"

ACTOR_ROLES = (
    {"role": "Desk User", "parenttype": "User", "parentfield": "roles"},
    {"role": "Nursing User", "parenttype": "User", "parentfield": "roles"},
    {"role": "Physician", "parenttype": "User", "parentfield": "roles"},
)
VERIFIER_ROLES = (
    {"role": "Physician", "parenttype": "User", "parentfield": "roles"},
)


def fixture_request():
    return contracts.validate_request(wire_request())


def fixture_sha(request=None):
    return hashlib.sha256((request or fixture_request()).image_bytes).hexdigest()


def fixture_key(request=None, payload=None, actor=ACTOR):
    request = request or fixture_request()
    return journal._reservation_key(
        request,
        site=SITE,
        operation_id=OPERATION_ID,
        generation=1,
        payload=payload or journal.payload_digest(request),
    )


def published_row(**overrides):
    request = fixture_request()
    row = {
        "name": OPERATION_ID,
        "site": SITE,
        "actor": ACTOR,
        "kind": request.kind,
        "request_key": request.request_key,
        "identity_digest": journal.identity_digest(
            SITE, ACTOR, request.kind, request.request_key
        ),
        "payload_digest": journal.payload_digest(request),
        "state": journal.PUBLISHED_STATE,
        "draft": None,
        "expected_version": None,
        "original": None,
        "synthetic_patient": request.synthetic_patient,
        "annotation_json": None,
        "image_base64": None,
        **staged_row_fields(),
        "reservation_key": fixture_key(request),
        "generation": 1,
        "expected_sha256": fixture_sha(request),
        "expected_size": len(PNG_1X1_BYTES),
        "published_file": FILE_NAME,
        "published_domain_doctype": PHOTO_DOCTYPE,
        "published_domain_name": PHOTO_NAME,
        "published_version": None,
        "creation_identity": CREATION_IDENTITY,
    }
    row.update(overrides)
    return row


def submitted_row(**overrides):
    request = fixture_request()
    row = {
        "name": OPERATION_ID,
        "site": SITE,
        "actor": ACTOR,
        "kind": request.kind,
        "request_key": request.request_key,
        "identity_digest": journal.identity_digest(
            SITE, ACTOR, request.kind, request.request_key
        ),
        "payload_digest": journal.payload_digest(request),
        "state": "SUBMITTED",
        "draft": None,
        "expected_version": None,
        "original": None,
        "synthetic_patient": request.synthetic_patient,
        "annotation_json": None,
        "image_base64": None,
        **staged_row_fields(),
        "reservation_key": None,
        "generation": None,
        "expected_sha256": None,
        "expected_size": None,
        "published_file": None,
        "published_domain_doctype": None,
        "published_domain_name": None,
        "published_version": None,
        "creation_identity": None,
    }
    row.update(overrides)
    return row


def documents(
    *,
    operation=None,
    enabled=1,
    actor_roles=ACTOR_ROLES,
    verifier_enabled=1,
    verifier_roles=VERIFIER_ROLES,
    file_owner=None,
    domain_name=PHOTO_NAME,
):
    request = fixture_request()
    key = fixture_key(request)
    owner = file_owner if file_owner is not None else ACTOR
    role_rows = {}
    for index, role in enumerate(actor_roles):
        role_rows[f"actor-role-{index}"] = dict(role, parent=ACTOR)
    for index, role in enumerate(verifier_roles):
        role_rows[f"verifier-role-{index}"] = dict(role, parent=VERIFIER)
    return {
        OPERATION_DOCTYPE: {OPERATION_ID: operation or published_row()},
        "File": {
            FILE_NAME: {
                "name": FILE_NAME,
                "file_url": key,
                "file_name": key.rsplit("/", 1)[-1],
                "is_private": 1,
                "owner": owner,
                "file_size": len(PNG_1X1_BYTES),
            }
        },
        PHOTO_DOCTYPE: {
            domain_name: {
                "name": domain_name,
                "storage_key": key,
                "original_file": FILE_NAME,
                "original_sha256": fixture_sha(request),
                "patient_reference": SYNTHETIC_PATIENT,
            }
        },
        VERSION_DOCTYPE: {},
        "User": {
            ACTOR: {"name": ACTOR, "enabled": enabled},
            VERIFIER: {"name": VERIFIER, "enabled": verifier_enabled},
        },
        "Has Role": role_rows,
    }


class _Callbacks:
    def __init__(self, functions=None, *, known=True):
        self._functions = list(functions or [])
        if not known:
            del self._functions


class _FakeDB:
    """Narrow read surface the probe body actually calls."""

    def __init__(self, docs, *, aliases=None):
        self.docs = {
            doctype: {name: dict(row) for name, row in rows.items()}
            for doctype, rows in docs.items()
        }
        self.aliases = dict(aliases or {})
        self.before_commit = _Callbacks()
        self.after_commit = _Callbacks()
        self.before_rollback = _Callbacks()
        self.after_rollback = _Callbacks()
        self.transaction_writes = 0

    def _select(self, doctype, filters):
        rows = list(self.docs.get(doctype, {}).values())
        if not filters:
            return rows
        for fieldname, expected in filters.items():
            rows = [row for row in rows if row.get(fieldname) == expected]
        return rows

    def get_value(
        self, doctype, filters=None, fieldname="name", as_dict=False, **_kwargs
    ):
        if isinstance(filters, str):
            row = self.docs.get(doctype, {}).get(filters)
            rows = [row] if row else []
        else:
            rows = self._select(doctype, filters)
        if not rows:
            return None
        row = rows[0]
        if (
            doctype == "User"
            and isinstance(filters, str)
            and fieldname == "name"
            and filters in self.aliases
        ):
            return self.aliases[filters]
        if as_dict:
            fields = (
                fieldname if isinstance(fieldname, (list, tuple)) else [fieldname]
            )
            return {field: row.get(field) for field in fields}
        if isinstance(fieldname, (list, tuple)):
            return [row.get(field) for field in fieldname]
        return row.get(fieldname)

    def get_all(
        self, doctype, filters=None, fields=None, pluck=None, limit=None, **_kwargs
    ):
        rows = self._select(doctype, filters)
        if limit:
            rows = rows[:limit]
        if pluck:
            return [row.get(pluck) for row in rows]
        if fields:
            return [{field: row.get(field) for field in fields} for row in rows]
        return [dict(row) for row in rows]

    def count(self, doctype, filters=None):
        return len(self._select(doctype, filters))


class _FakeFrappe(types.ModuleType):
    def __init__(
        self,
        docs,
        *,
        actual_site=SITE,
        publication_gate=True,
        synthetic_gate=True,
        set_user_failure=None,
        destroy_failure=False,
        aliases=None,
        db=None,
        noise=None,
        init_failure=None,
        connect_failure=None,
        fd_noise=False,
    ):
        super().__init__("frappe")
        self.local = types.SimpleNamespace(site="")
        self.session = types.SimpleNamespace(user="Guest")
        self.conf = {
            PUBLICATION_GATE: publication_gate,
            SYNTHETIC_GATE: synthetic_gate,
        }
        self.db = db if db is not None else _FakeDB(docs, aliases=aliases)
        self.actual_site = actual_site
        self.set_user_failure = set_user_failure
        self.destroy_failure = destroy_failure
        self.noise = noise
        self.init_failure = init_failure
        self.connect_failure = connect_failure
        self.fd_noise = fd_noise
        self.init_calls = []
        self.connect_calls = []
        self.set_user_calls = []
        self.destroy_calls = 0

    def _noise(self, label):
        if self.noise is None:
            return
        print(f"{label} {self.noise}", flush=True)
        print(f"{label} {self.noise}", file=sys.stderr, flush=True)
        if self.fd_noise:
            os.write(1, f"{label}-fd {self.noise}\n".encode("utf-8"))
            os.write(2, f"{label}-fd {self.noise}\n".encode("utf-8"))

    def init(self, site, sites_path=".", **_kwargs):
        self._noise("native-init")
        self.init_calls.append((site, sites_path))
        if self.init_failure is not None:
            raise self.init_failure
        self.local.site = self.actual_site

    def get_site_path(self, *parts):
        return os.path.join(STAGE_ROOT, self.local.site, *parts)

    def connect(self, site=None, db_name=None, set_admin_as_user=True):
        self._noise("native-connect")
        self.connect_calls.append(set_admin_as_user)
        if self.connect_failure is not None:
            raise self.connect_failure
        if set_admin_as_user:
            self.session.user = "Administrator"

    def set_user(self, user):
        self._noise("native-set-user")
        self.set_user_calls.append(user)
        if self.set_user_failure == user:
            raise RuntimeError("native adoption refused")
        self.session.user = user

    def destroy(self):
        self._noise("native-destroy")
        self.destroy_calls += 1
        if self.destroy_failure:
            raise RuntimeError("native context destruction failed")


@contextlib.contextmanager
def native_runtime(docs, **kwargs):
    module = _FakeFrappe(docs, **kwargs)
    previous = sys.modules.get("frappe")
    sys.modules["frappe"] = module
    try:
        yield module
    finally:
        if previous is None:
            sys.modules.pop("frappe", None)
        else:
            sys.modules["frappe"] = previous


@contextlib.contextmanager
def byte_proof(digest=None):
    with mock.patch.object(
        writer,
        "verify_committed_object",
        return_value=fixture_sha() if digest is None else digest,
    ) as spy:
        yield spy


def decoded(**overrides):
    document = input_document()
    document.update(overrides)
    return probe.decode_input(raw(document))


def baseline_document(**overrides):
    values = {"mode": "baseline", "baseline": None, "request": None}
    values.update(overrides)
    return decoded(**values)


def denial_document(mode, private_baseline, **overrides):
    values = {
        "mode": mode,
        "baseline": private_baseline,
        "request": wire_request() if mode == "replay_denial" else None,
    }
    values.update(overrides)
    return decoded(**values)


def saved_outcome(status="saved"):
    return contracts.SaveOutcome(status, OPERATION_ID)


def state_baseline(roles=ACTOR_ROLES):
    """Baseline shape for bootstrap refusal tests; evidence values are irrelevant."""
    value = baseline()
    value["roles"] = [dict(role) for role in roles]
    return value


class ProbeTestCase(unittest.TestCase):
    def baseline_private(self, docs, **kwargs):
        """Capture a real Task2 baseline value for later exact comparisons."""
        with native_runtime(docs, **kwargs), byte_proof(), mock.patch.object(
            inspection, "resolve_operation", return_value=saved_outcome()
        ):
            exit_code, response, private = probe.run_probe(baseline_document())
        self.assertEqual(exit_code, 0, response)
        self.assertEqual(response["result"], "observed")
        self.assertIsNotNone(private)
        return private


class RunProbeInterfaceTest(ProbeTestCase):
    def test_decoded_input_interface_and_input_bound_constants(self):
        # LOW-FIX1: the operational bound is an independent constant equality, not
        # only the expression the parser happens to use.
        self.assertEqual(
            probe.MAX_INPUT_BYTES, validation.MAX_REQUEST_BYTES + 65536
        )
        self.assertEqual(validation.MAX_REQUEST_BYTES, 12 * 1024 * 1024)
        self.assertEqual(probe.MAX_RESPONSE_BYTES, 16384)
        self.assertEqual(probe.SCHEMA_VERSION, 1)

        # A raw wire dict with an untyped request is not the intended interface.
        with self.assertRaises(probe.RevocationProtocolError):
            probe.run_probe(input_document())

        # A decoded document is accepted, and equality of the actor and verifier is
        # re-checked at the internal boundary as well as during decoding.
        normalized = baseline_document()
        with self.assertRaises(probe.RevocationProtocolError):
            probe.run_probe({**normalized, "verifier": normalized["actor"]})
        with self.assertRaises(probe.RevocationProtocolError):
            probe.run_probe({**normalized, "extra": True})

    def test_bootstrap_alias_disabled_adoption_and_verifier_refuse(self):
        document = denial_document("status_denial", state_baseline())
        target = mock.Mock(side_effect=AssertionError("target boundary called"))
        cases = (
            (
                "wrong actual site",
                {"actual_site": "other.local"},
                {},
            ),
            (
                "publication gate integer one",
                {"publication_gate": 1},
                {},
            ),
            (
                "publication gate missing",
                {"publication_gate": None},
                {},
            ),
            (
                "synthetic gate false",
                {"synthetic_gate": False},
                {},
            ),
            (
                "actor alias spelling",
                {"aliases": {ACTOR: "Synthetic-Clinician@example.test"}},
                {},
            ),
            (
                "verifier alias spelling",
                {"aliases": {VERIFIER: "synthetic-verifier@example.test"}},
                {},
            ),
            (
                "actor adoption failure",
                {"set_user_failure": ACTOR},
                {},
            ),
            (
                "verifier disabled",
                {},
                {"verifier_enabled": 0},
            ),
            (
                "verifier without clinician role",
                {},
                {"verifier_roles": ACTOR_ROLES[:1]},
            ),
            (
                "unclean after_commit manager",
                {},
                {"_unclean_callback": "after_commit"},
            ),
            (
                "unknown callback manager shape",
                {},
                {"_unknown_manager": True},
            ),
            (
                "nonzero transaction write counter",
                {},
                {"_writes": 1},
            ),
            (
                "unknown transaction write counter",
                {},
                {"_writes": "0"},
            ),
        )
        for label, runtime_kwargs, doc_kwargs in cases:
            with self.subTest(label=label):
                writes = doc_kwargs.pop("_writes", 0)
                unclean = doc_kwargs.pop("_unclean_callback", None)
                unknown_manager = doc_kwargs.pop("_unknown_manager", False)
                docs = documents(**doc_kwargs)
                with native_runtime(docs, **runtime_kwargs) as frappe:
                    frappe.db.transaction_writes = writes
                    if unclean is not None:
                        getattr(frappe.db, unclean)._functions.append(lambda: None)
                    if unknown_manager:
                        frappe.db.before_commit = _Callbacks(known=False)
                    with mock.patch.object(
                        inspection, "resolve_operation", target
                    ), mock.patch.object(submission, "stage_submission", target):
                        exit_code, response, private = probe.run_probe(document)
                self.assertEqual(exit_code, 2, response)
                self.assertEqual(response["result"], "refused")
                self.assertIsNone(private)
                self.assertEqual(frappe.init_calls, [(SITE, probe.SITES_PATH)])
                self.assertEqual(frappe.connect_calls, [False])
                self.assertFalse(target.called)


class ChangedStateDenialTest(ProbeTestCase):
    def _changed_docs(self, arm):
        if arm == "disabled":
            return documents(enabled=0)
        kept = tuple(
            role for role in ACTOR_ROLES if role["role"] == "Desk User"
        )
        return documents(actor_roles=kept)

    def _assert_denial(self, mode):
        # The baseline is captured from the unchanged state so the changed state is
        # the only difference between the two invocations.
        private = self.baseline_private(documents())
        for arm in ("disabled", "role_removed"):
            with self.subTest(arm=arm, mode=mode):
                docs = self._changed_docs(arm)
                document = denial_document(mode, private, arm=arm)
                with native_runtime(docs), byte_proof():
                    exit_code, response, returned = probe.run_probe(document)
                self.assertEqual(exit_code, 0, response)
                self.assertEqual(response["result"], "observed")
                self.assertTrue(response["clean"])
                self.assertIsNone(returned)
                if arm == "disabled":
                    self.assertIs(response["enabled"], False)
                    self.assertIs(response["clinician"], True)
                else:
                    self.assertIs(response["enabled"], True)
                    self.assertIs(response["clinician"], False)
                self.assertEqual(
                    response["baseline_digest"], probe.baseline_digest(private)
                )
                self.assertEqual(
                    response["evidence_digest"], private["evidence_digest"]
                )
                self.assertEqual(response["counts"], private["counts"])
                self.assertEqual(
                    response["counts"],
                    {"operations": 1, "files": 1, "photos": 1, "versions": 0},
                )

    def test_both_changed_states_require_exact_denials(self):
        self._assert_denial("status_denial")
        self._assert_denial("replay_denial")

    def test_wrong_denial_outcome_or_class_never_receives_credit(self):
        private = self.baseline_private(documents())
        document = denial_document("status_denial", private)

        cases = (
            (
                "saved instead of denied",
                mock.patch.object(
                    inspection, "resolve_operation", return_value=saved_outcome()
                ),
            ),
            (
                "pending instead of denied",
                mock.patch.object(
                    inspection,
                    "resolve_operation",
                    return_value=saved_outcome("pending"),
                ),
            ),
            (
                "unknown instead of denied",
                mock.patch.object(
                    inspection,
                    "resolve_operation",
                    return_value=saved_outcome("unknown"),
                ),
            ),
            (
                "different inspection exception",
                mock.patch.object(
                    inspection,
                    "resolve_operation",
                    side_effect=inspection.InspectionUnavailable(
                        inspection.SAFE_INSPECTION_UNAVAILABLE
                    ),
                ),
            ),
        )
        for label, patched in cases:
            with self.subTest(label=label):
                with native_runtime(documents(enabled=0)), byte_proof(), patched:
                    exit_code, response, returned = probe.run_probe(document)
                self.assertNotEqual(exit_code, 0, response)
                self.assertIn(response["result"], {"failed", "unavailable"})
                self.assertIsNone(returned)

        # Bootstrap refusal is not an application denial either.
        with native_runtime(documents(enabled=0, actor_roles=ACTOR_ROLES[:1])), \
                byte_proof():
            exit_code, response, _ = probe.run_probe(document)
        self.assertEqual(exit_code, 2, response)
        self.assertEqual(response["result"], "refused")

        # Replay: any success/pending/unknown outcome or other submission error fails.
        replay = denial_document("replay_denial", private)
        replay_cases = (
            ("staged instead of denied", saved_outcome("pending")),
            ("saved instead of denied", saved_outcome()),
        )
        for label, outcome in replay_cases:
            with self.subTest(label=label):
                with native_runtime(documents(enabled=0)), byte_proof(), \
                        mock.patch.object(
                            submission, "stage_submission", return_value=outcome
                        ):
                    exit_code, response, _ = probe.run_probe(replay)
                self.assertNotEqual(exit_code, 0, response)
                self.assertEqual(response["result"], "failed")
        with native_runtime(documents(enabled=0)), byte_proof(), \
                mock.patch.object(
                    submission,
                    "stage_submission",
                    side_effect=submission.SubmissionUnavailable(
                        submission.SAFE_SUBMISSION_UNAVAILABLE
                    ),
                ):
            exit_code, response, _ = probe.run_probe(replay)
        self.assertEqual(exit_code, 3, response)
        self.assertEqual(response["result"], "failed")

    def test_denial_with_write_or_callback_fails(self):
        private = self.baseline_private(documents())
        document = denial_document("status_denial", private)

        def write_then_deny(operation_id, *, actor):
            frappe.db.transaction_writes += 1
            raise inspection.InspectionDenied(inspection.SAFE_INSPECTION_DENIED)

        with native_runtime(documents(enabled=0)) as frappe, byte_proof(), \
                mock.patch.object(
                    inspection, "resolve_operation", side_effect=write_then_deny
                ):
            exit_code, response, _ = probe.run_probe(document)
        self.assertEqual(response["result"], "failed", response)
        self.assertFalse(response["clean"])
        self.assertNotEqual(exit_code, 0)

        def callback_then_deny(operation_id, *, actor):
            frappe.db.after_commit._functions.append(lambda: None)
            raise inspection.InspectionDenied(inspection.SAFE_INSPECTION_DENIED)

        with native_runtime(documents(enabled=0)) as frappe, byte_proof(), \
                mock.patch.object(
                    inspection, "resolve_operation", side_effect=callback_then_deny
                ):
            exit_code, response, _ = probe.run_probe(document)
        self.assertEqual(response["result"], "failed", response)
        self.assertFalse(response["clean"])

        # A changed transaction counter still fails even when the fresh committed
        # readback would match the baseline.
        def only_write_then_deny(operation_id, *, actor):
            frappe.db.transaction_writes += 1
            raise inspection.InspectionDenied(inspection.SAFE_INSPECTION_DENIED)

        with native_runtime(documents(enabled=0)) as frappe, byte_proof(), \
                mock.patch.object(
                    inspection, "resolve_operation", side_effect=only_write_then_deny
                ):
            exit_code, response, _ = probe.run_probe(document)
        self.assertFalse(response["clean"])
        self.assertEqual(response["result"], "failed")


class RequestBindingTest(ProbeTestCase):
    def test_request_binding_precedes_replay(self):
        private = self.baseline_private(documents())
        stage = mock.Mock(side_effect=AssertionError("replay reachable"))
        load_submission = mock.Mock(
            side_effect=AssertionError("PUBLISHED loaded as SUBMITTED")
        )

        variants = (
            (
                "payload digest mismatch",
                documents(
                    enabled=0,
                    operation=published_row(payload_digest="9" * 64),
                ),
                wire_request(),
            ),
            (
                "not published",
                documents(enabled=0, operation=submitted_row()),
                wire_request(),
            ),
            (
                "operation actor mismatch",
                documents(
                    enabled=0,
                    operation=published_row(
                        actor="other-clinician@example.test",
                        identity_digest=journal.identity_digest(
                            SITE,
                            "other-clinician@example.test",
                            fixture_request().kind,
                            fixture_request().request_key,
                        ),
                    ),
                ),
                wire_request(),
            ),
            (
                "presented request key mismatch",
                documents(enabled=0),
                {**wire_request(), "request_key": "request-key-0000000000000002"},
            ),
        )
        for label, docs, request in variants:
            with self.subTest(label=label):
                document = denial_document(
                    "replay_denial", private, request=request
                )
                with native_runtime(docs), byte_proof(), \
                        mock.patch.object(submission, "stage_submission", stage), \
                        mock.patch.object(journal, "load_submission", load_submission):
                    exit_code, response, _ = probe.run_probe(document)
                self.assertEqual(exit_code, 2, response)
                self.assertEqual(response["result"], "refused")
                self.assertFalse(stage.called)
                self.assertFalse(load_submission.called)

        # The exact original request proceeds to exactly one replay call.
        called = mock.Mock(
            side_effect=submission.SubmissionAuthorityDenied(
                submission.SAFE_SUBMISSION_UNAVAILABLE
            )
        )
        document = denial_document("replay_denial", private)
        with native_runtime(documents(enabled=0)), byte_proof(), \
                mock.patch.object(submission, "stage_submission", called), \
                mock.patch.object(journal, "load_submission", load_submission):
            exit_code, response, _ = probe.run_probe(document)
        self.assertEqual(exit_code, 0, response)
        self.assertEqual(response["result"], "observed")
        self.assertEqual(called.call_count, 1)
        self.assertEqual(
            called.call_args.args[0].request_key, wire_request()["request_key"]
        )
        self.assertFalse(load_submission.called)


class BaselineEvidenceTest(ProbeTestCase):
    def test_baseline_saved_and_verification_bind_bytes_links_counts(self):
        docs = documents()
        with native_runtime(docs), byte_proof() as proof, mock.patch.object(
            inspection, "resolve_operation", return_value=saved_outcome()
        ) as saved:
            exit_code, response, private = probe.run_probe(baseline_document())
        self.assertEqual(exit_code, 0, response)
        self.assertEqual(response["result"], "observed")
        self.assertTrue(response["clean"])
        self.assertIs(response["enabled"], True)
        self.assertIs(response["clinician"], True)
        self.assertEqual(saved.call_count, 1)
        self.assertGreaterEqual(proof.call_count, 1)
        self.assertEqual(private["enabled"], True)
        self.assertEqual(
            private["roles"],
            sorted(
                ACTOR_ROLES,
                key=lambda role: (
                    role["role"],
                    role["parenttype"],
                    role["parentfield"],
                ),
            ),
        )
        self.assertEqual(private["counts"], response["counts"])
        self.assertEqual(
            private["counts"],
            {"operations": 1, "files": 1, "photos": 1, "versions": 0},
        )
        for name in ("actor_digest", "operation_digest", "evidence_digest"):
            self.assertRegex(private[name], r"^[0-9a-f]{64}$")
        self.assertEqual(
            response["baseline_digest"], probe.baseline_digest(private)
        )
        self.assertEqual(response["evidence_digest"], private["evidence_digest"])

        # The public response carries no private names.
        serialized = json.dumps(response, sort_keys=True)
        for name in (ACTOR, VERIFIER, SITE, PHOTO_NAME, FILE_NAME, SYNTHETIC_PATIENT,
                     "Physician", "Nursing User", "Desk User", "/private/files/"):
            self.assertNotIn(name, serialized)

        # Verify mode reads only the enabled verifier and never the actor's clinical
        # status or the operator audit.
        document = decoded(mode="verify", baseline=private, request=None)
        with native_runtime(docs) as frappe, byte_proof(), mock.patch.object(
            inspection, "resolve_operation", return_value=saved_outcome()
        ) as forbidden_resolve, mock.patch.object(
            inspection, "inspect_operation", return_value=saved_outcome()
        ) as forbidden_audit:
            verify_exit, verify_response, returned = probe.run_probe(document)
        self.assertEqual(verify_exit, 0, verify_response)
        self.assertEqual(verify_response["result"], "observed")
        self.assertIsNone(returned)
        self.assertEqual(frappe.set_user_calls, [VERIFIER])
        self.assertIs(verify_response["enabled"], True)
        self.assertIs(verify_response["clinician"], True)
        self.assertEqual(
            verify_response["evidence_digest"], private["evidence_digest"]
        )
        self.assertFalse(forbidden_resolve.called)
        self.assertFalse(forbidden_audit.called)

        # A saved positive control that is not `saved` never yields a baseline.
        for status in ("pending", "unknown"):
            with self.subTest(status=status):
                with native_runtime(docs), byte_proof(), mock.patch.object(
                    inspection, "resolve_operation",
                    return_value=saved_outcome(status),
                ):
                    refused_exit, refused_response, refused_private = probe.run_probe(
                        baseline_document()
                    )
                self.assertEqual(refused_exit, 2, refused_response)
                self.assertEqual(refused_response["result"], "refused")
                self.assertIsNone(refused_private)

        # A byte-proof mismatch and a linked-File mismatch fail, never weaken.
        with native_runtime(docs), byte_proof(digest="0" * 64), mock.patch.object(
            inspection, "resolve_operation", return_value=saved_outcome()
        ):
            exit_code, response, _ = probe.run_probe(baseline_document())
        self.assertEqual(exit_code, 3, response)
        self.assertEqual(response["result"], "failed")

        bad_file = documents(file_owner="other-owner@example.test")
        with native_runtime(bad_file), byte_proof(), mock.patch.object(
            inspection, "resolve_operation", return_value=saved_outcome()
        ):
            exit_code, response, _ = probe.run_probe(baseline_document())
        self.assertEqual(exit_code, 3, response)
        self.assertEqual(response["result"], "failed")

        # Counts are part of the evidence; a changed baseline count fails verify.
        changed = dict(private, counts={"operations": 1, "files": 2, "photos": 1,
                                        "versions": 0})
        document = decoded(mode="verify", baseline=changed, request=None)
        with native_runtime(docs), byte_proof():
            exit_code, response, _ = probe.run_probe(document)
        self.assertEqual(exit_code, 3, response)
        self.assertEqual(response["result"], "failed")

        # Context destruction failure invalidates an otherwise observed success.
        with native_runtime(docs, destroy_failure=True), byte_proof(), \
                mock.patch.object(
                    inspection, "resolve_operation", return_value=saved_outcome()
                ):
            exit_code, response, returned = probe.run_probe(baseline_document())
        self.assertEqual(exit_code, 3, response)
        self.assertEqual(response["result"], "failed")
        self.assertIsNone(returned)


class RestorationTest(ProbeTestCase):
    def test_restoration_requires_exact_assignment_snapshot(self):
        baseline_docs = documents()
        private = self.baseline_private(baseline_docs)
        document = decoded(mode="restored", baseline=private, request=None)

        with native_runtime(baseline_docs), byte_proof(), mock.patch.object(
            inspection, "resolve_operation", return_value=saved_outcome()
        ) as saved:
            exit_code, response, returned = probe.run_probe(document)
        self.assertEqual(exit_code, 0, response)
        self.assertEqual(response["result"], "observed")
        self.assertIs(response["enabled"], True)
        self.assertIs(response["clinician"], True)
        self.assertIsNotNone(returned)
        self.assertEqual(probe.baseline_digest(returned),
                         probe.baseline_digest(private))
        self.assertEqual(returned["roles"], private["roles"])
        self.assertEqual(saved.call_count, 1)

        missing_unrelated = documents(
            actor_roles=tuple(
                role for role in ACTOR_ROLES if role["role"] != "Desk User"
            )
        )
        extra = documents(
            actor_roles=ACTOR_ROLES
            + ({"role": "System Manager", "parenttype": "User",
                "parentfield": "roles"},)
        )
        duplicated = documents(actor_roles=ACTOR_ROLES + (ACTOR_ROLES[2],))
        disabled = documents(enabled=0)

        for label, docs in (
            ("missing unrelated role", missing_unrelated),
            ("extra role", extra),
            ("duplicated multiplicity", duplicated),
            ("still disabled", disabled),
        ):
            with self.subTest(label=label):
                target = mock.Mock(
                    side_effect=AssertionError("positive control reachable")
                )
                with native_runtime(docs), byte_proof(), \
                        mock.patch.object(inspection, "resolve_operation", target):
                    exit_code, response, returned = probe.run_probe(document)
                self.assertEqual(exit_code, 2, response)
                self.assertEqual(response["result"], "refused")
                self.assertIsNone(returned)
                self.assertFalse(target.called)

        # Verify-only observation of the changed state is not restoration proof.
        with native_runtime(baseline_docs), byte_proof(), mock.patch.object(
            inspection, "resolve_operation",
            return_value=saved_outcome("pending"),
        ):
            exit_code, response, _ = probe.run_probe(document)
        self.assertEqual(exit_code, 2, response)
        self.assertEqual(response["result"], "refused")


class ChangedStateExactnessTest(ProbeTestCase):
    """F1: unrelated role-assignment drift must refuse, not be credited."""

    def test_unrelated_assignment_changes_refuse_before_the_target(self):
        private = self.baseline_private(documents())
        extra = {"role": "System Manager", "parenttype": "User",
                 "parentfield": "roles"}
        removed_unrelated = tuple(
            role for role in ACTOR_ROLES if role["role"] != "Desk User"
        )
        removed_candidates = tuple(
            role for role in ACTOR_ROLES if role["role"] == "Desk User"
        )
        cases = (
            (
                "disabled arm, unrelated role removed",
                "disabled",
                documents(enabled=0, actor_roles=removed_unrelated),
            ),
            (
                "disabled arm, unrelated role added",
                "disabled",
                documents(enabled=0, actor_roles=ACTOR_ROLES + (extra,)),
            ),
            (
                "role-removed arm, unrelated role removed",
                "role_removed",
                documents(enabled=1, actor_roles=()),
            ),
            (
                "role-removed arm, unrelated role added",
                "role_removed",
                documents(enabled=1, actor_roles=removed_candidates + (extra,)),
            ),
        )
        for mode in ("status_denial", "replay_denial"):
            for label, arm, docs in cases:
                with self.subTest(label=label, mode=mode, arm=arm):
                    target = mock.Mock(
                        side_effect=AssertionError("target boundary called")
                    )
                    document = denial_document(mode, private, arm=arm)
                    self.assertEqual(document["arm"], arm)
                    with native_runtime(docs), byte_proof(), \
                            mock.patch.object(
                                inspection, "resolve_operation", target
                            ), mock.patch.object(
                                submission, "stage_submission", target
                            ):
                        exit_code, response, returned = probe.run_probe(document)
                    self.assertEqual(exit_code, 2, response)
                    self.assertEqual(response["result"], "refused")
                    self.assertEqual(response["arm"], arm)
                    self.assertEqual(response["mode"], mode)
                    self.assertIsNone(returned)
                    self.assertFalse(target.called)


class DenialEvidenceChangeTest(ProbeTestCase):
    """F2: valid denial plus changed committed counts fails in the denial branch."""

    def test_denial_with_changed_committed_counts_fails_in_denial_branch(self):
        private = self.baseline_private(documents())
        docs = documents(enabled=0)
        docs[OPERATION_DOCTYPE]["f" * 32] = {
            "name": "f" * 32,
            "actor": ACTOR,
        }
        for mode in ("status_denial", "replay_denial"):
            with self.subTest(mode=mode):
                document = denial_document(mode, private)
                real_matches = probe._require_matches
                calls = []

                def counted_matches(baseline, evidence):
                    calls.append(evidence["counts"])
                    return real_matches(baseline, evidence)

                with native_runtime(docs), byte_proof(), \
                        mock.patch.object(
                            probe, "_require_matches",
                            side_effect=counted_matches,
                        ):
                    exit_code, response, returned = probe.run_probe(document)
                self.assertEqual(exit_code, 3, response)
                self.assertEqual(response["result"], "failed")
                self.assertFalse(response["clean"])
                self.assertIsNone(returned)
                self.assertEqual(calls, [{
                    "operations": 2,
                    "files": 1,
                    "photos": 1,
                    "versions": 0,
                }])


class NativeBootstrapFactsTest(ProbeTestCase):
    NOISE = "PRIVATE-NATIVE-DIAGNOSTIC-VALUE"

    def test_init_and_connect_failures_are_refused_without_target(self):
        private = self.baseline_private(documents())
        document = denial_document("status_denial", private)
        for label, kwargs in (
            ("init", {"init_failure": RuntimeError(self.NOISE)}),
            ("connect", {"connect_failure": RuntimeError(self.NOISE)}),
        ):
            with self.subTest(label=label):
                target = mock.Mock(
                    side_effect=AssertionError("target boundary called")
                )
                native_output = io.StringIO()
                native_errors = io.StringIO()
                with native_runtime(
                    documents(enabled=0), noise=self.NOISE, **kwargs
                ), byte_proof(), mock.patch.object(
                    inspection, "resolve_operation", target
                ), mock.patch.object(
                    submission, "stage_submission", target
                ), contextlib.redirect_stdout(native_output), \
                        contextlib.redirect_stderr(native_errors):
                    exit_code, response, _ = probe.run_probe(document)
                self.assertEqual(exit_code, 2, response)
                self.assertEqual(response["result"], "refused")
                self.assertFalse(response["clean"])
                self.assertFalse(target.called)
                # The double really is noisy; the public main path is the suppressor.
                self.assertIn(self.NOISE, native_output.getvalue())

    def test_native_constants_are_pinned_to_shared_sources(self):
        from clinic_photos.publication import process

        self.assertEqual(probe.SITES_PATH, process.SITES_PATH)
        self.assertEqual(
            probe.PUBLICATION_GATE_CONFIG_KEY, process.GATE_CONFIG_KEY
        )
        self.assertIn(probe.APPROVED_SITE, process.ALLOWED_SITES)
        self.assertEqual(
            probe.SYNTHETIC_MODE_CONFIG_KEY,
            submission.SYNTHETIC_MODE_CONFIG_KEY,
        )
        self.assertEqual(probe.MAX_BASELINE_BYTES, 65536)

    def test_verify_reports_verifier_facts_not_the_changed_actor(self):
        private = self.baseline_private(documents())
        verify = decoded(mode="verify", baseline=private, request=None)
        with native_runtime(documents(enabled=0)) as frappe, byte_proof(), \
                mock.patch.object(
                    inspection, "resolve_operation",
                    side_effect=AssertionError("actor status read"),
                ) as forbidden:
            exit_code, response, _ = probe.run_probe(verify)
        self.assertEqual(exit_code, 0, response)
        self.assertEqual(response["result"], "observed")
        self.assertIs(response["enabled"], True)
        self.assertIs(response["clinician"], True)
        self.assertEqual(frappe.set_user_calls, [VERIFIER])
        self.assertFalse(forbidden.called)


class NativeOutputSuppressionTest(ProbeTestCase):
    NOISE = "PRIVATE-NATIVE-DIAGNOSTIC-VALUE"

    def test_noisy_native_output_never_reaches_public_response(self):
        arguments = ["--site", SITE, "--operation-id", OPERATION_ID,
                     "--mode", "baseline"]
        with tempfile.TemporaryFile() as descriptor, \
                native_runtime(documents(), noise=self.NOISE, fd_noise=True), \
                byte_proof(), mock.patch.object(
                    inspection, "resolve_operation", return_value=saved_outcome()
                ):
            code, output = invoke_main(
                arguments, raw(baseline_document()),
                environment={probe.BASELINE_FD_ENV: str(descriptor.fileno())},
            )
        self.assertEqual(code, 0, output)
        self.assertEqual(output.count("\n"), 1)
        document = json.loads(output.strip())
        self.assertEqual(set(document), probe.OUTPUT_KEYS)
        self.assertEqual(document["result"], "observed")
        self.assertTrue(document["clean"])
        self.assertNotIn(self.NOISE, output)
        self.assertNotIn("native-init", output)
        self.assertNotIn("native-connect", output)
        self.assertNotIn("native-set-user", output)
        self.assertNotIn("native-destroy", output)

    def test_noisy_native_exception_still_emits_only_one_safe_document(self):
        private = self.baseline_private(documents())
        document = denial_document("status_denial", private)
        with native_runtime(
            documents(enabled=0),
            noise=self.NOISE,
            fd_noise=True,
            init_failure=RuntimeError(self.NOISE),
        ), byte_proof(), mock.patch.object(
            inspection, "resolve_operation",
            side_effect=AssertionError("target boundary called"),
        ):
            code, output = invoke_main(
                ["--site", SITE, "--operation-id", OPERATION_ID,
                 "--mode", "status_denial"],
                raw(document),
            )
        self.assertEqual(code, 2, output)
        self.assertEqual(output.count("\n"), 1)
        response = json.loads(output.strip())
        self.assertEqual(set(response), probe.OUTPUT_KEYS)
        self.assertEqual(response["result"], "refused")
        self.assertFalse(response["clean"])
        self.assertNotIn(self.NOISE, output)
        self.assertNotIn("Traceback", output)

    def test_partial_open_failure_restores_output_and_emits_one_failure(self):
        private = self.baseline_private(documents())
        document = denial_document("status_denial", private)
        real_dup2 = os.dup2
        calls = {"count": 0}

        def fail_first_dup2(descriptor, target):
            calls["count"] += 1
            if calls["count"] == 1:
                raise OSError("open dup2 failed")
            return real_dup2(descriptor, target)

        output = io.StringIO()
        before_fds = len(os.listdir("/dev/fd"))
        with native_runtime(documents(enabled=0)), byte_proof(), \
                mock.patch.object(sys, "stdin", _Stdin(raw(document))), \
                mock.patch.dict(os.environ, {"LC_ALL": "C"}, clear=True), \
                mock.patch.object(
                    probe.os, "dup2", side_effect=fail_first_dup2
                ), contextlib.redirect_stdout(output):
            code = probe.main(
                ["--site", SITE, "--operation-id", OPERATION_ID,
                 "--mode", "status_denial"]
            )
        after_fds = len(os.listdir("/dev/fd"))
        self.assertEqual(code, 3)
        self.assertEqual(output.getvalue().count("\n"), 1)
        response = json.loads(output.getvalue().strip())
        self.assertEqual(set(response), probe.OUTPUT_KEYS)
        self.assertEqual(response["result"], "failed")
        self.assertFalse(response["clean"])
        self.assertGreaterEqual(calls["count"], 3)
        # Partial-open cleanup releases every descriptor the guard captured.
        self.assertEqual(after_fds, before_fds)

    def test_real_restore_failure_keeps_saved_fd_for_one_failure_document(self):
        private = self.baseline_private(documents())
        document = denial_document("status_denial", private)
        captured = tempfile.TemporaryFile()
        saved_stdout = os.dup(1)
        real_dup2 = os.dup2
        os.dup2(captured.fileno(), 1)
        calls = {"count": 0}

        def fail_restore_dup2(descriptor, target):
            calls["count"] += 1
            if calls["count"] == 3:
                raise OSError("restore dup2 failed")
            return real_dup2(descriptor, target)

        original_stdout = sys.stdout
        before_fds = len(os.listdir("/dev/fd"))
        try:
            with native_runtime(documents(enabled=0)), byte_proof(), \
                    mock.patch.object(sys, "stdin", _Stdin(raw(document))), \
                    mock.patch.dict(os.environ, {"LC_ALL": "C"}, clear=True), \
                    mock.patch.object(
                        probe.os, "dup2", side_effect=fail_restore_dup2
                    ):
                code = probe.main(
                    ["--site", SITE, "--operation-id", OPERATION_ID,
                     "--mode", "status_denial"]
                )
            after_fds = len(os.listdir("/dev/fd"))
        finally:
            os.dup2(saved_stdout, 1)
            os.close(saved_stdout)
        self.assertIs(sys.stdout, original_stdout)
        self.assertEqual(after_fds, before_fds)
        captured.seek(0)
        payload = captured.read()
        captured.close()
        self.assertEqual(code, 3)
        self.assertEqual(payload.count(b"\n"), 1)
        response = json.loads(payload.decode("utf-8").strip())
        self.assertEqual(set(response), probe.OUTPUT_KEYS)
        self.assertEqual(response["result"], "failed")
        self.assertFalse(response["clean"])
        self.assertEqual(calls["count"], 4)

    def test_release_failure_cannot_earn_observed_credit(self):
        private = self.baseline_private(documents())
        document = denial_document("status_denial", private)
        real_close = os.close
        captured = tempfile.TemporaryFile()
        saved_stdout = os.dup(1)
        os.dup2(captured.fileno(), 1)

        def fail_release_close(descriptor):
            if descriptor > 2:
                raise OSError("release close failed")
            return real_close(descriptor)

        output = io.StringIO()
        try:
            with native_runtime(documents(enabled=0)), byte_proof(), \
                    mock.patch.object(sys, "stdin", _Stdin(raw(document))), \
                    mock.patch.dict(os.environ, {"LC_ALL": "C"}, clear=True), \
                    mock.patch.object(
                        probe.os, "close", side_effect=fail_release_close
                    ), contextlib.redirect_stdout(output):
                code = probe.main(
                    ["--site", SITE, "--operation-id", OPERATION_ID,
                     "--mode", "status_denial"]
                )
        finally:
            os.dup2(saved_stdout, 1)
            os.close(saved_stdout)
            captured.close()
        self.assertEqual(code, 3)
        self.assertEqual(output.getvalue().count("\n"), 1)
        response = json.loads(output.getvalue().strip())
        self.assertEqual(set(response), probe.OUTPUT_KEYS)
        self.assertEqual(response["result"], "failed")
        self.assertFalse(response["clean"])


class _Stdin:
    def __init__(self, payload):
        self.buffer = io.BytesIO(payload)


def invoke_main(arguments, payload, *, environment=None):
    output = io.StringIO()
    env = {"LC_ALL": "C"} if environment is None else dict(environment)
    with mock.patch.object(sys, "stdin", _Stdin(payload)), \
            mock.patch.dict(os.environ, env, clear=True), \
            contextlib.redirect_stdout(output):
        code = probe.main(arguments)
    return code, output.getvalue()


class MainBoundaryTest(ProbeTestCase):
    def assert_full_failure(self, output, *, mode="status_denial",
                            result="refused", arm="disabled"):
        self.assertEqual(output.count("\n"), 1)
        document = json.loads(output.strip())
        self.assertEqual(set(document), probe.OUTPUT_KEYS)
        self.assertEqual(document["schema"], 1)
        self.assertEqual(document["run_nonce"], RUN_NONCE)
        self.assertEqual(document["invocation_nonce"], INVOCATION_NONCE)
        self.assertEqual(document["operation_id"], OPERATION_ID)
        self.assertEqual(document["arm"], arm)
        self.assertEqual(document["mode"], mode)
        self.assertEqual(document["result"], result)
        self.assertIsNone(document["enabled"])
        self.assertIsNone(document["clinician"])
        self.assertIsNone(document["baseline_digest"])
        self.assertIsNone(document["evidence_digest"])
        self.assertIsNone(document["counts"])
        self.assertFalse(document["clean"])
        return document

    def test_failure_output_is_bounded_and_redacted(self):
        private = self.baseline_private(documents())
        document = denial_document("status_denial", private)
        arguments = ["--site", SITE, "--operation-id", OPERATION_ID,
                     "--mode", "status_denial"]
        with native_runtime(documents(enabled=0)), \
                byte_proof(digest="0" * 64):
            code, output = invoke_main(arguments, raw(document))
        self.assertEqual(code, 3)
        self.assertTrue(output.endswith("\n"))
        self.assertEqual(output.count("\n"), 1)
        line = output.strip()
        self.assertLessEqual(len(line.encode("utf-8")), probe.MAX_RESPONSE_BYTES)
        response = json.loads(line)
        self.assertEqual(response["result"], "failed")
        self.assertFalse(response["clean"])
        self.assertIsNone(response["evidence_digest"])
        for private_value in (
            ACTOR, VERIFIER, SITE, PHOTO_NAME, FILE_NAME, SYNTHETIC_PATIENT,
            "Physician", "Nursing User", "/private/files/", "Traceback",
        ):
            self.assertNotIn(private_value, line)

        # Only pre-decode impossibility may emit the reduced 2-key line.
        for label, payload, arguments_for_case in (
            ("malformed json", b"{", arguments),
            (
                "oversize input",
                raw(document) + b" " * (probe.MAX_INPUT_BYTES + 1),
                arguments,
            ),
            (
                "non-allowlisted argv site",
                raw(document),
                ["--site", "other.local", "--operation-id", OPERATION_ID,
                 "--mode", "status_denial"],
            ),
            (
                "unknown argv mode",
                raw(document),
                ["--site", SITE, "--operation-id", OPERATION_ID,
                 "--mode", "unknown"],
            ),
        ):
            with self.subTest(label=label):
                code, output = invoke_main(arguments_for_case, payload)
                self.assertEqual(code, 2)
                self.assertEqual(output.count("\n"), 1)
                self.assertEqual(
                    json.loads(output.strip()), {"schema": 1, "result": "refused"}
                )
                self.assertNotIn(ACTOR, output)
                self.assertNotIn(SITE, output)

        # Post-decode refusals carry the exact 15-key bindings, not a reduced line.
        post_decode_cases = (
            (
                "argv operation mismatch",
                raw(document),
                ["--site", SITE, "--operation-id", "e" * 32,
                 "--mode", "status_denial"],
            ),
            (
                "argv mode mismatch",
                raw(document),
                ["--site", SITE, "--operation-id", OPERATION_ID,
                 "--mode", "verify"],
            ),
        )
        for label, payload, arguments_for_case in post_decode_cases:
            with self.subTest(label=label):
                code, output = invoke_main(arguments_for_case, payload)
                self.assertEqual(code, 2, output)
                self.assert_full_failure(output)

        # Native image inspection unavailability maps to unavailable, never refused.
        with mock.patch.object(
            probe.contracts,
            "validate_request",
            side_effect=validation.ImageInspectionUnavailable("unavailable"),
        ):
            code, output = invoke_main(
                ["--site", SITE, "--operation-id", OPERATION_ID,
                 "--mode", "replay_denial"],
                raw(input_document(mode="replay_denial")),
            )
        self.assertEqual(code, 4)
        self.assertEqual(
            json.loads(output.strip()), {"schema": 1, "result": "unavailable"}
        )

    def test_output_serialization_failure_falls_back_to_safety_document(self):
        """Coverage pin for MEDIUM-1's output-failure branch (not RED evidence)."""
        private = self.baseline_private(documents())
        document = denial_document("status_denial", private)
        arguments = ["--site", SITE, "--operation-id", OPERATION_ID,
                     "--mode", "status_denial"]
        real_bytes = probe._response_bytes
        calls = {"count": 0}

        def first_fails_then_real(response):
            calls["count"] += 1
            if calls["count"] == 1:
                return None
            return real_bytes(response)

        with native_runtime(documents(enabled=0)), byte_proof(), \
                mock.patch.object(
                    probe, "_response_bytes",
                    side_effect=first_fails_then_real,
                ):
            code, output = invoke_main(arguments, raw(document))
        self.assertEqual(code, 3, output)
        self.assert_full_failure(output, result="failed")
        self.assertGreaterEqual(calls["count"], 2)

    def test_private_baseline_descriptor_protocol(self):
        arguments = ["--site", SITE, "--operation-id", OPERATION_ID,
                     "--mode", "baseline"]
        document = baseline_document()

        # Required only for baseline/restored; the post-decode refusal keeps all 15
        # bindings so Task 3 can correlate it.
        code, output = invoke_main(arguments, raw(document))
        self.assertEqual(code, 2)
        self.assert_full_failure(output, mode="baseline")

        private = self.baseline_private(documents())
        denial = denial_document("status_denial", private)
        with tempfile.TemporaryFile() as descriptor:
            code, output = invoke_main(
                ["--site", SITE, "--operation-id", OPERATION_ID,
                 "--mode", "status_denial"],
                raw(denial),
                environment={probe.BASELINE_FD_ENV: str(descriptor.fileno())},
            )
        self.assertEqual(code, 2, output)
        self.assert_full_failure(output)

        for label, value in (
            ("non-decimal", "abc"),
            ("negative", "-1"),
            ("non-canonical leading zero", "012"),
            ("trailing environment value", "12x"),
            ("stdio descriptor", "0"),
            ("missing descriptor", "999999"),
        ):
            with self.subTest(label=label):
                with native_runtime(documents()), byte_proof(), \
                        mock.patch.object(
                            inspection, "resolve_operation",
                            return_value=saved_outcome(),
                        ):
                    code, output = invoke_main(
                        arguments, raw(document),
                        environment={probe.BASELINE_FD_ENV: value},
                    )
                self.assertEqual(code, 2, output)
                self.assert_full_failure(output, mode="baseline")

        # A writable regular descriptor receives the bounded private baseline, which
        # binds the returned public digest and carries no public artifact.
        with tempfile.TemporaryFile() as descriptor, \
                native_runtime(documents()), byte_proof(), \
                mock.patch.object(
                    inspection, "resolve_operation", return_value=saved_outcome()
                ):
            code, output = invoke_main(
                arguments, raw(document),
                environment={probe.BASELINE_FD_ENV: str(descriptor.fileno())},
            )
            descriptor.seek(0)
            payload = descriptor.read()
        self.assertEqual(code, 0, output)
        response = json.loads(output.strip())
        self.assertEqual(response["result"], "observed")
        self.assertGreater(len(payload), 0)
        self.assertLessEqual(len(payload), probe.MAX_BASELINE_BYTES)
        private_baseline = json.loads(payload)
        self.assertEqual(
            response["baseline_digest"], probe.baseline_digest(private_baseline)
        )

    def test_private_baseline_descriptor_security(self):
        arguments = ["--site", SITE, "--operation-id", OPERATION_ID,
                     "--mode", "baseline"]
        document = baseline_document()
        private = self.baseline_private(documents())
        verify = decoded(mode="verify", baseline=private, request=None)

        # Verify mode must also reject a supplied private descriptor.
        with tempfile.TemporaryFile() as descriptor:
            code, output = invoke_main(
                ["--site", SITE, "--operation-id", OPERATION_ID, "--mode", "verify"],
                raw(verify),
                environment={probe.BASELINE_FD_ENV: str(descriptor.fileno())},
            )
        self.assertEqual(code, 2, output)
        self.assert_full_failure(output, mode="verify")

        # The descriptor itself must be an empty, offset-0, owner-only 0600 regular
        # file opened writable. Each rejection happens before native work.
        with tempfile.NamedTemporaryFile() as holder:
            holder.write(b"stale")
            holder.flush()
            with self.subTest(label="stale content"):
                holder.seek(0)
                code, output = invoke_main(
                    arguments, raw(document),
                    environment={probe.BASELINE_FD_ENV: str(holder.fileno())},
                )
                self.assertEqual(code, 2, output)
                self.assert_full_failure(output, mode="baseline")

            with self.subTest(label="nonzero offset"):
                holder.seek(0)
                os.ftruncate(holder.fileno(), 0)
                os.lseek(holder.fileno(), 4, os.SEEK_SET)
                code, output = invoke_main(
                    arguments, raw(document),
                    environment={probe.BASELINE_FD_ENV: str(holder.fileno())},
                )
                self.assertEqual(code, 2, output)
                self.assert_full_failure(output, mode="baseline")

            with self.subTest(label="group/other permissions"):
                os.chmod(holder.name, 0o644)
                os.lseek(holder.fileno(), 0, os.SEEK_SET)
                code, output = invoke_main(
                    arguments, raw(document),
                    environment={probe.BASELINE_FD_ENV: str(holder.fileno())},
                )
                self.assertEqual(code, 2, output)
                self.assert_full_failure(output, mode="baseline")

            with self.subTest(label="read-only descriptor"):
                os.chmod(holder.name, 0o600)
                read_only = os.open(holder.name, os.O_RDONLY)
                try:
                    code, output = invoke_main(
                        arguments, raw(document),
                        environment={probe.BASELINE_FD_ENV: str(read_only)},
                    )
                finally:
                    os.close(read_only)
                self.assertEqual(code, 2, output)
                self.assert_full_failure(output, mode="baseline")

        # A pipe is not the anonymous regular-file descriptor the protocol requires.
        read_fd, write_fd = os.pipe()
        try:
            code, output = invoke_main(
                arguments, raw(document),
                environment={probe.BASELINE_FD_ENV: str(write_fd)},
            )
        finally:
            os.close(read_fd)
            os.close(write_fd)
        self.assertEqual(code, 2, output)
        self.assert_full_failure(output, mode="baseline")

    def test_oversized_private_baseline_is_refused_without_credit(self):
        long_roles = (
            {"role": "Physician", "parenttype": "User", "parentfield": "roles"},
        ) + tuple(
            {
                "role": f"Unrelated-{index:04d}-" + "x" * 100,
                "parenttype": "User",
                "parentfield": "roles",
            }
            for index in range(600)
        )
        docs = documents(actor_roles=long_roles)
        arguments = ["--site", SITE, "--operation-id", OPERATION_ID,
                     "--mode", "baseline"]
        document = baseline_document()

        with tempfile.TemporaryFile() as descriptor, native_runtime(docs), \
                byte_proof(), mock.patch.object(
                    inspection, "resolve_operation", return_value=saved_outcome()
                ):
            code, output = invoke_main(
                arguments, raw(document),
                environment={probe.BASELINE_FD_ENV: str(descriptor.fileno())},
            )
            descriptor.seek(0)
            payload = descriptor.read()
        self.assertEqual(code, 3, output)
        self.assert_full_failure(output, mode="baseline", result="failed")
        self.assertEqual(payload, b"")


if __name__ == "__main__":
    unittest.main()
