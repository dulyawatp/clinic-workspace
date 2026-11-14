"""Declared isolated-case runner for the Frappe integration harness.

This runner does **not** claim any native result. On this host there is no bench and
no site, so it implements and validates only:

* loading and validating ``case_manifest.json`` (complete, unique case ids and
  per-case ``(module, class, method)`` identities, denial semantics separated from
  abort-latch semantics, and an explicit ``executable``/``blocked`` readiness),
* generating one ``bench run-tests`` command per *executable* case (``--case`` class
  plus a bare ``--test`` method, with the case's own module/class),
* verifying per-case provenance from the raw execution output,
* deriving acceptance strictly from that evidence and from a versioned, resource
  complete disposal confirmation.

Per-case identity: a manifest case may declare its own ``module``/``case_class``.
Cases that declare neither inherit the manifest-level pair, so a schema-1 manifest
keeps working unchanged. A ``blocked`` case is never selectable, is never required by
the acceptance matrix, and can never be reported as a pass; it must carry the exact
``blocked_reason`` that names its missing prerequisite.

Provenance, not labels: the selected test method itself emits (through a decorator)
a BEGIN/END pair carrying unittest's own ``self.id()`` and the per-invocation nonce
the runner passes in ``CLINIC_PHOTOS_CASE_NONCE``. Those markers are written to the
original stdout (``emit_marker``), because frappe's buffered test result swaps
``sys.stdout`` for a capture it never flushes, which would drop teardown evidence.
The runner requires exactly one summary, a terminal ``OK``, zero skips, and markers
whose identity equals the expected module/class/method and whose nonce equals this
invocation. A caller-supplied ``status`` field is never trusted: the status is
always re-derived from ``exit_code`` + raw ``output``, and a stored status that
contradicts the evidence is reported as a failure.

Execution is fail-closed twice over:

1. the bench CLI contract below is a **candidate**; ``--execute`` refuses unless a
   verification record proves the target bench accepts ``--app``/``--module``/
   ``--case``/``--test`` for the same contract digest;
2. ``--execute`` is the explicit authorization step and needs ``--site``.

Acceptance additionally needs a versioned cleanup disposition. Protected rows/bytes
are never deleted under a missing or poisoned owner (that would be an ordinary
immutable delete, and the abort latch is sticky). Instead the test harness emits a
residue marker listing exactly what it created, and this runner requires a
``--cleanup-confirmed`` record that binds the run (run id, site, project, source and
contract digests, per-case nonces and raw-output hashes), the residue (rows **and**
protected storage keys) and an **observed** disposal covering every listed resource.
A failing case still surrenders its residue: the verdict stays failed and the cleanup
obligation is kept. Empty rows are not evidence of filesystem cleanliness, so an empty
residue set still needs the observed disposal record. Without it the matrix is not
accepted, so leaked bytes are never silently accepted either.

Exit codes: ``0`` accepted, ``1`` acceptance failure, ``2``
manifest/contract/configuration error (nothing executed or accepted).
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import pathlib
import re
import secrets
import subprocess
import sys
import types

from clinic_photos.access.storage import canonical_private_key

HERE = pathlib.Path(__file__).resolve().parent
MANIFEST_PATH = HERE / "case_manifest.json"
APP_ROOT = HERE.parents[1]

BENCH = "bench"
RUN_TESTS = "run-tests"

# One source of truth for the evidence protocol; the integration harness imports
# these names so the producer and the consumer cannot drift.
CASE_NONCE_ENV = "CLINIC_PHOTOS_CASE_NONCE"
BEGIN_MARKER = "CLINIC_PHOTOS_CASE_BEGIN"
END_MARKER = "CLINIC_PHOTOS_CASE_END"
RESIDUE_MARKER = "CLINIC_PHOTOS_CASE_RESIDUE"

# Versioned evidence/disposal schema. Bump it when the binding changes; an older
# confirmation or run record is then rejected instead of being reinterpreted.
#
# Schema 3 (Task 10 fix round 1): the source identity is recomputed by the consumer
# against an explicitly supplied trusted root over a declared input set, the run record
# is version- and contract-checked and must be a bijection with the executable cases and
# the supplied results, acceptance distinguishes executable-subset success from complete
# required native acceptance, the run records its own ``recorded_at``/``source_root``, and
# the disposal time must not predate the run.
EVIDENCE_SCHEMA = 3

# Declared source-input set for the source identity. Scope, stated plainly: this is the
# *source revision* of the checkout the consumer is pointed at. It is NOT installed
# schema, mounted-copy, runtime-config or remote-deployment proof, and no cryptographic
# attestation is implied.
SOURCE_INPUT_GLOBS = ("clinic_photos/**/*.py", "clinic_photos/**/doctype/*/*.json")
SOURCE_INPUT_FILES = ("clinic_photos/modules.txt", "clinic_photos/patches.txt")

# The required publication requirement, frozen: each id together with the exact
# ``(module, case_class, method)`` identity it must still resolve to. This mapping — not
# the manifest's module labels, not the identities the caller happens to supply and not
# the id alone — defines what publication acceptance owes. Deleting a required case,
# re-pointing its module, renaming its class or moving the id onto another executable
# test leaves the obligation in place, because the id is bound to its own test identity.
# The mapping is read-only, derived nowhere and has no runtime override.
REQUIRED_PUBLICATION_CASES = types.MappingProxyType(
    {
        "pub.process.unknown_operation_refused": (
            "clinic_photos.tests.test_publication",
            "PublicationProcessEntryCases",
            "test_child_refuses_unknown_operation_without_residue",
        ),
        "pub.process.site_binding_enforced": (
            "clinic_photos.tests.test_publication",
            "PublicationProcessEntryCases",
            "test_child_binds_the_approved_site_and_ignores_no_other",
        ),
        "pub.process.argv_shape_fixed": (
            "clinic_photos.tests.test_publication",
            "PublicationProcessEntryCases",
            "test_child_rejects_client_supplied_argv_shapes",
        ),
        "pub.process.output_bounded_no_saved_claim": (
            "clinic_photos.tests.test_publication",
            "PublicationProcessEntryCases",
            "test_child_output_is_phifree_and_never_claims_saved",
        ),
        "pub.schema.reservation_null_and_unique": (
            "clinic_photos.tests.test_publication",
            "PublicationSchemaNativeCases",
            "test_reservation_null_semantics_and_unique_index",
        ),
        "pub.schema.set_value_no_hooks_no_commit": (
            "clinic_photos.tests.test_publication",
            "PublicationSchemaNativeCases",
            "test_set_value_has_no_hooks_and_no_commit",
        ),
        "pub.original.first_jpeg_mro_and_get_content": (
            "clinic_photos.tests.test_publication",
            "PublicationOriginalNativeCases",
            "test_first_jpeg_original_uses_protected_mro_and_get_content",
        ),
        "pub.original.hooks_and_rollback_residue": (
            "clinic_photos.tests.test_publication",
            "PublicationOriginalNativeCases",
            "test_native_hooks_and_rollback_residue_observed",
        ),
        "pub.reservation.survives_rollback": (
            "clinic_photos.tests.test_publication",
            "PublicationReservationNativeCases",
            "test_prepared_reservation_survives_rollback",
        ),
        "pub.reservation.retarget_reserved_refused": (
            "clinic_photos.tests.test_publication",
            "PublicationReservationNativeCases",
            "test_dedup_retarget_reserved_refused",
        ),
        "pub.reservation.retarget_registered_refused": (
            "clinic_photos.tests.test_publication",
            "PublicationReservationNativeCases",
            "test_dedup_retarget_registered_refused",
        ),
        "pub.reservation.post_insert_retarget_requires_rollback": (
            "clinic_photos.tests.test_publication",
            "PublicationReservationNativeCases",
            "test_post_insert_retarget_requires_rollback",
        ),
        "pub.reservation.original_path_rollback_guarded": (
            "clinic_photos.tests.test_publication",
            "PublicationReservationNativeCases",
            "test_original_path_rollback_guarded",
        ),
        "pub.reservation.unsaved_file_rollback_guarded": (
            "clinic_photos.tests.test_publication",
            "PublicationReservationNativeCases",
            "test_unsaved_file_rollback_guarded",
        ),
        "pub.reservation.administrator_delete_refused": (
            "clinic_photos.tests.test_publication",
            "PublicationReservationNativeCases",
            "test_administrator_delete_refused",
        ),
        "pub.atomicity.two_phase_atomic": (
            "clinic_photos.tests.test_publication",
            "PublicationAtomicityNativeCases",
            "test_prepared_commit_then_publication_is_atomic",
        ),
        "pub.atomicity.same_owner_after_commit": (
            "clinic_photos.tests.test_publication",
            "PublicationAtomicityNativeCases",
            "test_same_owner_rechecks_after_commit",
        ),
        "pub.atomicity.revocation_between_phases": (
            "clinic_photos.tests.test_publication",
            "PublicationAtomicityNativeCases",
            "test_revocation_between_phases_blocks_publication",
        ),
        "pub.atomicity.unknown_commit_not_retried": (
            "clinic_photos.tests.test_publication",
            "PublicationAtomicityNativeCases",
            "test_unknown_commit_outcome_is_not_retried",
        ),
        "pub.atomicity.duplicate_launch_no_resume": (
            "clinic_photos.tests.test_publication",
            "PublicationAtomicityNativeCases",
            "test_duplicate_launch_does_not_resume_prepared",
        ),
        "pub.access.replay_after_newer_version": (
            "clinic_photos.tests.test_publication",
            "PublicationAccessNativeCases",
            "test_replay_after_newer_version_returns_immutable_version",
        ),
        "pub.access.lost_response_replay": (
            "clinic_photos.tests.test_publication",
            "PublicationAccessNativeCases",
            "test_lost_response_replay_resolves_original_tuple",
        ),
        "pub.access.allowlist_cross_actor_audit": (
            "clinic_photos.tests.test_publication",
            "PublicationAccessNativeCases",
            "test_operator_allowlist_cross_actor_audit_is_denied",
        ),
        "pub.access.disabled_or_revoked_actor": (
            "clinic_photos.tests.test_publication",
            "PublicationAccessNativeCases",
            "test_disabled_or_revoked_actor_cannot_replay",
        ),
        "pub.access.native_file_veto": (
            "clinic_photos.tests.test_publication",
            "PublicationAccessNativeCases",
            "test_native_file_veto_denies_replay",
        ),
    }
)

# Derived compatibility view of the same requirement. It is not a second inventory: it
# is computed from the mapping above, so the ids and the identities cannot drift.
REQUIRED_PUBLICATION_CASE_IDS = frozenset(REQUIRED_PUBLICATION_CASES)


READINESS_EXECUTABLE = "executable"
READINESS_BLOCKED = "blocked"
READINESS_MODES = (READINESS_EXECUTABLE, READINESS_BLOCKED)

NATIVE_UNRUN = "UNRUN"


def emit_marker(marker: str, payload: dict) -> None:
    """Write one evidence marker to the process's original stdout.

    Frappe's buffered test result replaces ``sys.stdout`` with a capture buffer and
    never flushes the tail, so class-teardown evidence written through the current
    stdout is discarded. Markers therefore go to ``sys.__stdout__``, resolved at call
    time; the capture is never used as a fallback. A missing, closed, unwritable or
    failing stream raises instead of dropping evidence, because a silently absent
    marker would be rejected later as missing cleanup proof.

    The line is newline-framed so a preceding progress line cannot swallow the marker
    boundary, and carries the existing canonical JSON payload.
    """
    stream = sys.__stdout__
    if stream is None or getattr(stream, "closed", True):
        raise RuntimeError(
            "marker channel unavailable: the original stdout is missing or closed"
        )
    writable = getattr(stream, "writable", None)
    if writable is None or not writable():
        raise RuntimeError(
            "marker channel unavailable: the original stdout is not writable"
        )
    line = f"\n{marker} {json.dumps(payload, sort_keys=True)}\n"
    try:
        stream.write(line)
        stream.flush()
    except (OSError, ValueError) as error:
        raise RuntimeError(f"marker channel failed: {error}") from error

# Candidate contract. The digest must match the verification record exactly, so a
# changed command shape invalidates an old verification instead of silently reusing
# it. ``--case``/``--test`` support is NOT taken for granted: an unverified contract
# cannot run. Installed frappe 16.53.0 discovery compares ``--test`` against
# ``test._testMethodName``, so the method is always bare and the class is selected
# separately; ``--test Class.method`` matches nothing and the CLI still exits 0.
CLI_CONTRACT = {
    "status": "unverified-candidate",
    "template": [
        "bench",
        "--site",
        "{site}",
        "run-tests",
        "--app",
        "{app}",
        "--module",
        "{module}",
        "--case",
        "{case_class}",
        "--test",
        "{method}",
    ],
    "verification_requirement": (
        "On the target bench, record `bench --site <site> run-tests --help` and confirm "
        "the options --app, --module, --case and --test are supported. The selector must "
        "be `--case <Class> --test <bare_method>` because discovery matches --test "
        "against unittest's _testMethodName. Then run one case, capture the raw stdout, "
        "and confirm it shows exactly one `Ran 1 test` summary selecting that case, a "
        "terminal OK, and the CLINIC_PHOTOS_CASE_* marker lines, so a zero exit with "
        "nothing executed is visible as no output. Write a record with the same "
        "contract_digest (sha256 of the template above) plus that evidence. Verbose "
        "mode is not assumed: provenance comes from the harness markers, not from -v."
    ),
}

_RAN = re.compile(r"^Ran (\d+) tests? in ", re.MULTILINE)
_BEGIN = re.compile(r"^" + BEGIN_MARKER + r" (\{.*\})$", re.MULTILINE)
_END = re.compile(r"^" + END_MARKER + r" (\{.*\})$", re.MULTILINE)
_RESIDUE = re.compile(r"^" + RESIDUE_MARKER + r" (\{.*\})$", re.MULTILINE)
_SKIP = re.compile(r"\bskipped\b", re.IGNORECASE)

REQUIRED_CASE_KEYS = (
    "id",
    "method",
    "owner",
    "expects_denial",
    "latches_abort",
    "expected_exception",
    "expected_owner_reasons",
    "roles",
    "guard",
    "description",
)
OPTIONAL_CASE_KEYS = ("module", "case_class", "readiness", "blocked_reason")
OWNER_MODES = ("fresh", "none")
EXCEPTIONS = ("PermissionError", "ValidationError", None)


def contract_digest(contract=None) -> str:
    template = (contract or CLI_CONTRACT)["template"]
    return hashlib.sha256(json.dumps(template).encode("utf-8")).hexdigest()


def load_manifest(path=None) -> dict:
    manifest_path = pathlib.Path(path) if path else MANIFEST_PATH
    with open(manifest_path, encoding="utf-8") as handle:
        return json.load(handle)


def case_module(manifest: dict, case: dict) -> str:
    """Per-case module override, falling back to the manifest-level module."""
    return case.get("module") or manifest["module"]


def case_class(manifest: dict, case: dict) -> str:
    """Per-case class override, falling back to the manifest-level class."""
    return case.get("case_class") or manifest["case_class"]


def case_readiness(case: dict) -> str:
    return case.get("readiness") or READINESS_EXECUTABLE


def executable_cases(manifest: dict) -> list:
    return [
        case
        for case in manifest.get("cases") or []
        if case_readiness(case) == READINESS_EXECUTABLE
    ]


def blocked_cases(manifest: dict) -> list:
    return [
        case
        for case in manifest.get("cases") or []
        if case_readiness(case) == READINESS_BLOCKED
    ]


def expected_case_id(manifest: dict, case: dict) -> str:
    """The unittest id the selected method must report as its own identity."""
    return f"{case_module(manifest, case)}.{case_class(manifest, case)}.{case['method']}"


def validate_manifest(manifest: dict) -> list:
    """Structural validation; errors mean the matrix cannot be trusted."""
    problems = []
    for key in ("schema", "app", "module", "case_class", "cases"):
        if not manifest.get(key) and manifest.get(key) != 0:
            problems.append(f"manifest is missing {key!r}")
    cases = manifest.get("cases") or []
    if not isinstance(cases, list) or not cases:
        problems.append("manifest has no cases")
        return problems

    seen_ids, seen_identities = set(), set()
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            problems.append(f"case #{index} is not an object")
            continue
        missing = [key for key in REQUIRED_CASE_KEYS if key not in case]
        if missing:
            problems.append(f"case #{index} missing keys: {', '.join(missing)}")
            continue
        unknown = [
            key
            for key in case
            if key not in REQUIRED_CASE_KEYS + OPTIONAL_CASE_KEYS
        ]
        if unknown:
            problems.append(
                f"{case['id']}: unknown case keys: {', '.join(sorted(unknown))}"
            )
        for key in ("module", "case_class"):
            if key in case and (
                not isinstance(case[key], str) or not case[key].strip()
            ):
                problems.append(f"{case['id']}: {key} must be a non-empty string")
        readiness = case.get("readiness", READINESS_EXECUTABLE)
        if readiness not in READINESS_MODES:
            problems.append(
                f"{case['id']}: readiness must be one of {READINESS_MODES}"
            )
        if readiness == READINESS_BLOCKED:
            reason = case.get("blocked_reason")
            if not isinstance(reason, str) or not reason.strip():
                problems.append(
                    f"{case['id']}: a blocked case must name its blocked_reason"
                )
        elif case.get("blocked_reason"):
            problems.append(
                f"{case['id']}: an executable case must not carry blocked_reason"
            )
        if case["owner"] not in OWNER_MODES:
            problems.append(f"{case['id']}: owner must be one of {OWNER_MODES}")
        if case["expected_exception"] not in EXCEPTIONS:
            problems.append(f"{case['id']}: unexpected expected_exception")
        if not isinstance(case["expects_denial"], bool):
            problems.append(f"{case['id']}: expects_denial must be a boolean")
        if not isinstance(case["latches_abort"], bool):
            problems.append(f"{case['id']}: latches_abort must be a boolean")
        if not isinstance(case["roles"], list) or not case["roles"]:
            problems.append(f"{case['id']}: roles must be a non-empty list")
        if case["latches_abort"] and not case["expects_denial"]:
            problems.append(
                f"{case['id']}: a case cannot latch an abort without a denial"
            )
        # Denial semantics and latch semantics are separate on purpose: a guard may
        # deny before entering any publication boundary, in which case the owner is
        # not notified and nothing is latched.
        if not case["expects_denial"]:
            if case["expected_exception"] is not None:
                problems.append(
                    f"{case['id']}: a non-denial case must not expect an exception"
                )
            if case["latches_abort"]:
                problems.append(f"{case['id']}: a non-denial case cannot latch")
        elif case["expected_exception"] is None:
            problems.append(f"{case['id']}: a denial case must expect an exception")
        if case["owner"] == "none":
            if case["expected_owner_reasons"] is not None:
                problems.append(
                    f"{case['id']}: a no-owner case must not expect owner notifications"
                )
        elif not isinstance(case["expected_owner_reasons"], list):
            problems.append(
                f"{case['id']}: an owner case must list the expected abort reasons"
            )
        elif case["latches_abort"] and not case["expected_owner_reasons"]:
            problems.append(
                f"{case['id']}: a latched denial must name the notifying boundary"
            )
        elif not case["latches_abort"] and case["expected_owner_reasons"]:
            problems.append(
                f"{case['id']}: an unlatched denial must expect no notification"
            )
        if case["id"] in seen_ids:
            problems.append(f"duplicate case id: {case['id']}")
        seen_ids.add(case["id"])
        identity = (
            case_module(manifest, case),
            case_class(manifest, case),
            case["method"],
        )
        if identity in seen_identities:
            problems.append(
                "duplicate case identity: " + ".".join(identity)
            )
        seen_identities.add(identity)
    return problems


def build_command(manifest: dict, case: dict, site: str, contract=None) -> list:
    values = {
        "site": site,
        "app": manifest["app"],
        "module": case_module(manifest, case),
        "case_class": case_class(manifest, case),
        "method": case["method"],
    }
    template = (contract or CLI_CONTRACT)["template"]
    return [part.format(**values) for part in template]


def _coverage(manifest: dict, verified=None) -> dict:
    """Coverage of the frozen required publication inventory plus the whole matrix.

    The obligation comes from :data:`REQUIRED_PUBLICATION_CASES` — each required id
    together with the exact ``(module, case_class, method)`` identity it must resolve to
    — never from the manifest's module labels and never from whatever cases the caller
    supplied. Deleting a required case, re-pointing its module or moving its id onto
    another test therefore cannot remove the obligation: a required id whose supplied
    identity differs is reported as *rebound*, withholds ``publication_complete`` and is
    never credited as verified publication coverage.
    ``verified`` is the set of case ids whose supplied evidence passed. ``None`` means
    "nothing has executed" (dry run), so readiness is reported and nothing counts as
    verified: a dry run is readiness evidence, not acceptance.
    """
    all_cases = {case["id"]: case for case in manifest.get("cases") or []}
    missing = sorted(REQUIRED_PUBLICATION_CASE_IDS - set(all_cases))
    required = [
        all_cases[case_id]
        for case_id in sorted(REQUIRED_PUBLICATION_CASE_IDS)
        if case_id in all_cases
    ]
    rebound = [
        case["id"]
        for case in required
        if (
            case_module(manifest, case),
            case_class(manifest, case),
            case["method"],
        )
        != REQUIRED_PUBLICATION_CASES[case["id"]]
    ]
    blocked_required = [
        case["id"]
        for case in required
        if case_readiness(case) == READINESS_BLOCKED
    ]
    executable_required = [
        case["id"]
        for case in required
        if case_readiness(case) != READINESS_BLOCKED
    ]
    passed = set(verified or ())
    verified_required = [
        case_id
        for case_id in executable_required
        if case_id in passed and case_id not in rebound
    ]
    unverified = [
        case_id
        for case_id in executable_required
        if case_id not in passed and case_id not in rebound
    ]

    blocked = blocked_cases(manifest)
    by_module = {}
    for case in blocked:
        module = case_module(manifest, case)
        by_module[module] = by_module.get(module, 0) + 1
    return {
        "required_publication": len(REQUIRED_PUBLICATION_CASES),
        "executable": len(executable_cases(manifest)),
        "blocked_unrun": len(blocked),
        "blocked_by_module": by_module,
        "publication_required_missing": len(missing),
        "publication_required_missing_ids": missing,
        "publication_required_rebound": len(rebound),
        "publication_required_rebound_ids": rebound,
        "publication_required_blocked": len(blocked_required),
        "publication_required_blocked_ids": blocked_required,
        "publication_required_executable": len(executable_required),
        "publication_required_verified": len(verified_required),
        "publication_required_unverified": len(unverified),
        "publication_required_unverified_ids": unverified,
        "publication_complete": (
            not missing
            and not rebound
            and not blocked_required
            and not unverified
        ),
    }


def build_dry_run(manifest: dict, site: str) -> dict:
    rows = []
    for case in manifest["cases"]:
        blocked = case_readiness(case) == READINESS_BLOCKED
        rows.append(
            {
                "case": case["id"],
                "expected_id": expected_case_id(manifest, case),
                "module": case_module(manifest, case),
                "case_class": case_class(manifest, case),
                "readiness": case_readiness(case),
                "native_status": NATIVE_UNRUN,
                "blocked_reason": case.get("blocked_reason"),
                "nonce_env": CASE_NONCE_ENV,
                # A blocked case is not selectable: no command is fabricated.
                "command": None if blocked else build_command(manifest, case, site),
            }
        )
    coverage = _coverage(manifest)
    return {
        "dry_run": True,
        "executed": False,
        "native_status": NATIVE_UNRUN,
        "accepted": False,
        "executable_subset_accepted": False,
        "acceptance_scope": "executable_subset_only",
        "publication_complete": coverage["publication_complete"],
        "matrix_complete": False,
        "readiness_only": True,
        "coverage": coverage,
        "evidence_schema": EVIDENCE_SCHEMA,
        "site": site,
        "app": manifest["app"],
        "executable_cases": coverage["executable"],
        "blocked_cases": coverage["blocked_unrun"],
        "cli_contract": {
            "status": CLI_CONTRACT["status"],
            "template": CLI_CONTRACT["template"],
            "digest": contract_digest(),
            "verification_requirement": CLI_CONTRACT["verification_requirement"],
        },
        "cases": rows,
    }


def _json_markers(pattern, output):
    markers = []
    for match in pattern.finditer(output or ""):
        try:
            markers.append(json.loads(match.group(1)))
        except ValueError:
            markers.append({"__unparsable__": match.group(1)})
    return markers


def _terminal_line(output: str) -> str:
    for line in reversed((output or "").splitlines()):
        if line.strip():
            return line.strip()
    return ""


def output_digest(output: str) -> str:
    """Hash of the raw per-case output the derived status is bound to."""
    return hashlib.sha256((output or "").encode("utf-8")).hexdigest()


def canonical_rows(rows) -> list:
    """Sorted, de-duplicated resource identities as ``[doctype, name]`` pairs.

    A row that is not a two-element pair of non-empty strings carries no resource
    identity, so a residue list containing one is not usable cleanup evidence.
    """
    canonical = set()
    for row in rows or []:
        if not isinstance(row, (list, tuple)) or len(row) != 2:
            raise ValueError("residue row is not a [doctype, name] identity pair")
        doctype, name = row
        if not isinstance(doctype, str) or not isinstance(name, str):
            raise ValueError("residue row identity must be text")
        if not doctype.strip() or not name.strip():
            raise ValueError("residue row identity is empty")
        canonical.add((doctype, name))
    return [[doctype, name] for doctype, name in sorted(canonical)]


def canonical_storage_keys(keys) -> tuple:
    """Sorted, de-duplicated protected storage keys.

    Every key must be the canonical private path the native layer uses; anything else
    is an unverifiable identity and cannot appear in a disposal record.
    """
    cleaned = set()
    for key in keys or []:
        if not isinstance(key, str):
            raise ValueError("storage key must be text")
        canonical_private_key(key)
        cleaned.add(key)
    return tuple(sorted(cleaned))


def _collect_orphan_residue(result: dict, output: str, nonce: str) -> None:
    """Capture a same-invocation residue marker whose case identity is unproven.

    When the case markers are absent or name the wrong case, the case cannot be
    credited, but a residue marker still proves the execution created something. It is
    reported separately (``orphan_residue``/``orphan_storage_keys``) so the disposal
    list under-reports nothing, and it is never used to pass a case.
    """
    residues = _json_markers(_RESIDUE, output)
    if len(residues) != 1 or residues[0].get("nonce") != nonce:
        return
    try:
        result["orphan_residue"] = canonical_rows(residues[0].get("rows") or [])
        result["orphan_storage_keys"] = list(
            canonical_storage_keys(residues[0].get("storage_keys") or [])
        )
    except (TypeError, ValueError) as error:
        result["orphan_residue"] = [["<unusable-orphan-residue>", str(error)]]
        result["orphan_storage_keys"] = []


def parse_case_output(exit_code: int, output: str, *, expected_id: str, nonce: str) -> dict:
    """Derive one case's status from raw output; never from a supplied label.

    Required evidence, in order: exactly one BEGIN and one END marker whose identity
    is the expected ``module.Class.method`` and whose nonce is this invocation's, then
    exactly one unittest summary selecting exactly one test, no skip, a zero exit, and
    the exact terminal ``OK`` outcome. A residue marker with the same nonce and the
    same case class is required as cleanup evidence.

    The residue is read *before* the verdict is decided and is kept even when the
    verdict is ``failed``: a failing case still created whatever it created, and
    dropping that would silently drop the cleanup obligation (and would let a
    partial-digest disposal record pass). Empty residue is recorded as empty but is
    never treated as proof that nothing exists on the filesystem.
    """
    output = output or ""
    result = {
        "status": "unknown",
        "detail": "",
        "identity": None,
        "residue": None,
        "storage_keys": [],
        "orphan_residue": [],
        "orphan_storage_keys": [],
        "raw_output_sha256": output_digest(output),
    }

    begins = _json_markers(_BEGIN, output)
    ends = _json_markers(_END, output)
    if len(begins) != 1 or len(ends) != 1:
        result["status"] = "no-evidence"
        result["detail"] = (
            f"expected exactly one case marker pair, found {len(begins)} begin / "
            f"{len(ends)} end"
        )
        _collect_orphan_residue(result, output, nonce)
        return result

    identity = begins[0].get("id")
    result["identity"] = identity
    if identity != expected_id or ends[0].get("id") != expected_id:
        result["status"] = "wrong-identity"
        result["detail"] = f"executed {identity!r}, expected {expected_id!r}"
        _collect_orphan_residue(result, output, nonce)
        return result
    if begins[0].get("nonce") != nonce or ends[0].get("nonce") != nonce:
        result["status"] = "wrong-identity"
        result["detail"] = "case evidence was not produced by this invocation"
        _collect_orphan_residue(result, output, nonce)
        return result

    # Residue first, so a failed verdict cannot discard it.
    expected_class_id = expected_id.rsplit(".", 1)[0]
    verdict = (
        "failed" if ends[0].get("status") != "pass" else "pass"
    )
    residues = _json_markers(_RESIDUE, output)
    residue_problem = None
    if len(residues) != 1:
        residue_problem = (
            f"expected exactly one residue marker, found {len(residues)}; leaked "
            "fixtures would be silently accepted otherwise"
        )
    elif residues[0].get("id") != expected_class_id or residues[0].get("nonce") != nonce:
        residue_problem = (
            "residue evidence was not produced by this invocation and its case class"
        )
    else:
        residue = residues[0]
        rows = residue.get("rows")
        if not isinstance(rows, list):
            residue_problem = "residue marker has no row list"
        else:
            try:
                result["residue"] = canonical_rows(rows)
                result["storage_keys"] = list(
                    canonical_storage_keys(residue.get("storage_keys") or [])
                )
            except (TypeError, ValueError) as error:
                result["residue"] = None
                result["storage_keys"] = []
                residue_problem = f"residue evidence is unusable: {error}"

    if residue_problem:
        # Unusable cleanup evidence is a hard failure of the evidence contract; it is
        # never downgraded to the case verdict, in either direction.
        result["status"] = "no-cleanup-evidence"
        result["detail"] = residue_problem
        if verdict != "pass":
            result["detail"] += f" (case also reported {ends[0].get('status')!r})"
        return result
    if verdict != "pass":
        result["status"] = "failed"
        result["detail"] = f"case reported {ends[0].get('status')!r}"
        return result

    summaries = _RAN.findall(output)
    if not summaries:
        result["status"] = "missing"
        result["detail"] = "no unittest result line; the case produced no result"
        return result
    if len(summaries) != 1:
        result["status"] = "multiple-summaries"
        result["detail"] = f"expected exactly one summary, found {len(summaries)}"
        return result
    count = int(summaries[0])
    if count != 1:
        # More than one test ran: selection was not case-isolated. Zero means the
        # case was not selected at all.
        result["status"] = "wrong-cardinality"
        result["detail"] = f"expected exactly 1 selected test, got {count}"
        return result

    if _SKIP.search(output):
        result["status"] = "skipped"
        result["detail"] = "case skipped; required coverage is not accepted as green"
        return result
    if int(exit_code) != 0:
        result["status"] = "failed"
        result["detail"] = f"exit code {exit_code}"
        return result
    if "FAILED" in output or "ERROR:" in output:
        result["status"] = "failed"
        result["detail"] = "failure token present in the output"
        return result
    if _terminal_line(output) != "OK":
        # A stray "OK" inside a log line must not be mistaken for the summary.
        result["status"] = "unknown"
        result["detail"] = (
            f"terminal outcome is {_terminal_line(output)!r}, expected 'OK'"
        )
        return result

    result["status"] = "passed"
    result["detail"] = "one isolated case passed with matching evidence"
    return result


def canonical_residue(rows, keys=()) -> dict:
    """Canonical residue identity: rows **and** protected storage keys."""
    return {
        "rows": canonical_rows(rows),
        "storage_keys": list(canonical_storage_keys(keys)),
    }


def residue_digest(rows, keys=()) -> str:
    """Digest of the whole residue identity, not the row list alone."""
    return hashlib.sha256(
        json.dumps(canonical_residue(rows, keys), sort_keys=True).encode("utf-8")
    ).hexdigest()


def resolve_source_root(value):
    """Resolve a caller-supplied trusted source root, or refuse.

    The root is supplied by the operator/direct caller; it is never taken from the run
    record it is meant to validate.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ValueError(
            "a trusted source root is required; the run record cannot supply it"
        )
    root = pathlib.Path(value).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"trusted source root is not a directory: {root}")
    if not (root / "clinic_photos").is_dir():
        raise ValueError(
            f"trusted source root does not contain the app package: {root}"
        )
    return root


def source_inputs(root, manifest_path=None) -> list:
    """Deterministic declared source-input set for the source identity.

    Scope: the app package's Python modules, every app DocType JSON definition, the
    applicable ``modules.txt``/``patches.txt`` and the exact case manifest in use. This
    binds *source revision* only; it is not installed schema, mounted-copy,
    runtime-config or remote-deployment evidence.
    """
    root = resolve_source_root(root)
    entries = []
    seen = set()
    for pattern in SOURCE_INPUT_GLOBS:
        for path in sorted(root.glob(pattern)):
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            if relative in seen:
                continue
            seen.add(relative)
            entries.append([relative, hashlib.sha256(path.read_bytes()).hexdigest()])
    for relative in SOURCE_INPUT_FILES:
        path = root / relative
        if path.is_file() and relative not in seen:
            seen.add(relative)
            entries.append([relative, hashlib.sha256(path.read_bytes()).hexdigest()])

    manifest = pathlib.Path(manifest_path or MANIFEST_PATH).expanduser().resolve()
    if not manifest.is_file():
        raise ValueError(f"case manifest not found: {manifest}")
    manifest_relative = None
    try:
        manifest_relative = manifest.relative_to(root).as_posix()
    except ValueError:
        manifest_relative = None
    manifest_hash = hashlib.sha256(manifest.read_bytes()).hexdigest()
    if manifest_relative is None:
        # Supplied outside the package tree: still bind its exact bytes.
        entries.append(["manifest::" + manifest.name, manifest_hash])
    elif manifest_relative not in seen:
        seen.add(manifest_relative)
        entries.append([manifest_relative, manifest_hash])
    return sorted(entries)


def source_digest(root, manifest_path=None) -> str:
    """Canonical source identity (schema 3) over :func:`source_inputs`."""
    entries = source_inputs(root, manifest_path)
    return hashlib.sha256(json.dumps(entries, sort_keys=True).encode("utf-8")).hexdigest()


def _iso_timestamp(value) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.datetime.fromisoformat(text)
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _timestamp(value):
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.datetime.fromisoformat(text)


def _recorded_at() -> str:
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def run_evidence(
    manifest: dict,
    site: str,
    results: list,
    *,
    run_id=None,
    project=None,
    source_root=None,
    manifest_path=None,
    recorded_at=None,
) -> dict:
    """Versioned run record binding every executable case to this run and source.

    The record is the producer half of the evidence contract. It always carries an entry
    for **every** executable case, so an under-bound record cannot be produced and then
    rejected downstream: a case with no result is recorded with ``derived_status:
    "missing"`` and an empty output hash, which the consumer rejects.
    """
    root = resolve_source_root(source_root)
    manifest_file = pathlib.Path(manifest_path or MANIFEST_PATH).expanduser().resolve()
    by_case = {}
    for result in results:
        case_id = result.get("case")
        if case_id in by_case:
            continue
        by_case[case_id] = result

    entries = []
    for case in executable_cases(manifest):
        result = by_case.get(case["id"])
        expected_id = expected_case_id(manifest, case)
        if result is None:
            entries.append(
                {
                    "case": case["id"],
                    "expected_id": expected_id,
                    "nonce": None,
                    "raw_output_sha256": output_digest(""),
                    "residue_rows": [],
                    "storage_keys": [],
                    "orphan_residue_rows": [],
                    "orphan_storage_keys": [],
                    "derived_status": "missing",
                }
            )
            continue
        output = result.get("output") or ""
        parsed = parse_case_output(
            result.get("exit_code", -1),
            output,
            expected_id=expected_id,
            nonce=result.get("nonce") or "",
        )
        entries.append(
            {
                "case": case["id"],
                "expected_id": expected_id,
                "nonce": result.get("nonce"),
                "raw_output_sha256": output_digest(output),
                "residue_rows": [list(row) for row in (parsed["residue"] or [])],
                "storage_keys": list(parsed["storage_keys"]),
                "orphan_residue_rows": [
                    list(row) for row in (parsed["orphan_residue"] or [])
                ],
                "orphan_storage_keys": list(parsed["orphan_storage_keys"]),
                "derived_status": parsed["status"],
            }
        )
    if run_id is None:
        material = json.dumps(
            [[entry["case"], entry["nonce"], entry["raw_output_sha256"]] for entry in entries],
            sort_keys=True,
        )
        run_id = hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]
    return {
        "schema": EVIDENCE_SCHEMA,
        "run_id": run_id,
        "site": site,
        "project": project or manifest["app"],
        "source_root": str(root),
        "source_digest": source_digest(root, manifest_file),
        "manifest_path": str(manifest_file),
        "contract_digest": contract_digest(),
        "recorded_at": recorded_at or _recorded_at(),
        "cases": entries,
    }


def run_residue(run: dict) -> tuple:
    """Canonical (rows, storage keys) a run's own evidence declares."""
    rows, keys = [], []
    for entry in run.get("cases") or []:
        try:
            rows.extend(canonical_rows(entry.get("residue_rows") or []))
            keys.extend(canonical_storage_keys(entry.get("storage_keys") or []))
        except (TypeError, ValueError):
            # Unusable evidence must not silently vanish from the obligation.
            rows.append(["<unusable-residue>", str(entry.get("case"))])
    return canonical_rows(rows), canonical_storage_keys(keys)


def _canonical_json(value) -> str:
    """Canonical JSON text used for the manifest object <-> manifest file comparison."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def validate_run(
    manifest: dict,
    results: list,
    run,
    *,
    source_root,
    manifest_path=None,
    expected_site=None,
) -> list:
    """Fail-closed validation of the producer record against reality.

    Everything is recomputed here: the source identity from the supplied trusted root,
    each case's status/identity/nonce/raw hash/residue from the raw output, and the case
    set from the manifest. The run record is compared *to* those recomputed values; it is
    never used to derive them. The invocation site (``expected_site``) and the manifest
    app are supplied independently, so a fabricated site/project cannot be validated by
    agreeing with itself.
    """
    problems = []
    if not isinstance(run, dict):
        return ["no run evidence record; acceptance needs a versioned run binding"]

    if run.get("schema") != EVIDENCE_SCHEMA:
        problems.append(
            f"run evidence schema {run.get('schema')!r} is not {EVIDENCE_SCHEMA}"
        )
    if run.get("contract_digest") != contract_digest():
        problems.append("run evidence does not match this command contract")
    for key in ("run_id", "site", "project", "source_root", "recorded_at"):
        if run.get(key) in (None, ""):
            problems.append(f"run evidence is missing {key}")
    if run.get("recorded_at") not in (None, "") and not _iso_timestamp(
        run.get("recorded_at")
    ):
        problems.append("run evidence recorded_at is not an ISO-8601 timestamp")

    # The invocation site is an independent input, never taken from the run record. The
    # project is the manifest's app. Run<->confirmation equality alone is not enough:
    # both could name the same fabricated bench.
    if not isinstance(expected_site, str) or not expected_site.strip():
        problems.append(
            "a trusted invocation site is required; the run record cannot supply it"
        )
    elif run.get("site") != expected_site:
        problems.append("run evidence site is not the trusted invocation site")
    if run.get("project") != manifest.get("app"):
        problems.append("run evidence project is not the manifest app")

    manifest_file = pathlib.Path(manifest_path or MANIFEST_PATH).expanduser().resolve()
    # Bind the *object under validation* to the file the run's digest points at: a
    # doctored in-memory manifest paired with the real path must not be trusted for
    # coverage or case expectations.
    try:
        file_payload = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        problems.append(f"supplied case manifest is unreadable: {error}")
    else:
        if _canonical_json(manifest) != _canonical_json(file_payload):
            problems.append(
                "the supplied manifest object is not the manifest file bound by the "
                "run evidence"
            )

    try:
        root = resolve_source_root(source_root)
    except ValueError as error:
        problems.append(str(error))
        root = None
    if root is not None:
        if run.get("source_root") not in (None, "") and pathlib.Path(
            run["source_root"]
        ).expanduser().resolve() != root:
            problems.append(
                "run evidence was produced for a different source root than the "
                "trusted root supplied"
            )
        if run.get("manifest_path") not in (None, "") and pathlib.Path(
            run["manifest_path"]
        ).expanduser().resolve() != manifest_file:
            problems.append(
                "run evidence was produced for a different case manifest than the one "
                "supplied"
            )
        try:
            expected_digest = source_digest(root, manifest_file)
        except ValueError as error:
            problems.append(f"cannot recompute source identity: {error}")
            expected_digest = None
        if expected_digest is not None and run.get("source_digest") != expected_digest:
            problems.append(
                "run evidence source identity does not match the trusted source root"
            )

    expected = {case["id"]: case for case in executable_cases(manifest)}
    # Required identity binding, checked on the *supplied* manifest before any coverage
    # or run evidence is credited: an id that has been moved onto another module, class
    # or method is not the required publication case, whatever it reports.
    for case in manifest.get("cases") or []:
        required_identity = REQUIRED_PUBLICATION_CASES.get(case.get("id"))
        if required_identity is None:
            continue
        identity = (
            case_module(manifest, case),
            case_class(manifest, case),
            case["method"],
        )
        if identity != required_identity:
            problems.append(
                f"required publication case {case.get('id')} is bound to "
                f"{'.'.join(identity)}, not its required identity "
                f"{'.'.join(required_identity)}"
            )
    entries = run.get("cases")
    if not isinstance(entries, list):
        problems.append("run evidence carries no case list")
        entries = []
    if not entries:
        problems.append("run evidence case list is empty")
    recorded_ids = [entry.get("case") for entry in entries]
    if len(recorded_ids) != len(set(recorded_ids)):
        problems.append("run evidence repeats a case entry")
    missing = sorted(set(expected) - set(recorded_ids))
    if missing:
        problems.append(f"run evidence omits executable case(s): {missing[:3]}")
    extra = sorted(set(recorded_ids) - set(expected))
    if extra:
        problems.append(f"run evidence includes non-executable case(s): {extra[:3]}")

    result_ids = [result.get("case") for result in results]
    if len(result_ids) != len(set(result_ids)):
        problems.append("duplicate result for case in the supplied results")
    result_missing = sorted(set(expected) - set(result_ids))
    if result_missing:
        problems.append(f"missing result for case(s): {result_missing[:3]}")
    result_extra = sorted({cid for cid in result_ids if cid is not None} - set(expected))
    if result_extra:
        problems.append(f"unexpected case result(s): {result_extra[:3]}")

    nonces = [
        result.get("nonce") for result in results if result.get("nonce")
    ]
    if len(nonces) != len(set(nonces)):
        problems.append("duplicate case nonce: one invocation's evidence was reused")

    by_case = {result.get("case"): result for result in results}
    for entry in entries:
        case = expected.get(entry.get("case"))
        if case is None:
            continue
        case_id = case["id"]
        expected_id = expected_case_id(manifest, case)
        if entry.get("expected_id") != expected_id:
            problems.append(
                f"case {case_id}: recorded expected_id is not this case's identity"
            )
        result = by_case.get(case_id)
        if result is None:
            continue
        parsed = parse_case_output(
            result.get("exit_code", -1),
            result.get("output") or "",
            expected_id=expected_id,
            nonce=result.get("nonce") or "",
        )
        if entry.get("nonce") != result.get("nonce"):
            problems.append(f"case {case_id}: recorded nonce is not this result's nonce")
        if entry.get("raw_output_sha256") != output_digest(result.get("output") or ""):
            problems.append(
                f"case {case_id}: raw output does not match the recorded run evidence"
            )
        if entry.get("residue_rows") != [list(row) for row in (parsed["residue"] or [])]:
            problems.append(
                f"case {case_id}: recorded residue rows are not this result's residue"
            )
        if list(entry.get("storage_keys") or []) != list(parsed["storage_keys"]):
            problems.append(
                f"case {case_id}: recorded storage keys are not this result's keys"
            )
        if entry.get("derived_status") != parsed["status"]:
            problems.append(
                f"case {case_id}: recorded status {entry.get('derived_status')!r} is not "
                f"the derived status {parsed['status']!r}"
            )
        if entry.get("orphan_residue_rows") is not None and entry.get(
            "orphan_residue_rows"
        ) != [list(row) for row in (parsed["orphan_residue"] or [])]:
            problems.append(
                f"case {case_id}: recorded orphan residue rows are not this result's"
            )
        if entry.get("orphan_storage_keys") is not None and list(
            entry.get("orphan_storage_keys") or []
        ) != list(parsed["orphan_storage_keys"]):
            problems.append(
                f"case {case_id}: recorded orphan storage keys are not this result's"
            )
    return problems


def validate_disposal(
    confirmation, run: dict, rows, keys, *, source_root=None
) -> list:
    """Fail-closed check of the versioned, identity-complete disposal record.

    The record is operator-supplied. It is *not* authenticated proof that a disposal was
    observed: nothing in this module can verify absence, so it is an identity-complete
    attestation that a disposal was performed. An empty-resource record asserts that no
    resource was created; it neither authorizes nor requires stack destruction.
    """
    problems = []
    if not isinstance(confirmation, dict):
        return ["no cleanup confirmation record; the run's residue is not disposed"]
    if confirmation.get("schema") != EVIDENCE_SCHEMA:
        problems.append(
            f"cleanup confirmation schema {confirmation.get('schema')!r} is not "
            f"{EVIDENCE_SCHEMA}"
        )
    for key in (
        "run_id",
        "site",
        "project",
        "source_root",
        "source_digest",
        "contract_digest",
        "recorded_at",
    ):
        if confirmation.get(key) in (None, ""):
            problems.append(f"cleanup confirmation is missing {key}")
    if confirmation.get("run_id") not in (None, "") and (
        confirmation.get("run_id") != run.get("run_id")
    ):
        problems.append("cleanup confirmation belongs to a different run")
    if confirmation.get("site") not in (None, "") and (
        confirmation.get("site") != run.get("site")
    ):
        problems.append("cleanup confirmation names a different site")
    if confirmation.get("project") not in (None, "") and (
        confirmation.get("project") != run.get("project")
    ):
        problems.append("cleanup confirmation names a different project")
    if confirmation.get("source_digest") not in (None, "") and (
        confirmation.get("source_digest") != run.get("source_digest")
    ):
        problems.append(
            "cleanup confirmation was not produced for this source revision"
        )
    if confirmation.get("source_root") not in (None, "") and (
        run.get("source_root") not in (None, "")
    ) and pathlib.Path(confirmation["source_root"]).expanduser().resolve() != pathlib.Path(
        run["source_root"]
    ).expanduser().resolve():
        problems.append("cleanup confirmation names a different source root")
    if confirmation.get("recorded_at") not in (None, "") and (
        confirmation.get("recorded_at") != run.get("recorded_at")
    ):
        problems.append("cleanup confirmation was not produced for this run's record")
    if confirmation.get("contract_digest") not in (None, "") and (
        confirmation.get("contract_digest") != contract_digest()
    ):
        problems.append(
            "cleanup confirmation does not match this command contract"
        )

    expected_nonces = {
        entry["case"]: entry["nonce"] for entry in run.get("cases") or []
    }
    if not expected_nonces:
        problems.append(
            "cleanup confirmation cannot be checked against an empty case set"
        )
    if confirmation.get("case_nonces") != expected_nonces:
        problems.append(
            "cleanup confirmation does not cover this run's case nonces"
        )
    expected_hashes = {
        entry["case"]: entry["raw_output_sha256"] for entry in run.get("cases") or []
    }
    if not expected_hashes:
        problems.append(
            "cleanup confirmation cannot be checked against an empty output-hash set"
        )
    if confirmation.get("raw_output_sha256") != expected_hashes:
        problems.append(
            "cleanup confirmation does not cover this run's raw output hashes"
        )

    try:
        confirmed_rows = canonical_rows(confirmation.get("residue_rows"))
    except (TypeError, ValueError) as error:
        confirmed_rows = None
        problems.append(f"cleanup confirmation residue rows are unusable: {error}")
    try:
        confirmed_keys = canonical_storage_keys(confirmation.get("storage_keys"))
    except (TypeError, ValueError) as error:
        confirmed_keys = None
        problems.append(f"cleanup confirmation storage keys are unusable: {error}")
    if confirmed_rows is not None and confirmed_rows != list(rows):
        problems.append(
            "cleanup confirmation residue rows do not match the residue this run "
            "created"
        )
    if confirmed_keys is not None and confirmed_keys != tuple(keys):
        problems.append(
            "cleanup confirmation does not match the protected storage keys this run "
            "created"
        )

    disposal = confirmation.get("disposal")
    if not isinstance(disposal, dict):
        problems.append("cleanup confirmation carries no disposal record")
        return problems
    if disposal.get("observed") is not True:
        # An unobserved claim (or an inference from empty residue) is not disposal.
        problems.append(
            "cleanup disposal was not observed; an empty residue list is not proof "
            "of filesystem cleanliness"
        )
    for key in ("disposed_by", "method", "observed_at"):
        if not isinstance(disposal.get(key), str) or not disposal.get(key).strip():
            problems.append(f"cleanup disposal lacks {key}")
    if isinstance(disposal.get("observed_at"), str) and disposal.get("observed_at"):
        if not _iso_timestamp(disposal["observed_at"]):
            problems.append("cleanup disposal observed_at is not an ISO-8601 timestamp")
        elif _iso_timestamp(run.get("recorded_at")):
            if _timestamp(disposal["observed_at"]) < _timestamp(run["recorded_at"]):
                problems.append(
                    "cleanup disposal is recorded before the run it claims to dispose of"
                )
    try:
        verified = canonical_absent(disposal.get("verified_absent"))
    except (TypeError, ValueError) as error:
        problems.append(f"cleanup disposal absent-resource list is unusable: {error}")
        verified = set()
    required = {(row[0], row[1]) for row in rows} | {
        ("storage", key) for key in keys
    }
    if not required <= verified:
        missing = sorted(required - verified)
        problems.append(
            "cleanup disposal does not record the absent state of "
            f"{len(missing)} created resource(s): {missing[:3]}"
        )
    return problems


def canonical_absent(entries) -> set:
    """Normalise a disposal's verified-absent list to (kind, identity) pairs."""
    verified = set()
    for entry in entries or []:
        if isinstance(entry, (list, tuple)) and len(entry) == 2:
            doctype, name = entry
            if not isinstance(doctype, str) or not isinstance(name, str):
                raise ValueError("absent resource identity must be text")
            if not doctype.strip() or not name.strip():
                raise ValueError("absent resource identity is empty")
            verified.add((doctype, name))
        elif isinstance(entry, str) and entry:
            canonical_private_key(entry)
            verified.add(("storage", entry))
        else:
            raise ValueError("absent resource entry is not an identity")
    return verified


def aggregate(
    manifest: dict,
    results: list,
    cleanup_confirmed=None,
    *,
    run=None,
    source_root=None,
    manifest_path=None,
    expected_site=None,
) -> dict:
    """Accept only a fully bound, evidence-backed executable subset.

    ``accepted`` is whole-matrix acceptance: the frozen publication inventory and every
    legacy case must be present, unblocked and verified passing.
    ``executable_subset_accepted`` describes the verified subset only, and
    ``publication_complete`` reports the publication inventory on its own.
    """
    required_cases = executable_cases(manifest)
    expected = [case["id"] for case in required_cases]
    blocked = blocked_cases(manifest)
    blocked_ids = {case["id"] for case in blocked}
    all_cases = {case["id"]: case for case in manifest["cases"]}
    by_case = {}
    passed_ids = set()
    problems = []
    duplicates = []
    residue = []
    storage_keys = []
    orphan_residue = []
    orphan_storage_keys = []

    for result in results:
        case_id = result.get("case")
        if case_id in blocked_ids:
            # Never a pass, never an acceptance input: a blocked case is unrun.
            problems.append(
                f"blocked case {case_id} was reported; it must remain unrun until "
                f"its prerequisite is met ({all_cases[case_id].get('blocked_reason')})"
            )
            continue
        if case_id not in all_cases or case_id not in expected:
            problems.append(f"unexpected case result: {case_id!r}")
            continue
        if case_id in by_case:
            duplicates.append(case_id)
            continue
        case = all_cases[case_id]

        # Raw evidence is mandatory. A supplied status is derived-only and is never
        # authority; a contradictory one is itself an acceptance failure.
        missing = [
            key
            for key in ("exit_code", "output", "nonce")
            if result.get(key) in (None, "")
        ]
        if missing:
            problems.append(
                f"case {case_id}: no execution evidence ({', '.join(missing)} missing)"
            )
            by_case[case_id] = result
            continue

        parsed = parse_case_output(
            result["exit_code"],
            result["output"],
            expected_id=expected_case_id(manifest, case),
            nonce=result["nonce"],
        )
        timed_out = result.get("timed_out") is True
        if timed_out:
            problems.append(
                f"case {case_id}: the case timed out; a timed-out execution is not "
                "acceptance evidence, whatever its partial output said"
            )
        supplied = result.get("status")
        if supplied is not None and supplied != parsed["status"]:
            problems.append(
                f"case {case_id}: supplied status {supplied!r} contradicts evidence "
                f"{parsed['status']!r}"
            )
        if parsed["status"] != "passed":
            detail = parsed.get("detail") or ""
            problems.append(f"case {case_id}: {parsed['status']} {detail}".strip())
        elif not timed_out:
            passed_ids.add(case_id)
        if parsed["residue"]:
            residue.extend(parsed["residue"])
        if parsed["storage_keys"]:
            storage_keys.extend(parsed["storage_keys"])
        if parsed["orphan_residue"]:
            orphan_residue.extend(parsed["orphan_residue"])
        if parsed["orphan_storage_keys"]:
            orphan_storage_keys.extend(parsed["orphan_storage_keys"])
        by_case[case_id] = dict(result, derived_status=parsed["status"])

    for case_id in duplicates:
        problems.append(f"duplicate result for case: {case_id}")

    missing_cases = [case_id for case_id in expected if case_id not in by_case]
    for case_id in missing_cases:
        problems.append(f"missing result for case: {case_id}")

    canonical = canonical_residue(residue, storage_keys)
    orphan = canonical_residue(orphan_residue, orphan_storage_keys)
    if orphan["rows"] or orphan["storage_keys"]:
        problems.append(
            "an uncredited execution reported residue whose case identity is unproven; "
            "it must be disposed of and the case re-run under matching evidence"
        )

    problems.extend(
        validate_run(
            manifest,
            results,
            run,
            source_root=source_root,
            manifest_path=manifest_path,
            expected_site=expected_site,
        )
    )
    # F11: the run record's own residue must agree with what the outputs prove.
    if isinstance(run, dict) and run.get("cases"):
        declared_rows, declared_keys = run_residue(run)
        if declared_rows != canonical["rows"] or list(declared_keys) != canonical[
            "storage_keys"
        ]:
            problems.append(
                "run evidence residue does not match the residue derived from the raw "
                "outputs"
            )

    disposal_problems = validate_disposal(
        cleanup_confirmed, run if isinstance(run, dict) else {},
        canonical["rows"], canonical["storage_keys"], source_root=source_root,
    )
    if disposal_problems:
        if cleanup_confirmed is None:
            problems.append(
                "protected fixtures were created and no cleanup disposition is "
                "confirmed; acceptance depends on an authorized, observed disposal "
                "of the listed residue"
            )
        else:
            problems.extend(
                f"cleanup confirmation rejected: {item}" for item in disposal_problems
            )

    coverage = _coverage(manifest, passed_ids)
    subset_accepted = not problems
    publication_complete = coverage["publication_complete"]
    # Whole-matrix completeness: nothing blocked, nothing required publication missing
    # or unverified. This is deliberately broader than the publication-specific signal:
    # a blocked legacy case blocks acceptance too.
    matrix_complete = coverage["blocked_unrun"] == 0 and publication_complete
    incomplete = []
    if coverage["publication_required_missing"]:
        incomplete.append(
            f"{coverage['publication_required_missing']} required publication case(s) "
            "are missing from the manifest; the obligation is frozen and cannot be "
            "removed by editing the manifest"
        )
    if coverage["publication_required_blocked"]:
        incomplete.append(
            f"{coverage['publication_required_blocked']} required publication case(s) "
            "are unimplemented and unrun; the executable subset is not publication "
            "acceptance"
        )
    if coverage["publication_required_rebound"]:
        incomplete.append(
            f"{coverage['publication_required_rebound']} required publication case(s) "
            "are bound to a different test identity than the frozen requirement; "
            "re-binding an id does not transfer the obligation"
        )
    if coverage["publication_required_unverified"]:
        incomplete.append(
            f"{coverage['publication_required_unverified']} executable publication "
            "case(s) have no valid passing evidence"
        )
    if coverage["blocked_unrun"]:
        incomplete.append(
            f"{coverage['blocked_unrun']} required case(s) are blocked and unrun"
        )
    if not results:
        native_status = NATIVE_UNRUN
    elif subset_accepted and not matrix_complete:
        native_status = "ACCEPTED_EXECUTABLE_SUBSET"
    elif subset_accepted:
        native_status = "ACCEPTED"
    else:
        native_status = "UNACCEPTED"

    return {
        "accepted": subset_accepted and matrix_complete and bool(results),
        "executable_subset_accepted": bool(results) and subset_accepted,
        "acceptance_scope": (
            "complete_matrix" if matrix_complete else "executable_subset_only"
        ),
        "coverage": coverage,
        "publication_complete": publication_complete,
        "matrix_complete": matrix_complete,
        "incomplete_reasons": incomplete,
        "evidence_schema": EVIDENCE_SCHEMA,
        "native_status": native_status,
        "expected_cases": len(expected),
        "reported_cases": len(by_case),
        "blocked_cases": [
            {
                "case": case["id"],
                "expected_id": expected_case_id(manifest, case),
                "native_status": NATIVE_UNRUN,
                "reason": case.get("blocked_reason"),
            }
            for case in blocked
        ],
        "run": {
            "schema": (run or {}).get("schema") if isinstance(run, dict) else None,
            "run_id": (run or {}).get("run_id") if isinstance(run, dict) else None,
            "site": (run or {}).get("site") if isinstance(run, dict) else None,
            "project": (run or {}).get("project") if isinstance(run, dict) else None,
            "source_root": (run or {}).get("source_root")
            if isinstance(run, dict)
            else None,
            "source_digest": (run or {}).get("source_digest")
            if isinstance(run, dict)
            else None,
            "manifest_path": (run or {}).get("manifest_path")
            if isinstance(run, dict)
            else None,
            "recorded_at": (run or {}).get("recorded_at")
            if isinstance(run, dict)
            else None,
            "contract_digest": contract_digest(),
            "cases": list((run or {}).get("cases") or [])
            if isinstance(run, dict)
            else [],
        },
        "cleanup_required": canonical["rows"],
        "cleanup_required_storage_keys": canonical["storage_keys"],
        "cleanup_digest": residue_digest(
            canonical["rows"], canonical["storage_keys"]
        ),
        "orphan_residue": orphan["rows"],
        "orphan_storage_keys": orphan["storage_keys"],
        "cleanup_confirmed": bool(cleanup_confirmed) and not disposal_problems,
        "problems": problems,
    }


def check_verification(path, contract=None) -> tuple:
    """Fail closed unless a matching CLI verification record exists."""
    if not path:
        return False, (
            "the bench CLI contract is unverified; supply --cli-verified with the "
            "verification record from the target bench"
        )
    record_path = pathlib.Path(path)
    if not record_path.exists():
        return False, f"verification record not found: {record_path}"
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except ValueError as error:
        return False, f"verification record is not valid JSON: {error}"
    if record.get("contract_digest") != contract_digest(contract):
        return False, "verification record does not match this command contract"
    supported = record.get("supports") or []
    for option in ("--app", "--module", "--case", "--test"):
        if option not in supported:
            return False, f"verification record does not confirm support for {option}"
    if record.get("exit_code") not in (0, "0"):
        return False, "verification record shows a failing help invocation"
    if not record.get("evidence"):
        return False, "verification record carries no help output evidence"
    return True, "verified"


def _load_cleanup(path):
    """Return ``(record, error)``; a read failure is reported, not silently absent."""
    if not path:
        return None, None
    try:
        return json.loads(pathlib.Path(path).read_text(encoding="utf-8")), None
    except (OSError, ValueError) as error:
        return None, f"cannot read cleanup confirmation: {error}"


def _refuse_after_aggregate(report: dict, problem: str) -> dict:
    """Apply a post-aggregation refusal without leaving a success machine status.

    ``native_status`` is recomputed here: an artifact must never read
    ``executable_subset_accepted: false`` next to ``ACCEPTED`` or
    ``ACCEPTED_EXECUTABLE_SUBSET``.
    """
    report.setdefault("problems", []).append(problem)
    report["accepted"] = False
    report["executable_subset_accepted"] = False
    report["acceptance_scope"] = "executable_subset_only"
    report["matrix_complete"] = False
    report["native_status"] = (
        "UNACCEPTED" if report.get("reported_cases") else NATIVE_UNRUN
    )
    return report


def execute_case(command: list, nonce: str, timeout: int = 1800) -> dict:
    environment = dict(os.environ)
    environment[CASE_NONCE_ENV] = nonce
    # One merged pipe: markers may be interleaved with unittest progress, and
    # capturing the streams separately would reorder them on concatenation. The raw
    # order is preserved, never reordered or scrubbed.
    #
    # The ``bench`` CLI must be invoked from a bench root; the runner inherits the
    # caller's cwd and a wrong cwd fails closed (non-zero, no accepted evidence).
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
            check=False,
            env=environment,
        )
    except subprocess.TimeoutExpired as expired:
        # A hung case must still produce a report and a recorded (non-passing) result
        # instead of crashing the runner with no evidence at all.
        captured = expired.stdout or ""
        if isinstance(captured, bytes):
            captured = captured.decode("utf-8", "replace")
        return {
            "exit_code": -1,
            "output": captured + "\ncase timed out; no verdict evidence\n",
            "timed_out": True,
        }
    return {
        "exit_code": completed.returncode,
        "output": completed.stdout or "",
    }


def _read_results(path) -> tuple:
    """Read a results file as ``(run, results)``.

    A bare list is still readable, but it carries no run binding, so its residue
    cannot be disposed of against this run and acceptance will fail closed.
    """
    data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        return data.get("run"), data.get("results") or []
    return None, data


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Isolated per-case runner for the clinic_photos native harness."
    )
    parser.add_argument("--manifest", default=None, help="path to case_manifest.json")
    parser.add_argument(
        "--site", default="$PHOTO_TEST_SITE", help="bench site name (execution only)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate the manifest and print one command per case (default)",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="run every case in its own bench process (needs a verified CLI contract)",
    )
    parser.add_argument(
        "--cli-verified", default=None, help="CLI verification record JSON"
    )
    parser.add_argument(
        "--aggregate", default=None, help="aggregate a JSON results file instead of running"
    )
    parser.add_argument(
        "--cleanup-confirmed",
        default=None,
        help="record of the authorized disposal of the residue this run created",
    )
    parser.add_argument(
        "--source-root",
        default=None,
        help=(
            "trusted app source root used to recompute the source identity "
            "(required for --execute and --aggregate)"
        ),
    )
    parser.add_argument(
        "--manifest-path",
        default=None,
        help=(
            "case manifest whose bytes are bound into the source identity "
            "(defaults to --manifest, then the packaged manifest)"
        ),
    )
    parser.add_argument("--json-out", default=None, help="write the report to this path")
    args = parser.parse_args(argv)

    try:
        manifest = load_manifest(args.manifest)
    except (OSError, ValueError) as error:
        print(json.dumps({"error": f"cannot load manifest: {error}"}, indent=2))
        return 2

    problems = validate_manifest(manifest)
    if problems:
        print(json.dumps({"valid": False, "problems": problems}, indent=2))
        return 2

    manifest_path = (
        args.manifest_path or args.manifest or str(MANIFEST_PATH)
    )

    if args.aggregate:
        try:
            run, results = _read_results(args.aggregate)
        except (OSError, ValueError) as error:
            print(json.dumps({"error": f"cannot read results: {error}"}, indent=2))
            return 2
        cleanup, cleanup_error = _load_cleanup(args.cleanup_confirmed)
        report = aggregate(
            manifest,
            results,
            cleanup,
            run=run,
            source_root=args.source_root,
            manifest_path=manifest_path,
            expected_site=args.site,
        )
        if cleanup_error:
            _refuse_after_aggregate(report, cleanup_error)
        report["cleanup_read_error"] = cleanup_error
        print(json.dumps(report, indent=2))
        if args.json_out:
            pathlib.Path(args.json_out).write_text(
                json.dumps(report, indent=2), encoding="utf-8"
            )
        return 0 if report["accepted"] else 1

    if not args.execute:
        report = build_dry_run(manifest, args.site)
        print(json.dumps(report, indent=2))
        if args.json_out:
            pathlib.Path(args.json_out).write_text(
                json.dumps(report, indent=2), encoding="utf-8"
            )
        return 0

    verified, detail = check_verification(args.cli_verified)
    if not verified:
        print(
            json.dumps(
                {
                    "executed": False,
                    "error": detail,
                    "next_step": CLI_CONTRACT["verification_requirement"],
                },
                indent=2,
            )
        )
        return 2

    try:
        resolve_source_root(args.source_root)
    except ValueError as error:
        print(
            json.dumps(
                {
                    "executed": False,
                    "error": str(error),
                    "next_step": (
                        "supply --source-root with the trusted app source root; "
                        "the run record cannot supply it"
                    ),
                },
                indent=2,
            )
        )
        return 2

    results = []
    run_id = secrets.token_hex(16)
    # Blocked cases are never selected: they stay explicitly unrun.
    for case in executable_cases(manifest):
        nonce = secrets.token_hex(16)
        command = build_command(manifest, case, args.site)
        outcome = execute_case(command, nonce)
        results.append(
            {
                "case": case["id"],
                "expected_id": expected_case_id(manifest, case),
                "nonce": nonce,
                "command": command,
                "exit_code": outcome["exit_code"],
                "output": outcome["output"],
                "timed_out": bool(outcome.get("timed_out")),
            }
        )

    run = run_evidence(
        manifest,
        args.site,
        results,
        run_id=run_id,
        source_root=args.source_root,
        manifest_path=manifest_path,
    )
    cleanup, cleanup_error = _load_cleanup(args.cleanup_confirmed)
    report = aggregate(
        manifest,
        results,
        cleanup,
        run=run,
        source_root=args.source_root,
        manifest_path=manifest_path,
        expected_site=args.site,
    )
    report["results"] = results
    report["run"] = run
    report["cleanup_read_error"] = cleanup_error
    if cleanup_error:
        _refuse_after_aggregate(report, cleanup_error)
    print(json.dumps(report, indent=2))
    if args.json_out:
        pathlib.Path(args.json_out).write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
    return 0 if report["accepted"] else 1


if __name__ == "__main__":
    sys.exit(main())
