"""Native publication acceptance cases for durable photo save.

Two kinds of case live here, and the case manifest names which is which:

* **executable** — a real case that can be selected and run on the approved
  disposable site, in its own native execution, with the runner's per-invocation
  nonce. Most cover the fixed fresh-child entry (``python -m
  clinic_photos.publication.process`` from ``/home/frappe/frappe-bench/sites``), its
  site binding and its refuse-before-any-residue behaviour, and create no domain,
  File or byte fixture, so their residue list is empty — which is *not* a claim that
  the disk is clean: the runner still requires an observed disposal record. Four
  authored cases (first JPEG original, lost-response replay, replay after a newer
  version and the non-allowlisted cross-actor inspection denial) publish real
  protected objects through that same child and report every operation, domain,
  version, File and storage key they create. They are authored and selectable here
  but have **not** been executed: their native status is UNRUN, and source readiness
  is not native acceptance.
* **blocked** — a placeholder whose prerequisite does not exist. Each is
  ``@unittest.skip`` carrying the manifest's canonical ``blocked_reason`` plus a
  ``self.fail`` body, so a skip can never be read as a native pass. Blocked cases are
  excluded from the acceptance matrix by ``case_manifest.json``
  (``readiness: "blocked"``), never counted as coverage, and emit no provenance at all.

Only executable cases emit the proven marker protocol: the selected test method writes
its own ``CLINIC_PHOTOS_CASE_BEGIN``/``END`` pair carrying unittest's ``self.id()`` and
the runner nonce, and the class teardown writes the ``CLINIC_PHOTOS_CASE_RESIDUE`` list
of rows and protected storage keys. Markers reach the *original* stdout because
Frappe's buffered test result replaces ``sys.stdout`` with a capture it never flushes.
A blocked placeholder runs nothing, so it writes no BEGIN/END and no residue marker.

This module imports no Frappe code at module import time: it must stay importable
(and manifest-inspectable) without a site. Framework imports happen inside the case
bodies.

No case here creates a protected byte, seeds a reservation, swaps a database, clears
a latched abort, or deletes protected residue. Synthetic fixtures, when a later task
can author them, may only be produced by the real writer/coordinator after the
disposable site, synthetic mode, feature gates and native actor/domain authorization
are all explicitly in force.
"""

import base64
import contextlib
import functools
import hashlib
import io
import json
import os
import pathlib
import re
import secrets
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest

from clinic_photos.tests.run_isolated_cases import (
    BEGIN_MARKER,
    CASE_NONCE_ENV,
    END_MARKER,
    RESIDUE_MARKER,
    emit_marker,
)

APPROVED_SITE = "clinic-photo-accept.local"
SITES_PATH = "/home/frappe/frappe-bench/sites"
BENCH_ENV = "/home/frappe/frappe-bench/env"
PROCESS_MODULE = "clinic_photos.publication.process"
PUBLICATION_GATE = "clinic_photos_publication_enabled"
SYNTHETIC_MODE = "clinic_photos_synthetic_mode"
HARNESS_OBSERVATION_PREFIX = "harness observation:"
MAX_CHILD_OUTPUT_LINES = 20
MAX_CHILD_OUTPUT_BYTES = 4096
DIAGNOSTIC_ENV = "CLINIC_PHOTOS_PUBLICATION_ENTRY_DIAGNOSTIC"
OPERATION_ID_PATTERN = re.compile(r"[0-9a-f]{32}")

# Externally provisioned synthetic fixture on the disposable site. These name an
# actor and a Patient that already exist: nothing here creates, discovers or
# reconfigures one, and a missing fixture refuses the case instead of substituting a
# real record or Administrator. The defaults are the pinned development fixture; an
# override must still name a synthetic, non-production identity.
SYNTHETIC_CLINICIAN_ENV = "CLINIC_PHOTOS_SYNTHETIC_CLINICIAN"
SYNTHETIC_PATIENT_ENV = "CLINIC_PHOTOS_SYNTHETIC_PATIENT"
# The cross-actor audit case needs a *second* externally provisioned synthetic
# clinician. It has no default on purpose: an absent value refuses the case rather
# than reusing the author or discovering another user.
SYNTHETIC_OBSERVER_ENV = "CLINIC_PHOTOS_SYNTHETIC_OBSERVER_CLINICIAN"
DEFAULT_SYNTHETIC_CLINICIAN = (
    "native-probe-clinician-20261108t083005z@example.invalid"
)
DEFAULT_SYNTHETIC_PATIENT = "NativeProbe 20261108T083005Z"
SYNTHETIC_CLINICIAN = os.environ.get(
    SYNTHETIC_CLINICIAN_ENV, DEFAULT_SYNTHETIC_CLINICIAN
).strip()
SYNTHETIC_PATIENT = os.environ.get(SYNTHETIC_PATIENT_ENV, DEFAULT_SYNTHETIC_PATIENT).strip()
SYNTHETIC_OBSERVER = os.environ.get(SYNTHETIC_OBSERVER_ENV, "").strip()

OPERATION_DOCTYPE = "Clinic Photo Save Operation"
DRAFT_DOCTYPE = "Clinic Photo Draft"
PHOTO_DOCTYPE = "Clinic Photo"
VERSION_DOCTYPE = "Clinic Photo Annotation Version"

# The outcome/preparation columns Task 6 declares. While any is missing every
# operation reader refuses, so a native publication case must refuse before staging
# rather than observe an incomplete row.
REQUIRED_OPERATION_COLUMNS = frozenset(
    {
        "reservation_key",
        "generation",
        "expected_sha256",
        "expected_size",
        "published_file",
        "published_domain_doctype",
        "published_domain_name",
        "published_version",
        "creation_identity",
    }
)

SAFE_PREREQUISITE = (
    "native publication prerequisites are not in force on this site "
    "({missing}); no fixture was staged and this case must not be read as a pass"
)
SAFE_INVENTORY_UNCERTAIN = (
    "publication residue inventory is incomplete; the reported rows and storage keys "
    "are a partial list and must not be read as a clean or absent result"
)

# The only feature doctypes a residue inventory may name from a published outcome.
DOMAIN_INVENTORY_DOCTYPES = frozenset({PHOTO_DOCTYPE, VERSION_DOCTYPE})


STATUS_REFUSED = "refused"
STATUS_UNAVAILABLE = "unavailable"
STATUS_COMPLETED = "completed"
EXIT_CODES = {STATUS_COMPLETED: 0, STATUS_REFUSED: 2, STATUS_UNAVAILABLE: 3}

# ---------------------------------------------------------------------------
# Task 3: private external-operator handoff and fresh revocation probes
# ---------------------------------------------------------------------------
REVOCATION_PROBE_MODULE = "clinic_photos.tests.revocation_probe"
REVOCATION_CONTROL_DIR_ENV = "CLINIC_PHOTOS_REVOCATION_CONTROL_DIR"
REVOCATION_VERIFIER_ENV = "CLINIC_PHOTOS_REVOCATION_VERIFIER"
REVOCATION_BASELINE_FD_ENV = "CLINIC_PHOTOS_REVOCATION_BASELINE_FD"
REVOCATION_ARMS = ("disabled", "role_removed")
REVOCATION_PROBE_TIMEOUT = 30.0
REVOCATION_ACTION_TIMEOUT = 120.0
REVOCATION_CASE_TIMEOUT = 1200.0
REVOCATION_ACK_POLL = 0.2
REVOCATION_ACK_BYTES = 4096
REVOCATION_BASELINE_BYTES = 65536
REVOCATION_OUTPUT_BYTES = 16384
REVOCATION_TERM_GRACE = 2.0
REVOCATION_ACTION_KEYS = frozenset(
    {
        "schema",
        "run_nonce",
        "action_nonce",
        "site",
        "source_digest",
        "operation_id",
        "actor",
        "arm",
        "action",
        "baseline_digest",
        "baseline_roles",
    }
)
REVOCATION_ACK_KEYS = frozenset(
    (REVOCATION_ACTION_KEYS - {"baseline_roles"}) | {"status"}
)
REVOCATION_ACTION_STATUS = "done"
SAFE_PROBE_UNCERTAIN = (
    "revocation probe child outcome is uncertain; no raw child diagnostic is retained"
)
SAFE_PROBE_REFUSED = "revocation probe child result is not creditable"
SAFE_ACTION_TIMEOUT = (
    "revocation operator acknowledgement did not arrive before the deadline; "
    "no resubmission is attempted"
)
SAFE_ACTION_REFUSED = "revocation operator acknowledgement refused"
SAFE_CONTROL_REFUSED = (
    "revocation control directory or verifier configuration refused before staging"
)
SAFE_TRANSPORT_REFUSED = "revocation private transport could not be sealed"
SAFE_BASELINE_REFUSED = "revocation private baseline refused"
SAFE_RESTORE_INCOMPLETE = (
    "revocation fixture restoration is incomplete; the changed state is not proven "
    "restored and no later arm may run"
)

# Same attribute the production latch uses (``access.frappe_context``); repeated
# here only so the stale-execution guard needs no framework import.
ABORT_ATTR = "clinic_photos_publication_aborted"


# ---------------------------------------------------------------------------
# Runner provenance: emitted by the selected test itself, never by the runner
# ---------------------------------------------------------------------------
def _case_nonce() -> str:
    """Per-invocation nonce supplied by the isolated runner (empty when manual)."""
    return os.environ.get(CASE_NONCE_ENV, "")


def _emit(marker: str, payload: dict) -> None:
    """Delegate to the runner's original-stdout channel (see ``emit_marker``)."""
    emit_marker(marker, payload)


def _case(method):
    """Emit verifiable BEGIN/END evidence for exactly this selected test.

    The identity is unittest's own ``self.id()`` (module.Class.method), so a runner
    cannot relabel an arbitrary pass as the requested case, and the nonce binds the
    evidence to one runner invocation. Running the module by hand produces no
    accepted provenance, which is intentional: acceptance requires the runner.
    """

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        nonce = _case_nonce()
        identity = self.id()
        if not nonce:
            self.fail(
                "case evidence requires the isolated runner to supply "
                f"{CASE_NONCE_ENV}"
            )
        _emit(BEGIN_MARKER, {"id": identity, "nonce": nonce})
        try:
            method(self, *args, **kwargs)
        except BaseException:
            _emit(END_MARKER, {"id": identity, "nonce": nonce, "status": "fail"})
            raise
        _emit(END_MARKER, {"id": identity, "nonce": nonce, "status": "pass"})

    return wrapper


# ---------------------------------------------------------------------------
# Fixture helpers: pure decisions first, framework access only in the case body
# ---------------------------------------------------------------------------
def publication_precondition_failures(observed) -> list:
    """Ordered fixed codes for every prerequisite this execution does not satisfy.

    ``observed`` carries measurements the caller already took; this function reads no
    framework state and writes nothing, so the refusal order is testable without a
    site. Both gates must be the boolean ``True``: a truthy string such as ``"1"`` is
    an observed failure, not a satisfied gate.
    """
    failures = []
    if observed.get("site") != APPROVED_SITE:
        failures.append("approved_site")
    if observed.get("publication_gate") is not True:
        failures.append("publication_gate")
    if observed.get("synthetic_mode") is not True:
        failures.append("synthetic_mode")
    if not observed.get("bench_interpreter"):
        failures.append("bench_interpreter")
    if not observed.get("actor_exists"):
        failures.append("clinician_actor")
    elif not observed.get("actor_enabled") or not observed.get("actor_clinician"):
        failures.append("clinician_actor")
    if not observed.get("patient_exists"):
        failures.append("synthetic_patient")
    if not observed.get("patient_read"):
        failures.append("patient_read")
    if observed.get("missing_operation_columns"):
        failures.append("operation_schema")
    return failures


def observer_precondition_failures(observed) -> list:
    """Ordered fixed codes for the cross-actor observer prerequisites.

    ``observed`` carries measurements the caller already took; this function reads no
    framework state and writes nothing. The observer must be explicitly supplied,
    distinct from the operation author, an existing enabled clinician and absent from
    the configured operator allowlist. An absent or empty allowlist is the normal
    default and is valid; a malformed value refuses the case instead of denying for a
    reason unrelated to the observer's non-membership.
    """
    failures = []
    observer = observed.get("observer") or ""
    author = observed.get("author") or ""
    if not observer:
        failures.append("observer_identity")
    else:
        if observer == author:
            failures.append("observer_distinct")
        if not observed.get("observer_exists"):
            failures.append("observer_actor")
        elif not observed.get("observer_enabled") or not observed.get(
            "observer_clinician"
        ):
            failures.append("observer_actor")
    allowlist = observed.get("operator_allowlist")
    if allowlist in (None, [], ()):
        pass
    elif not isinstance(allowlist, (list, tuple)) or any(
        not isinstance(entry, str) for entry in allowlist
    ):
        failures.append("operator_allowlist")
    elif observer and observer in allowlist:
        failures.append("observer_is_operator")
    return failures


def missing_operation_columns(column_rows) -> list:
    """Required operation columns absent from the installed table, in stable order."""
    present = {row[0] for row in column_rows or () if row}
    return sorted(REQUIRED_OPERATION_COLUMNS - present)


def has_injected_content(file_doc) -> bool:
    """Whether a File carries truthy cached content instead of the native default.

    Pinned Frappe 16.53 ``File.__init__`` is ``self.content = self.get("content") or
    b""``, so a freshly loaded disk-backed File reads ``b""`` — never ``None``. Both
    ``b""`` and ``None`` mean "read from disk"; only truthy content would short-circuit
    the effective read, and this case never supplies any. The predicate is truthiness,
    matching the shipped inspection guard, not identity with ``None``.
    """
    return bool(getattr(file_doc, "content", None))


def add_safe_note(error, note: str) -> None:
    """Attach one fixed diagnostic to an exception without replacing it."""
    notes = getattr(error, "__notes__", None)
    if notes is None:
        notes = []
        try:
            setattr(error, "__notes__", notes)
        except Exception:  # pragma: no cover - exotic exception types
            return
    if note not in notes:
        notes.append(note)


# ---------------------------------------------------------------------------
# Task 3: private operator handoff and owned probe-child boundary.
#
# These helpers are test-side only. They never execute an operator action, mutate a
# User/role, reuse a parent transaction after the external change or treat an
# acknowledgement as state proof. Every private document is exact-key, bounded and
# duplicate-rejecting; no child diagnostic is ever formatted into a message.
# ---------------------------------------------------------------------------
_REVOCATION_NONCE_PATTERN = re.compile(r"[0-9a-f]{32}")
_REVOCATION_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_REVOCATION_CHILD_ENV = ("PATH", "PYTHONPATH")
_REVOCATION_ACTIONS = frozenset({"change", "restore"})
_REVOCATION_ROLE_KEYS = frozenset({"role", "parenttype", "parentfield"})


def _emit_revocation_log(text: str) -> None:
    """Emit exactly one fixed control-wait line; never a raw child diagnostic."""
    stream = sys.__stdout__
    if stream is None or getattr(stream, "closed", True):
        return
    try:
        stream.write(text + "\n")
        stream.flush()
    except Exception:
        return


def _canonical_private_json(value) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError):
        raise AssertionError(SAFE_TRANSPORT_REFUSED) from None


def _no_duplicate_private_keys(pairs):
    document = {}
    for key, value in pairs:
        if key in document:
            raise ValueError("duplicate private key")
        document[key] = value
    return document


def _reject_private_constant(value):
    raise ValueError("non-finite private number")


def _parse_private_json(raw: bytes, limit: int, message: str) -> dict:
    """Strict one-line bounded JSON; duplicates, NaN and trailing data are refused."""
    if not isinstance(raw, bytes) or len(raw) > limit:
        raise AssertionError(message)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise AssertionError(message) from None
    if text.endswith("\n"):
        text = text[:-1]
    if not text or "\n" in text or "\r" in text:
        raise AssertionError(message)
    try:
        document = json.loads(
            text,
            object_pairs_hook=_no_duplicate_private_keys,
            parse_constant=_reject_private_constant,
        )
    except (TypeError, ValueError, RecursionError):
        raise AssertionError(message) from None
    if not isinstance(document, dict):
        raise AssertionError(message)
    return document


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if written <= 0:
            raise OSError("short private write")
        offset += written


def _read_all(descriptor: int, limit: int) -> bytes:
    chunks = []
    total = 0
    while True:
        room = limit - total
        if room <= 0:
            break
        chunk = os.read(descriptor, min(8192, room))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


def _validate_control_directory(value) -> str:
    """Absolute existing effective-UID 0700 directory with no symlink component."""
    if not isinstance(value, str) or not value or not os.path.isabs(value):
        raise AssertionError(SAFE_CONTROL_REFUSED)
    path = os.path.normpath(value)
    parts = pathlib.PurePath(path).parts
    current = parts[0]
    for part in parts[1:]:
        current = os.path.join(current, part)
        try:
            info = os.lstat(current)
        except OSError:
            raise AssertionError(SAFE_CONTROL_REFUSED) from None
        if stat.S_ISLNK(info.st_mode):
            raise AssertionError(SAFE_CONTROL_REFUSED)
    try:
        info = os.lstat(path)
    except OSError:
        raise AssertionError(SAFE_CONTROL_REFUSED) from None
    if not stat.S_ISDIR(info.st_mode):
        raise AssertionError(SAFE_CONTROL_REFUSED)
    if info.st_uid != os.geteuid():
        raise AssertionError(SAFE_CONTROL_REFUSED)
    if stat.S_IMODE(info.st_mode) != 0o700:
        raise AssertionError(SAFE_CONTROL_REFUSED)
    return path


def _require_revocation_configuration():
    """Refuse absent/unsafe control configuration before any fixture staging."""
    control = _validate_control_directory(
        os.environ.get(REVOCATION_CONTROL_DIR_ENV)
    )
    verifier = (os.environ.get(REVOCATION_VERIFIER_ENV) or "").strip()
    actor = SYNTHETIC_CLINICIAN
    if not actor or actor in {"Guest", "Administrator"}:
        raise AssertionError(SAFE_CONTROL_REFUSED)
    if (
        not verifier
        or len(verifier) > 140
        or verifier.strip() != verifier
        or verifier in {"Guest", "Administrator"}
        or verifier == actor
    ):
        raise AssertionError(SAFE_CONTROL_REFUSED)
    return control, verifier


def _sealed_input_descriptor(directory: str, payload: bytes) -> int:
    """Write the private input, reopen it read-only and unlink the name pre-launch."""
    try:
        writer, name = tempfile.mkstemp(prefix=".revocation-input-", dir=directory)
    except OSError:
        raise AssertionError(SAFE_TRANSPORT_REFUSED) from None
    reader = None
    try:
        os.fchmod(writer, 0o600)
        _write_all(writer, payload)
        os.lseek(writer, 0, os.SEEK_SET)
        reader = os.open(
            name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        )
        os.unlink(name)
        return reader
    except BaseException:
        if reader is not None:
            try:
                os.close(reader)
            except OSError:
                pass
        try:
            os.unlink(name)
        except OSError:
            pass
        raise AssertionError(SAFE_TRANSPORT_REFUSED) from None
    finally:
        try:
            os.close(writer)
        except OSError:
            pass


def _probe_environment(baseline_descriptor) -> dict:
    environment = {}
    for name in _REVOCATION_CHILD_ENV:
        value = os.environ.get(name)
        if value:
            environment[name] = value
    if baseline_descriptor is not None:
        environment[REVOCATION_BASELINE_FD_ENV] = str(baseline_descriptor)
    return environment


def _read_private_baseline_descriptor(descriptor: int) -> dict:
    """Read the inherited baseline after a proven exit; bounded and strict."""
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
    except OSError:
        raise AssertionError(SAFE_BASELINE_REFUSED) from None
    try:
        raw = _read_all(descriptor, REVOCATION_BASELINE_BYTES + 1)
    except OSError:
        raise AssertionError(SAFE_BASELINE_REFUSED) from None
    if len(raw) > REVOCATION_BASELINE_BYTES:
        raise AssertionError(SAFE_BASELINE_REFUSED)
    return _parse_private_json(raw, REVOCATION_BASELINE_BYTES, SAFE_BASELINE_REFUSED)


def _expected_revocation_bindings(document, wire, probe_module):
    expected = {
        "run_nonce": wire["run_nonce"],
        "invocation_nonce": wire["invocation_nonce"],
        "site_digest": probe_module.site_digest(wire["site"]),
        "source_digest": wire["source_digest"],
        "operation_id": wire["operation_id"],
        "arm": wire["arm"],
        "mode": wire["mode"],
    }
    baseline = wire.get("baseline")
    if baseline is not None:
        expected["baseline_digest"] = probe_module.baseline_digest(baseline)
        expected["evidence_digest"] = baseline["evidence_digest"]
    return expected


def _parse_revocation_response(raw: bytes, expected: dict) -> dict:
    """One compact 16384-byte JSON line; every weaker form is refused."""
    from clinic_photos.tests import revocation_probe as probe_module

    if not isinstance(raw, bytes) or len(raw) > REVOCATION_OUTPUT_BYTES:
        raise AssertionError(SAFE_PROBE_REFUSED)
    if not raw.endswith(b"\n"):
        raise AssertionError(SAFE_PROBE_REFUSED)
    body = raw[:-1]
    if not body or b"\n" in body or b"\r" in body:
        raise AssertionError(SAFE_PROBE_REFUSED)
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        raise AssertionError(SAFE_PROBE_REFUSED) from None
    try:
        document = json.loads(
            text,
            object_pairs_hook=_no_duplicate_private_keys,
            parse_constant=_reject_private_constant,
        )
    except (TypeError, ValueError, RecursionError):
        raise AssertionError(SAFE_PROBE_REFUSED) from None
    if not isinstance(document, dict):
        raise AssertionError(SAFE_PROBE_REFUSED)
    try:
        canonical = _canonical_private_json(document)
    except AssertionError:
        raise
    if canonical != body:
        raise AssertionError(SAFE_PROBE_REFUSED)
    try:
        return probe_module.validate_response(document, expected)
    except Exception:
        raise AssertionError(SAFE_PROBE_REFUSED) from None


def _expected_revocation_state(arm: str, mode: str) -> tuple:
    if mode in {"baseline", "restored", "verify"}:
        # ``verify`` reports the separate verifier's facts, never the actor's.
        return True, True
    if mode in {"status_denial", "replay_denial"}:
        if arm == "disabled":
            return False, True
        if arm == "role_removed":
            return True, False
    raise AssertionError(SAFE_PROBE_REFUSED)


def _require_revocation_state(response: dict, arm: str, mode: str) -> None:
    enabled, clinician = _expected_revocation_state(arm, mode)
    if (
        response.get("result") != "observed"
        or response.get("clean") is not True
        or response.get("enabled") is not enabled
        or response.get("clinician") is not clinician
    ):
        raise AssertionError(SAFE_PROBE_REFUSED)


def _require_revocation_matches(response: dict, baseline: dict) -> None:
    if (
        response.get("evidence_digest") != baseline["evidence_digest"]
        or response.get("counts") != baseline["counts"]
    ):
        raise AssertionError(SAFE_PROBE_REFUSED)


def _signal_owned_child(process, signum) -> None:
    pid = getattr(process, "pid", None)
    if isinstance(pid, int) and pid > 0:
        try:
            if os.getpgid(pid) == pid:
                os.killpg(pid, signum)
                return
        except OSError:
            pass
    try:
        process.send_signal(signum)
    except Exception:
        return


def _wait_owned_child(process, seconds: float) -> bool:
    try:
        process.wait(timeout=seconds)
        return process.returncode is not None
    except subprocess.TimeoutExpired:
        return False
    except Exception:
        return False


def _close_child_pipes(process) -> None:
    for name in ("stdout", "stderr"):
        stream = getattr(process, name, None)
        if stream is None:
            continue
        try:
            stream.close()
        except Exception:
            pass


def _terminate_owned_child(process) -> bool:
    """Bounded TERM, bounded wait, bounded KILL and bounded reap; owns only this child."""
    _signal_owned_child(process, signal.SIGTERM)
    if _wait_owned_child(process, REVOCATION_TERM_GRACE):
        return True
    _signal_owned_child(process, signal.SIGKILL)
    return _wait_owned_child(process, REVOCATION_TERM_GRACE)


def _run_revocation_probe(document: dict):
    """Launch one fresh owned probe child and return (response, private baseline).

    The input is sealed read-only and unlinked before launch. The private baseline
    descriptor exists only for baseline/restored and is read only after a proven clean
    exit with a creditable result. Nothing else about the child is inherited.
    """
    from clinic_photos.tests import revocation_probe as probe_module

    if not isinstance(document, dict):
        raise AssertionError(SAFE_PROBE_REFUSED)
    wire = dict(document)
    request = wire.get("request")
    if isinstance(request, probe_module.SaveRequest):
        wire["request"] = probe_module.encode_request(request)
    payload = _canonical_private_json(wire)
    try:
        probe_module.decode_input(payload)
    except Exception:
        raise AssertionError(SAFE_PROBE_REFUSED) from None

    mode = wire["mode"]
    requires_baseline = mode in {"baseline", "restored"}
    control = _validate_control_directory(
        os.environ.get(REVOCATION_CONTROL_DIR_ENV)
    )
    stdin_descriptor = _sealed_input_descriptor(control, payload)
    baseline_file = None
    baseline_descriptor = None
    if requires_baseline:
        try:
            baseline_file = tempfile.TemporaryFile()
        except OSError:
            try:
                os.close(stdin_descriptor)
            except OSError:
                pass
            raise AssertionError(SAFE_TRANSPORT_REFUSED) from None
        baseline_descriptor = baseline_file.fileno()

    argv = [
        sys.executable,
        "-m",
        REVOCATION_PROBE_MODULE,
        "--site",
        wire["site"],
        "--operation-id",
        wire["operation_id"],
        "--mode",
        mode,
    ]
    environment = _probe_environment(baseline_descriptor)
    expected = _expected_revocation_bindings(document, wire, probe_module)
    try:
        try:
            process = subprocess.Popen(
                argv,
                cwd=SITES_PATH,
                stdin=stdin_descriptor,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
                pass_fds=(baseline_descriptor,) if requires_baseline else (),
                close_fds=True,
                start_new_session=True,
            )
        finally:
            try:
                os.close(stdin_descriptor)
            except OSError:
                pass
    except BaseException:
        if baseline_file is not None:
            try:
                baseline_file.close()
            except Exception:
                pass
        raise AssertionError(SAFE_PROBE_REFUSED) from None

    try:
        try:
            stdout, _stderr = process.communicate(timeout=REVOCATION_PROBE_TIMEOUT)
        except subprocess.TimeoutExpired:
            survived = _terminate_owned_child(process)
            _close_child_pipes(process)
            suffix = " (surviving child)" if not survived else " (timeout)"
            raise AssertionError(SAFE_PROBE_UNCERTAIN + suffix) from None
        except BaseException:
            _terminate_owned_child(process)
            _close_child_pipes(process)
            raise AssertionError(SAFE_PROBE_UNCERTAIN) from None
        if process.returncode is None:
            survived = _terminate_owned_child(process)
            _close_child_pipes(process)
            suffix = " (surviving child)" if not survived else " (uncertain exit)"
            raise AssertionError(SAFE_PROBE_UNCERTAIN + suffix) from None
        if process.returncode != 0:
            raise AssertionError(SAFE_PROBE_REFUSED)
        response = _parse_revocation_response(stdout, expected)
        # Credit the mode state and document-bound evidence before touching the
        # private baseline descriptor; a refused state never credits that read.
        _require_revocation_state(response, wire["arm"], mode)
        if wire.get("baseline") is not None:
            _require_revocation_matches(response, wire["baseline"])
        private = None
        if requires_baseline:
            private = _read_private_baseline_descriptor(baseline_descriptor)
            try:
                actual = probe_module.baseline_digest(private)
            except Exception:
                raise AssertionError(SAFE_BASELINE_REFUSED) from None
            if actual != response.get("baseline_digest"):
                raise AssertionError(SAFE_BASELINE_REFUSED)
        elif response.get("baseline_digest") is None:
            raise AssertionError(SAFE_BASELINE_REFUSED)
        if requires_baseline != (private is not None):
            raise AssertionError(SAFE_BASELINE_REFUSED)
        return response, private
    finally:
        if baseline_file is not None:
            try:
                baseline_file.close()
            except Exception:
                pass


def _require_hex(value, pattern, message):
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise AssertionError(message)
    return value


def _canonical_revocation_roles(roles) -> list:
    if not isinstance(roles, list):
        raise AssertionError(SAFE_ACTION_REFUSED)
    normalized = []
    for role in roles:
        if not isinstance(role, dict) or set(role) != _REVOCATION_ROLE_KEYS:
            raise AssertionError(SAFE_ACTION_REFUSED)
        row = {}
        for name in ("role", "parenttype", "parentfield"):
            value = role[name]
            if not isinstance(value, str) or not value or value.strip() != value:
                raise AssertionError(SAFE_ACTION_REFUSED)
            row[name] = value
        normalized.append(row)
    return sorted(
        normalized,
        key=lambda item: (item["role"], item["parenttype"], item["parentfield"]),
    )


def _validate_revocation_action_binding(binding: dict) -> dict:
    if not isinstance(binding, dict) or set(binding) != REVOCATION_ACTION_KEYS:
        raise AssertionError(SAFE_ACTION_REFUSED)
    schema = binding["schema"]
    if isinstance(schema, bool) or schema != 1:
        raise AssertionError(SAFE_ACTION_REFUSED)
    action = binding["action"]
    arm = binding["arm"]
    if action not in _REVOCATION_ACTIONS or arm not in REVOCATION_ARMS:
        raise AssertionError(SAFE_ACTION_REFUSED)
    if binding["site"] != APPROVED_SITE:
        raise AssertionError(SAFE_ACTION_REFUSED)
    actor = binding["actor"]
    if (
        not isinstance(actor, str)
        or not actor
        or len(actor) > 140
        or actor.strip() != actor
        or actor in {"Guest", "Administrator"}
    ):
        raise AssertionError(SAFE_ACTION_REFUSED)
    return {
        "schema": 1,
        "run_nonce": _require_hex(
            binding["run_nonce"], _REVOCATION_NONCE_PATTERN, SAFE_ACTION_REFUSED
        ),
        "action_nonce": _require_hex(
            binding["action_nonce"], _REVOCATION_NONCE_PATTERN, SAFE_ACTION_REFUSED
        ),
        "site": APPROVED_SITE,
        "source_digest": _require_hex(
            binding["source_digest"], _REVOCATION_SHA256_PATTERN,
            SAFE_ACTION_REFUSED,
        ),
        "operation_id": _require_hex(
            binding["operation_id"], _REVOCATION_NONCE_PATTERN, SAFE_ACTION_REFUSED
        ),
        "actor": actor,
        "arm": arm,
        "action": action,
        "baseline_digest": _require_hex(
            binding["baseline_digest"], _REVOCATION_SHA256_PATTERN,
            SAFE_ACTION_REFUSED,
        ),
        "baseline_roles": _canonical_revocation_roles(binding["baseline_roles"]),
    }


def _read_revocation_ack(path: str) -> dict:
    descriptor = None
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise AssertionError(SAFE_ACTION_REFUSED)
        if info.st_uid != os.geteuid():
            raise AssertionError(SAFE_ACTION_REFUSED)
        if stat.S_IMODE(info.st_mode) != 0o600:
            raise AssertionError(SAFE_ACTION_REFUSED)
        raw = _read_all(descriptor, REVOCATION_ACK_BYTES + 1)
    except AssertionError:
        raise
    except OSError:
        raise AssertionError(SAFE_ACTION_REFUSED) from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
    if len(raw) > REVOCATION_ACK_BYTES:
        raise AssertionError(SAFE_ACTION_REFUSED)
    return _parse_private_json(raw, REVOCATION_ACK_BYTES, SAFE_ACTION_REFUSED)


def _require_revocation_ack(binding: dict, ack: dict) -> None:
    if set(ack) != REVOCATION_ACK_KEYS:
        raise AssertionError(SAFE_ACTION_REFUSED)
    if ack["status"] != REVOCATION_ACTION_STATUS:
        raise AssertionError(SAFE_ACTION_REFUSED)
    for name in REVOCATION_ACTION_KEYS - {"baseline_roles"}:
        if ack.get(name) != binding[name]:
            raise AssertionError(SAFE_ACTION_REFUSED)


class _RevocationHandoffError(AssertionError):
    """Internal handoff outcome: fixed message plus local issuance evidence."""

    def __init__(self, message, *, issued):
        super().__init__(message)
        self.issued = bool(issued)


def _revocation_handoff_issued(error) -> bool:
    """Whether a change request was locally issued before this handoff failure.

    The marker is set only by the real await boundary after a complete request
    write. An unmarked/unknown failure is treated as issued, the conservative
    choice for fixture restoration; it is never a substitute for operator proof.
    """
    marker = getattr(error, "issued", None)
    if isinstance(marker, bool):
        return marker
    return True


def _await_revocation_action(binding: dict, *, deadline: float) -> None:
    """Write one exact request and consume one strict acknowledgement, or refuse."""
    state = {"issued": False}
    try:
        _await_revocation_action_inner(binding, deadline=deadline, state=state)
    except _RevocationHandoffError:
        raise
    except AssertionError as error:
        raise _RevocationHandoffError(str(error), issued=state["issued"]) from None


def _await_revocation_action_inner(binding: dict, *, deadline: float, state: dict) -> None:
    normalized = _validate_revocation_action_binding(binding)
    directory = _validate_control_directory(
        os.environ.get(REVOCATION_CONTROL_DIR_ENV)
    )
    if isinstance(deadline, bool) or not isinstance(deadline, (int, float)):
        raise AssertionError(SAFE_ACTION_REFUSED)
    if time.monotonic() >= deadline:
        raise AssertionError(SAFE_ACTION_TIMEOUT)

    nonce = normalized["action_nonce"]
    request_path = os.path.join(directory, nonce + ".request.json")
    ack_path = os.path.join(directory, nonce + ".ack.json")
    for path in (request_path, ack_path):
        if os.path.lexists(path):
            raise AssertionError(SAFE_ACTION_REFUSED)

    payload = _canonical_private_json(normalized)
    if len(payload) > REVOCATION_ACK_BYTES:
        raise AssertionError(SAFE_ACTION_REFUSED)
    descriptor = None
    created_identity = None
    try:
        descriptor = os.open(
            request_path,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        info = os.fstat(descriptor)
        created_identity = (info.st_dev, info.st_ino)
        os.fchmod(descriptor, 0o600)
        _write_all(descriptor, payload)
        # The complete request is now durably ours. This local receipt survives a
        # later timeout, an operator removing the consumed request, or a lost ack.
        state["issued"] = True
    except OSError:
        # A failed O_EXCL created nothing, so there is nothing to remove. A failure
        # after creation may clean up only the exact inode this call created; a
        # replaced pathname belongs to someone else and is left untouched.
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
            descriptor = None
        if created_identity is not None:
            try:
                current = os.lstat(request_path)
            except OSError:
                current = None
            if current is not None and (
                current.st_dev,
                current.st_ino,
            ) == created_identity:
                try:
                    os.unlink(request_path)
                except OSError:
                    pass
        raise AssertionError(SAFE_ACTION_REFUSED) from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass

    _emit_revocation_log(
        "revocation_wait "
        f"action_nonce={nonce} action={normalized['action']} arm={normalized['arm']}"
    )

    while True:
        now = time.monotonic()
        if now >= deadline:
            raise AssertionError(SAFE_ACTION_TIMEOUT)
        if os.path.lexists(ack_path):
            ack = _read_revocation_ack(ack_path)
            _require_revocation_ack(normalized, ack)
            return
        time.sleep(min(REVOCATION_ACK_POLL, max(0.0, deadline - now)))


def synthetic_jpeg_bytes(nonce: str, label: str) -> tuple:
    """Deterministic nonce-scoped JPEG bytes and their real ``(width, height)``.

    The content is derived from the runner nonce and this case's label, so two cases
    cannot share bytes by accident and a collision is never retried or merged. The
    bytes are a real JPEG: the request validator and the writer both read the format
    from the payload, never from a filename.
    """
    from PIL import Image

    seed = hashlib.sha256(f"{nonce}:{label}".encode("utf-8")).digest()
    palette = [
        (seed[index % 32], seed[(index + 7) % 32], seed[(index + 13) % 32])
        for index in range(16)
    ]
    width = height = 8
    image = Image.new("RGB", (width, height))
    image.putdata([palette[index % len(palette)] for index in range(width * height)])
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue(), width, height


def synthetic_annotation(nonce: str, label: str) -> dict:
    """One deterministic in-bounds annotation envelope for this nonce and label."""
    seed = hashlib.sha256(f"{nonce}:{label}".encode("utf-8")).hexdigest()
    return {
        "schema_version": 1,
        "coordinate_space": "normalized",
        "shapes": [
            {
                "id": f"s-{seed[:12]}",
                "type": "rectangle",
                "color": "#" + seed[12:18].upper(),
                "x": 0.1,
                "y": 0.1,
                "width": 0.2,
                "height": 0.2,
                "stroke_width": 0.01,
            }
        ],
    }


def request_key(nonce: str, label: str) -> str:
    """Nonce-scoped ASCII request key inside the contract's 16-128 character bound."""
    return f"case-{nonce}-{label}"


class PublicationNativeCase(unittest.TestCase):
    """Shared fixture/residue protocol for native publication cases.

    Nothing is created by this base class. A subclass appends every protected row it
    creates to ``created`` and every protected storage key to ``storage_keys`` so the
    class teardown can report them to the runner; ordinary deletion is deliberately
    not attempted here (that would be an ordinary immutable delete and the abort
    latch is sticky), so disposal stays an explicitly observed operator step.
    """

    created = []
    storage_keys = []
    # Set when a failure left residue that could not be inventoried completely. It makes
    # the class teardown fail rather than let a partial list read as a clean result.
    residue_incomplete = False

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.created = []
        cls.storage_keys = []
        cls.residue_incomplete = False
        cls._require_approved_site()

    @classmethod
    def _require_approved_site(cls):
        frappe = cls._framework()
        if getattr(frappe.local, "site", "") != APPROVED_SITE:
            raise AssertionError(
                f"native publication cases run only on {APPROVED_SITE!r}"
            )

    @staticmethod
    def _framework():
        import frappe

        return frappe

    @contextlib.contextmanager
    def acting_as(self, actor: str):
        """Run this block as ``actor`` and always restore the original session user.

        Switching the session is not a configuration change: it is the native way this
        case proves that the *current* server actor, not a client-supplied capability,
        is what the inspection guard reads. The original session is restored in
        ``finally``, including when the block raises.
        """
        frappe = self.frappe
        session = getattr(frappe, "session", None)
        original = getattr(session, "user", "") or getattr(self, "actor", "")
        frappe.set_user(actor)
        try:
            yield
        finally:
            frappe.set_user(original)

    def setUp(self):
        super().setUp()
        frappe = self._framework()
        if getattr(frappe.local, ABORT_ATTR, False):
            raise AssertionError(
                "stale execution: publication abort is already latched; select this "
                "case in a fresh native execution"
            )
        from clinic_photos.access import frappe_context

        if frappe_context.current_publication_owner() is not None:
            raise AssertionError(
                "stale execution: a publication owner is still registered"
            )
        if frappe_context.current_publication() is not None:
            raise AssertionError(
                "stale execution: a publication context is still bound"
            )
        self.frappe = frappe
        self.before_operations = self._operation_count()
        self.before_files = self._file_count()

    def _operation_count(self) -> int:
        return self.frappe.db.count("Clinic Photo Save Operation")

    def _file_count(self) -> int:
        return self.frappe.db.count("File")

    def assert_no_new_residue(self):
        """Refusal proofs must leave the durable counts exactly as they were.

        Honest limit: this is a *committed-count delta visible to this session*, not a
        proof of absence. An uncommitted child write, a stale snapshot or a child killed
        before commit would not show up here. Residue for any case that creates
        something is therefore reported through the residue marker protocol, not
        inferred from counts.
        """
        self.assertEqual(self._operation_count(), self.before_operations)
        self.assertEqual(self._file_count(), self.before_files)
        self.assertEqual(list(self.created), [])
        self.assertEqual(list(self.storage_keys), [])

    def observe_gate(self) -> str:
        """Record the observed publication-gate posture as harness-only evidence.

        This is a plain observation line in the raw output, deliberately *not* a
        ``CLINIC_PHOTOS_CASE_*`` marker: the marker protocol is unchanged. It exists so
        the branch the child actually took is attributable, and it makes no claim that
        the child exposes a distinguishing branch of its own.
        """
        conf = getattr(self.frappe, "conf", None)
        gate = "unknown"
        try:
            gate = "on" if conf and conf.get(PUBLICATION_GATE) is True else "off"
        except Exception:  # pragma: no cover - defensive, conf shape is native
            gate = "unknown"
        if not getattr(self.__class__, "_gate_observed", False):
            stream = sys.__stdout__
            if stream is not None and not getattr(stream, "closed", True):
                stream.write(
                    f"\n{HARNESS_OBSERVATION_PREFIX} publication_gate={gate} "
                    f"site={APPROVED_SITE}\n"
                )
                stream.flush()
            self.__class__._gate_observed = True
        return gate

    def require_bench_environment(self) -> None:
        """Fail with an attributable message when the venv is not the bench env."""
        prefix = str(sys.prefix)
        if not prefix.rstrip("/").endswith(BENCH_ENV.rstrip("/")):
            self.fail(
                "the publication child must run under the bench environment "
                f"({BENCH_ENV}); this process reports sys.prefix={prefix!r}. "
                "Run the case through `bench run-tests` in the bench venv."
            )

    def run_child(self, site: str, operation_id: str) -> subprocess.CompletedProcess:
        """Run the fixed publication child: bench venv, absolute sites cwd, fixed argv.

        This is the only supported publication entry. No test here calls the
        coordinator, the launcher or the queue in-process: an in-process call would
        not exercise the fresh-process bootstrap the design requires.
        """
        self.require_bench_environment()
        argv = [
            sys.executable,
            "-m",
            PROCESS_MODULE,
            "--site",
            site,
            "--operation-id",
            operation_id,
        ]
        return subprocess.run(
            argv,
            cwd=SITES_PATH,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=180,
            check=False,
        )

    def parse_status(self, completed: subprocess.CompletedProcess) -> dict:
        """Parse the bounded child outcome marker; reject anything unbounded."""
        output = completed.stdout or ""
        self.assertNotIn("Traceback", output)
        self.assertNotIn("clinic_photos.publication", output)
        self.assertNotIn("/private/files/", output)
        lines = [line for line in output.splitlines() if line.strip()]
        statuses = [line for line in lines if line.startswith("status=")]
        self.assertEqual(
            len(statuses), 1, f"expected exactly one bounded status line: {lines}"
        )
        self.assertLessEqual(
            len(lines), MAX_CHILD_OUTPUT_LINES, f"unbounded child output: {output!r}"
        )
        self.assertLessEqual(
            len(output.encode("utf-8")),
            MAX_CHILD_OUTPUT_BYTES,
            "unbounded child output size",
        )
        fields = dict(
            part.split("=", 1) for part in statuses[0].split(" ") if "=" in part
        )
        status = fields.get("status")
        self.assertIn(status, EXIT_CODES, statuses[0])
        self.assertEqual(completed.returncode, EXIT_CODES[status])
        if "operation_id" in fields:
            self.assertRegex(fields["operation_id"], OPERATION_ID_PATTERN)
        return fields

    # -- synthetic fixture lifecycle (external records, real submission seam) ------
    def require_native_prerequisites(self) -> tuple:
        """Verify the external synthetic fixture and installed schema before any write.

        Every observation is read-only: this method stages nothing, creates nothing and
        changes no configuration. A missing prerequisite fails the case with the fixed
        code list, so an unavailable native gate can never be read as a pass.
        """
        from clinic_photos.access import frappe_context

        self.require_bench_environment()
        frappe = self.frappe
        site = getattr(frappe.local, "site", "")
        conf = getattr(frappe, "conf", None) or {}

        actor = SYNTHETIC_CLINICIAN
        patient = SYNTHETIC_PATIENT
        actor_exists = bool(actor) and bool(frappe.db.exists("User", actor))
        if actor_exists:
            frappe.set_user(actor)
        fresh = frappe_context.current_actor()
        patient_exists = bool(patient) and bool(frappe.db.exists("Patient", patient))
        observed = {
            "site": site,
            "publication_gate": conf.get(PUBLICATION_GATE),
            "synthetic_mode": conf.get(SYNTHETIC_MODE),
            "bench_interpreter": str(sys.prefix).rstrip("/").endswith(
                BENCH_ENV.rstrip("/")
            ),
            "actor_exists": actor_exists,
            "actor_enabled": bool(fresh.enabled) and fresh.user == actor,
            "actor_clinician": bool(fresh.clinician),
            "patient_exists": patient_exists,
            "patient_read": patient_exists
            and bool(
                frappe.has_permission("Patient", "read", doc=patient, throw=False)
            ),
            "missing_operation_columns": missing_operation_columns(
                self._operation_columns()
            ),
        }
        failures = publication_precondition_failures(observed)
        if failures:
            self.fail(SAFE_PREREQUISITE.format(missing=", ".join(failures)))
        self.actor = actor
        self.patient = patient
        return actor, patient

    def _stored_user_name(self, supplied) -> str:
        """Resolve one supplied user identifier to its canonical stored ``User.name``.

        The native ``get_value`` lookup is authority; no lower/casefold guess is made.
        A missing, badly shaped or unreadable result returns ``""`` so the caller can
        fail with a fixed code that carries no identity diagnostic.
        """
        if not isinstance(supplied, str) or not supplied:
            return ""
        try:
            resolved = self.frappe.db.get_value("User", supplied, "name")
        except Exception:
            return ""
        return resolved if isinstance(resolved, str) and resolved else ""

    def _fail_observer_prerequisite(self, failures) -> None:
        """Fail with the fixed prerequisite codes and no identity diagnostic."""
        self.fail(
            "native cross-actor observer prerequisites are not in force on this "
            f"site ({', '.join(failures)}); no fixture was staged and this case "
            "must not be read as a pass"
        )

    def require_observer_prerequisite(self, author: str) -> str:
        """Verify the externally supplied second clinician before any fixture write.

        Read-only: the supplied author and observer are resolved to their canonical
        stored ``User.name`` values first, the session is switched only to measure the
        canonical observer's current classification and is always restored, and nothing
        is created, discovered or configured. A missing/noncanonical author, or a
        missing, shared, disabled, non-clinician or allowlisted observer, fails with
        fixed codes, so no fixture is staged for a case that could not attribute its
        denial to non-membership.
        """
        from clinic_photos.access import frappe_context
        from clinic_photos.publication import inspection

        self.require_bench_environment()
        frappe = self.frappe
        conf = getattr(frappe, "conf", None) or {}

        canonical_author = self._stored_user_name(author)
        if not canonical_author:
            self._fail_observer_prerequisite(["author_identity"])
        if canonical_author != author:
            self._fail_observer_prerequisite(["author_noncanonical"])

        observer = self._stored_user_name(SYNTHETIC_OBSERVER)
        observer_exists = bool(observer) and bool(frappe.db.exists("User", observer))
        observer_enabled = observer_clinician = False
        if observer_exists:
            with self.acting_as(observer):
                fresh = frappe_context.current_actor()
                observer_enabled = bool(fresh.enabled) and fresh.user == observer
                observer_clinician = bool(fresh.clinician)
        observed = {
            "author": canonical_author,
            "observer": observer,
            "observer_exists": observer_exists,
            "observer_enabled": observer_enabled,
            "observer_clinician": observer_clinician,
            "operator_allowlist": conf.get(
                inspection.RECOVERY_OPERATOR_CONFIG_KEY
            ),
        }
        failures = observer_precondition_failures(observed)
        if failures:
            self._fail_observer_prerequisite(failures)
        return observer

    def _operation_columns(self) -> list:
        """Installed column names of the operation table; empty when it is absent."""
        try:
            rows = self.frappe.db.sql(
                """
                select column_name from information_schema.columns
                where table_schema = database() and table_name = %s
                """,
                (f"tab{OPERATION_DOCTYPE}",),
            )
        except Exception:
            return []
        return list(rows or ())

    def synthetic_jpeg(self, label: str) -> tuple:
        return synthetic_jpeg_bytes(_case_nonce(), label)

    def original_request(self, label: str):
        """One validated synthetic-original request plus its exact decoded bytes."""
        from clinic_photos.publication import contracts

        data, _, _ = self.synthetic_jpeg(label)
        request = contracts.validate_request(
            {
                "kind": contracts.SYNTHETIC_ORIGINAL_CREATE,
                "request_key": request_key(_case_nonce(), label),
                "synthetic_patient": SYNTHETIC_PATIENT,
                "image_base64": base64.b64encode(data).decode("ascii"),
            }
        )
        return request, data

    def annotation_request(self, draft: str, expected_version: int, original: str, label: str):
        """One validated annotation-append request with the real canonical envelope."""
        from clinic_photos.publication import contracts

        return contracts.validate_request(
            {
                "kind": contracts.ANNOTATION_APPEND,
                "request_key": request_key(_case_nonce(), label),
                "draft": draft,
                "expected_version": expected_version,
                "original": original,
                "annotation": synthetic_annotation(_case_nonce(), label),
            }
        )

    def stage_and_commit(self, request) -> str:
        """Stage one SUBMITTED row through the real boundary, then commit it once.

        The submission seam is the same authenticated boundary the request entry uses;
        the commit is this test's own transaction end, so the committed row is exactly
        what the fixed child reads. No after-commit notification is registered here, so
        publication is launched only by ``run_child``. No DB read follows this commit
        until the child returns: the next read opens the new snapshot that contains the
        child's own commit.
        """
        from clinic_photos.publication import submission

        outcome = submission.stage_submission(request, self.actor)
        self.assertEqual(outcome.status, "pending", outcome.status)
        operation_id = outcome.operation_id
        # Registered at the moment the identity is known: an unreadable outcome later
        # must still leave the operation row inventoried.
        self.created.append((OPERATION_DOCTYPE, operation_id))
        try:
            self.frappe.db.commit()
        except BaseException as error:
            # A failed commit leaves the durable state unknown even when this transaction
            # still sees the row, so this branch always reports an incomplete inventory.
            entry = (OPERATION_DOCTYPE, operation_id)
            if entry not in self.created:
                self.created.append(entry)
            try:
                self.inventory_operation(operation_id)
            except BaseException:
                # The inventory must never replace the failure it is describing.
                pass
            self._mark_residue_incomplete()
            add_safe_note(error, SAFE_INVENTORY_UNCERTAIN)
            raise
        return operation_id

    def publish(self, operation_id: str):
        """Run the fixed child, require its completed status, load the committed outcome.

        ``completed`` is necessary and never sufficient: the returned evidence is read
        from the committed PUBLISHED row, and each case asserts the File, domain,
        version and byte identity itself. On any failure after the child was launched —
        timeout, refusal, malformed output or a failed readback — the rows and keys that
        operation can still prove are inventoried read-only, and the original failure is
        re-raised carrying the fixed uncertainty note. Nothing is retried or deleted.
        """
        try:
            completed = self.run_child(APPROVED_SITE, operation_id)
            fields = self.parse_status(completed)
            self.assertEqual(
                fields["status"],
                STATUS_COMPLETED,
                "publication did not complete; a refusal or an unavailable child is not "
                "evidence and is never retried or treated as absent",
            )
            from clinic_photos.publication import journal

            published = journal.load_published(operation_id)
        except BaseException as error:
            # The inventory call itself is guarded: an unexpected failure there must not
            # replace the original failure, lose the known identity or drop the
            # uncertainty signal. The default is uncertain.
            entry = (OPERATION_DOCTYPE, operation_id)
            if entry not in self.created:
                self.created.append(entry)
            uncertain = True
            try:
                uncertain = self.inventory_operation(operation_id)
            except BaseException:
                uncertain = True
            if uncertain:
                self._mark_residue_incomplete()
                add_safe_note(error, SAFE_INVENTORY_UNCERTAIN)
            raise
        self.record_residue(published)
        return published

    def _mark_residue_incomplete(self) -> None:
        """Flag this class's residue list as partial so teardown cannot pass."""
        type(self).residue_incomplete = True

    def inventory_operation(self, operation_id: str) -> bool:
        """Read-only failure inventory for one operation this case already staged.

        Returns ``True`` when the collected list cannot be treated as complete. Only
        exact identities are read: this opaque operation id, then the File, original and
        version rows that carry the one canonical reserved key that row reserved. Never a
        scan, never a byte read, never a write, lock, retry or state repair.
        """
        from clinic_photos.publication import journal

        entry = (OPERATION_DOCTYPE, operation_id)
        if entry not in self.created:
            self.created.append(entry)

        try:
            from clinic_photos.access import frappe_context

            row = frappe_context.get_save_operation(operation_id)
        except Exception:
            return True
        if row is None:
            # The identity is known but its durable row is not readable, so nothing below
            # can be settled and bytes may exist without it.
            return True
        if (row.get("actor") or "") != self.actor or (row.get("site") or "") != APPROVED_SITE:
            # A row that does not belong to this staged identity is not evidence about it.
            return True

        uncertain = False
        stored_key = row.get("reservation_key") or ""
        key = ""
        if stored_key:
            key = self._inventory_key(stored_key)
            if not key:
                uncertain = True
            elif key not in self.storage_keys:
                self.storage_keys.append(key)

        state = row.get("state") or ""
        if state == journal.SUBMITTED_STATE and not key:
            # No reservation exists at SUBMITTED, so no protected byte can exist yet.
            return uncertain

        found = set()
        if key:
            for doctype, fieldname in (
                ("File", "file_url"),
                (PHOTO_DOCTYPE, "storage_key"),
                (VERSION_DOCTYPE, "storage_key"),
            ):
                try:
                    names = list(
                        self.frappe.db.get_all(
                            doctype, filters={fieldname: key}, pluck="name", limit=0
                        )
                        or ()
                    )
                except Exception:
                    uncertain = True
                    continue
                if len(names) > 1:
                    # More than one row for this operation's own key is ambiguous, so the
                    # list stays partial even though every name is retained.
                    uncertain = True
                for name in names:
                    if name:
                        found.add((doctype, name))
                        if (doctype, name) not in self.created:
                            self.created.append((doctype, name))

        published_file = row.get("published_file") or ""
        if published_file and ("File", published_file) not in self.created:
            self.created.append(("File", published_file))

        domain_doctype = row.get("published_domain_doctype") or ""
        domain_name = row.get("published_domain_name") or ""
        if domain_doctype or domain_name:
            if domain_doctype in DOMAIN_INVENTORY_DOCTYPES and domain_name:
                if (domain_doctype, domain_name) not in self.created:
                    self.created.append((domain_doctype, domain_name))
            else:
                uncertain = True

        if state != journal.PUBLISHED_STATE:
            # PREPARED or an unknown child outcome: bytes may exist without a durable
            # outcome row, so the known list is explicitly partial.
            uncertain = True
        elif not (key and published_file and domain_name):
            uncertain = True
        elif ("File", published_file) not in found or (
            domain_doctype,
            domain_name,
        ) not in found:
            # A successful SELECT that returns nothing, or rows under other names, is not
            # evidence that this operation's own File and domain rows exist.
            uncertain = True
        return uncertain

    def _inventory_key(self, value) -> str:
        """One canonical reserved key, or "" when the stored value is not one."""
        from clinic_photos.access import storage

        if not isinstance(value, str) or not value:
            return ""
        try:
            key = storage.canonical_private_key(value)
        except storage.InvalidStoragePath:
            return ""
        if key != value or not storage.is_reserved_key(key):
            return ""
        return key

    def record_residue(self, published) -> None:
        """Inventory the committed domain row, File row and canonical key of an outcome."""
        entry = (published.domain_doctype, published.domain_name)
        if entry not in self.created:
            self.created.append(entry)
        file_entry = ("File", published.file_name)
        if file_entry not in self.created:
            self.created.append(file_entry)
        if published.reservation.key not in self.storage_keys:
            self.storage_keys.append(published.reservation.key)

    # -- Task 3: external operator handoff and two-arm case --------------------
    def run_revocation_probe(self, document: dict):
        """Run one fresh owned probe child and return its creditable evidence."""
        return _run_revocation_probe(document)

    def await_revocation_action(self, binding: dict, *, deadline: float) -> None:
        """Write one private action request and consume one strict acknowledgement."""
        return _await_revocation_action(binding, deadline=deadline)

    def revocation_source_digest(self) -> str:
        """The existing trusted source-root digest, including this probe module."""
        from clinic_photos.tests import run_isolated_cases

        root = pathlib.Path(__file__).resolve().parents[2]
        return run_isolated_cases.source_digest(root)

    @staticmethod
    def _revocation_fresh_nonce() -> str:
        return secrets.token_hex(16)

    def _revocation_document(
        self, *, run_nonce, source_digest, operation_id, verifier, arm, mode,
        baseline, request,
    ) -> dict:
        return {
            "schema": 1,
            "run_nonce": run_nonce,
            "invocation_nonce": self._revocation_fresh_nonce(),
            "site": APPROVED_SITE,
            "source_digest": source_digest,
            "operation_id": operation_id,
            "actor": self.actor,
            "verifier": verifier,
            "arm": arm,
            "mode": mode,
            "baseline": baseline,
            "request": request,
        }

    def _revocation_action_binding(
        self, *, run_nonce, source_digest, operation_id, arm, action,
        baseline_digest_value, baseline,
    ) -> dict:
        return {
            "schema": 1,
            "run_nonce": run_nonce,
            "action_nonce": self._revocation_fresh_nonce(),
            "site": APPROVED_SITE,
            "source_digest": source_digest,
            "operation_id": operation_id,
            "actor": self.actor,
            "arm": arm,
            "action": action,
            "baseline_digest": baseline_digest_value,
            "baseline_roles": [dict(role) for role in baseline["roles"]],
        }

    @staticmethod
    def _require_revocation_budget(case_deadline: float, needed: float) -> None:
        if time.monotonic() + needed > case_deadline:
            raise AssertionError(
                "revocation case deadline budget is insufficient before an action"
            )

    @staticmethod
    def _revocation_action_deadline(case_deadline: float) -> float:
        now = time.monotonic()
        return min(now + REVOCATION_ACTION_TIMEOUT, case_deadline - REVOCATION_PROBE_TIMEOUT)

    def _restore_revocation_arm(
        self, *, case_deadline, run_nonce, source_digest, operation_id,
        verifier, arm, baseline, baseline_digest_value,
    ) -> None:
        """One correlated external restore handoff plus a fresh saved-baseline proof."""
        from clinic_photos.tests import revocation_probe as probe_module

        self._require_revocation_budget(
            case_deadline, REVOCATION_ACTION_TIMEOUT + REVOCATION_PROBE_TIMEOUT
        )
        binding = self._revocation_action_binding(
            run_nonce=run_nonce,
            source_digest=source_digest,
            operation_id=operation_id,
            arm=arm,
            action="restore",
            baseline_digest_value=baseline_digest_value,
            baseline=baseline,
        )
        self.await_revocation_action(
            binding, deadline=self._revocation_action_deadline(case_deadline)
        )
        response, private = self.run_revocation_probe(
            self._revocation_document(
                run_nonce=run_nonce,
                source_digest=source_digest,
                operation_id=operation_id,
                verifier=verifier,
                arm=arm,
                mode="restored",
                baseline=baseline,
                request=None,
            )
        )
        # The fresh restored probe already required resolve_operation(...).status ==
        # 'saved' as the canonical actor before returning. Here the exact private
        # baseline (role assignments and committed evidence) must be equal again.
        _require_revocation_state(response, arm, "restored")
        _require_revocation_matches(response, baseline)
        if private is None:
            raise AssertionError(SAFE_RESTORE_INCOMPLETE)
        try:
            actual = probe_module.baseline_digest(private)
        except Exception:
            raise AssertionError(SAFE_RESTORE_INCOMPLETE) from None
        if actual != baseline_digest_value:
            raise AssertionError(SAFE_RESTORE_INCOMPLETE)

    def exercise_revocation_arm(self, arm: str) -> None:
        """Run one complete arm: original, change, denials, verifier and restoration."""
        from clinic_photos.tests import revocation_probe as probe_module

        if not isinstance(arm, str) or arm not in REVOCATION_ARMS:
            raise AssertionError(SAFE_CONTROL_REFUSED)
        _control, verifier = _require_revocation_configuration()
        run_nonce = _case_nonce()
        if _REVOCATION_NONCE_PATTERN.fullmatch(run_nonce or "") is None:
            raise AssertionError(
                "revocation cases require the isolated runner invocation nonce"
            )
        case_deadline = getattr(self, "_revocation_case_deadline", None)
        if case_deadline is None:
            case_deadline = time.monotonic() + REVOCATION_CASE_TIMEOUT
            self._revocation_case_deadline = case_deadline

        # Read-only prerequisites once for the case, then this arm's own real original
        # and publication. A second arm must not re-read the parent snapshot after the
        # first arm's external window; the canonical actor is bound for the case.
        if not getattr(self, "actor", None):
            self.require_native_prerequisites()
        request, _data = self.original_request(f"revocation-{arm}")
        operation_id = self.stage_and_commit(request)
        self.publish(operation_id)
        # Complete known residue inventory before the external change. An uncertain
        # or unreadable inventory stops here and never replaces its own failure: no
        # operator action is requested for an incomplete list.
        try:
            uncertain = self.inventory_operation(operation_id)
        except BaseException as error:
            self._mark_residue_incomplete()
            add_safe_note(error, SAFE_INVENTORY_UNCERTAIN)
            raise
        if uncertain:
            self._mark_residue_incomplete()
            raise AssertionError(SAFE_INVENTORY_UNCERTAIN)
        source_digest = self.revocation_source_digest()

        baseline_response, baseline = self.run_revocation_probe(
            self._revocation_document(
                run_nonce=run_nonce,
                source_digest=source_digest,
                operation_id=operation_id,
                verifier=verifier,
                arm=arm,
                mode="baseline",
                baseline=None,
                request=None,
            )
        )
        _require_revocation_state(baseline_response, arm, "baseline")
        if not isinstance(baseline, dict):
            raise AssertionError(SAFE_BASELINE_REFUSED)
        try:
            baseline_digest_value = probe_module.baseline_digest(baseline)
        except Exception:
            raise AssertionError(SAFE_BASELINE_REFUSED) from None

        # Reserve action, three fresh probes and the final restoration before issuing
        # the change; a short budget refuses rather than half-running the window.
        self._require_revocation_budget(
            case_deadline,
            REVOCATION_ACTION_TIMEOUT
            + 3 * REVOCATION_PROBE_TIMEOUT
            + REVOCATION_ACTION_TIMEOUT
            + REVOCATION_PROBE_TIMEOUT,
        )
        change = self._revocation_action_binding(
            run_nonce=run_nonce,
            source_digest=source_digest,
            operation_id=operation_id,
            arm=arm,
            action="change",
            baseline_digest_value=baseline_digest_value,
            baseline=baseline,
        )
        issued = False
        primary = None
        restoration_error = None
        try:
            try:
                self.await_revocation_action(
                    change,
                    deadline=self._revocation_action_deadline(case_deadline),
                )
            except BaseException as error:
                # A lost acknowledgement is still an issued change: the real await
                # boundary carries a local receipt that survives request removal.
                # Unknown handoff outcomes are treated as issued (conservative).
                issued = _revocation_handoff_issued(error)
                raise
            issued = True

            status_response, status_private = self.run_revocation_probe(
                self._revocation_document(
                    run_nonce=run_nonce,
                    source_digest=source_digest,
                    operation_id=operation_id,
                    verifier=verifier,
                    arm=arm,
                    mode="status_denial",
                    baseline=baseline,
                    request=None,
                )
            )
            _require_revocation_state(status_response, arm, "status_denial")
            _require_revocation_matches(status_response, baseline)
            if status_private is not None:
                raise AssertionError(SAFE_PROBE_REFUSED)

            replay_response, replay_private = self.run_revocation_probe(
                self._revocation_document(
                    run_nonce=run_nonce,
                    source_digest=source_digest,
                    operation_id=operation_id,
                    verifier=verifier,
                    arm=arm,
                    mode="replay_denial",
                    baseline=baseline,
                    request=request,
                )
            )
            _require_revocation_state(replay_response, arm, "replay_denial")
            _require_revocation_matches(replay_response, baseline)
            if replay_private is not None:
                raise AssertionError(SAFE_PROBE_REFUSED)

            verify_response, verify_private = self.run_revocation_probe(
                self._revocation_document(
                    run_nonce=run_nonce,
                    source_digest=source_digest,
                    operation_id=operation_id,
                    verifier=verifier,
                    arm=arm,
                    mode="verify",
                    baseline=baseline,
                    request=None,
                )
            )
            # The verifier's own enabled/clinician facts are the only state reported
            # in this mode; the disabled/revoked actor is never consulted here.
            _require_revocation_state(verify_response, arm, "verify")
            _require_revocation_matches(verify_response, baseline)
            if verify_private is not None:
                raise AssertionError(SAFE_PROBE_REFUSED)
        except BaseException as error:
            primary = error
            # Only an issued change can leave the protected state uncertain; a
            # pre-issuance refusal never invents an incomplete residue list.
            if issued:
                self._mark_residue_incomplete()
        finally:
            if issued:
                try:
                    self._restore_revocation_arm(
                        case_deadline=case_deadline,
                        run_nonce=run_nonce,
                        source_digest=source_digest,
                        operation_id=operation_id,
                        verifier=verifier,
                        arm=arm,
                        baseline=baseline,
                        baseline_digest_value=baseline_digest_value,
                    )
                except BaseException as error:
                    restoration_error = error
        if primary is not None:
            if restoration_error is not None:
                add_safe_note(primary, SAFE_RESTORE_INCOMPLETE)
            raise primary
        if restoration_error is not None:
            # Restoration-only uncertainty keeps its own fixed code, marks the
            # residue incomplete and blocks the next arm. Raw diagnostics are not
            # chained into the public failure.
            self._mark_residue_incomplete()
            raise AssertionError(SAFE_RESTORE_INCOMPLETE) from None

    @classmethod
    def tearDownClass(cls):
        # Residue is reported, never deleted: the runner refuses acceptance until an
        # authorized, observed disposal of exactly this list is confirmed. The frozen
        # marker payload is unchanged; uncertainty is surfaced through the existing
        # harness observation channel and a failing teardown, never a new marker field.
        payload = {
            "id": f"{__name__}.{cls.__name__}",
            "nonce": _case_nonce(),
            "rows": [list(row) for row in getattr(cls, "created", [])],
            "storage_keys": list(getattr(cls, "storage_keys", [])),
        }
        incomplete = bool(getattr(cls, "residue_incomplete", False))
        try:
            _emit(RESIDUE_MARKER, payload)
            if incomplete:
                stream = sys.__stdout__
                if stream is not None and not getattr(stream, "closed", True):
                    stream.write(
                        f"\n{HARNESS_OBSERVATION_PREFIX} residue inventory incomplete: "
                        "the reported rows and storage keys are a partial list\n"
                    )
                    stream.flush()
        finally:
            super().tearDownClass()
        if incomplete:
            raise AssertionError(SAFE_INVENTORY_UNCERTAIN)


class PublicationProcessEntryCases(PublicationNativeCase):
    """Executable cases for the fixed fresh-child publication entry.

    These are the cases that can run today with no protected fixture at all. They
    prove the entry's *shape* (fixed argv, bench venv, sites cwd, allowlisted site),
    its definite refusals and its bounded PHI-free output. They do not prove
    publication, actor adoption, journal lookup, coordinator behaviour or any saved
    outcome - output-format and refusal coverage is not publication coverage - and
    the publication acceptance surface stays unimplemented below.
    """

    @_case
    def test_child_refuses_unknown_operation_without_residue(self):
        gate = self.observe_gate()
        completed = self.run_child(APPROVED_SITE, "0" * 32)
        fields = self.parse_status(completed)
        # A definite refusal, exit 2. ``unavailable`` is deliberately NOT accepted: it
        # also covers a missing coordinator or a swallowed child exception, so accepting
        # it would let a broken child look like a pass.
        #
        # Honest scope: this proves a bounded refusal at one of the entry guards (site,
        # gate, identity/journal), not specifically the operation lookup - with the gate
        # off the child refuses before loading the operation. The observed gate posture
        # ("on"/"off"/"unknown") is recorded above in the run output so the branch the
        # child actually took stays attributable.
        self.assertIn(gate, ("on", "off", "unknown"))
        self.assertEqual(fields["status"], STATUS_REFUSED)
        self.assertEqual(completed.returncode, 2)
        self.assert_no_new_residue()

    @_case
    def test_child_binds_the_approved_site_and_ignores_no_other(self):
        # A non-approved site is refused before any framework connection, and the
        # approved site's durable state is untouched by the attempt.
        completed = self.run_child("other-site.invalid", "0" * 32)
        fields = self.parse_status(completed)
        self.assertEqual(fields["status"], STATUS_REFUSED)
        self.assertEqual(completed.returncode, 2)
        self.assert_no_new_residue()

    @_case
    def test_child_rejects_client_supplied_argv_shapes(self):
        # The entry accepts exactly ``--site <site> --operation-id <id>`` in that
        # order and nothing else; a client cannot add a payload, actor or path.
        self.require_bench_environment()
        shapes = [
            [sys.executable, "-m", PROCESS_MODULE],
            [sys.executable, "-m", PROCESS_MODULE, "--site", APPROVED_SITE],
            [
                sys.executable,
                "-m",
                PROCESS_MODULE,
                "--operation-id",
                "0" * 32,
                "--site",
                APPROVED_SITE,
            ],
            [sys.executable, "-m", PROCESS_MODULE, "--site", APPROVED_SITE,
             "--operation-id", "NOT-A-VALID-ID"],
        ]
        for argv in shapes:
            with self.subTest(argv=argv[3:]):
                completed = subprocess.run(
                    argv,
                    cwd=SITES_PATH,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    timeout=60,
                    check=False,
                )
                output = completed.stdout or ""
                self.assertIn("status=refused", output)
                self.assertEqual(completed.returncode, 2)
                self.assertNotIn("Traceback", output)
        self.assert_no_new_residue()

    @_case
    def test_child_output_is_phifree_and_never_claims_saved(self):
        # Bounded output only: a refusal never claims a saved outcome, never prints
        # an actor, payload, storage path or traceback, and the exit code is derived
        # from the single status word. A definite refusal (exit 2) is required; a
        # broken/unavailable child is a failure, not coverage.
        self.observe_gate()
        completed = self.run_child(APPROVED_SITE, "f" * 32)
        fields = self.parse_status(completed)
        self.assertEqual(fields["status"], STATUS_REFUSED)
        self.assertEqual(completed.returncode, 2)
        output = completed.stdout or ""
        for forbidden in ("saved", "actor=", "payload", "patient", "@"):
            self.assertNotIn(forbidden, output.lower())
        self.assert_no_new_residue()



class PublicationSchemaNativeCases(PublicationNativeCase):
    """Blocked native cases for the Task 6 reservation schema itself."""

    # Unit proof: test_async_publication_journal reservation/dedup tests.
    @unittest.skip(
            "[task6-schema-sync] Reservation columns and indexes are declared only in "
            "the reviewed app JSON and are not installed; a narrow reload_doc sync is "
            "a separate parent-authorized window."
    )
    def test_reservation_null_semantics_and_unique_index(self):
        self.fail("test_reservation_null_semantics_and_unique_index has not run")

    # Unit proof: test_async_publication_boundaries set_value posture tests.
    @unittest.skip(
            "[task6-schema-sync] The set_value hook/commit posture needs the synced "
            "reservation columns and a second live connection."
    )
    def test_set_value_has_no_hooks_and_no_commit(self):
        self.fail("test_set_value_has_no_hooks_and_no_commit has not run")


class PublicationOriginalNativeCases(PublicationNativeCase):
    """Native cases for the first synthetic JPEG original (Task 7).

    ``test_first_jpeg_original_uses_protected_mro_and_get_content`` is authored and
    selectable; it has not been executed in this pass. The rollback-residue case still
    needs a bound writer transaction the test may not manufacture, so it stays blocked.
    """

    # Unit proof: test_file_policy protected MRO / get_content coverage.
    # Native prerequisites: installed Task-6 operation schema, both gates exactly True,
    # an enabled synthetic clinician and an existing synthetic Patient on the disposable
    # site, the bench interpreter, and an empty native write-hook map.
    @_case
    def test_first_jpeg_original_uses_protected_mro_and_get_content(self):
        from clinic_photos.access.file_mixin import ProtectedFileMixin
        from clinic_photos.publication import inspection, writer

        actor, patient = self.require_native_prerequisites()
        request, data = self.original_request("orig")
        # A real JPEG, never a PNG under a .jpg name: the validator and the writer both
        # read the format from these exact bytes.
        self.assertEqual(data[:3], b"\xff\xd8\xff")
        digest = hashlib.sha256(data).hexdigest()

        operation_id = self.stage_and_commit(request)
        published = self.publish(operation_id)

        self.assertEqual(published.reservation.kind, request.kind)
        self.assertEqual(published.reservation.actor, actor)
        self.assertEqual(published.reservation.expected_sha256, digest)
        self.assertEqual(published.reservation.expected_size, len(data))
        self.assertTrue(published.reservation.key.endswith(".jpg"))
        self.assertIsNone(published.version)

        photo = self.frappe.get_doc(PHOTO_DOCTYPE, published.domain_name)
        self.assertEqual(photo.patient_reference, patient)
        self.assertEqual(photo.storage_key, published.reservation.key)
        self.assertEqual(photo.original_file, published.file_name)
        self.assertEqual(photo.original_sha256, digest)
        self.assertEqual((photo.original_width, photo.original_height), (8, 8))

        # The persisted File is the installed class extended by the registered mixin.
        # Pinned Frappe 16.53 loads a disk-backed File with ``content = b""``; this suite
        # injected no truthy content, so the bytes below come from disk through the
        # protected read path rather than a cached value.
        file_doc = self.frappe.get_doc("File", published.file_name)
        self.assertIsInstance(file_doc, ProtectedFileMixin)
        self.assertFalse(
            has_injected_content(file_doc),
            "content must not be injected; the native default is b''",
        )
        self.assertEqual(file_doc.file_url, published.reservation.key)
        self.assertEqual(int(file_doc.is_private), 1)
        self.assertEqual(file_doc.get_content(), data)

        # A normal status read claims ``saved`` only from the actual byte identity, not
        # from the child's exit word.
        resolved = inspection.resolve_operation(operation_id, actor=actor)
        self.assertEqual(resolved.status, "saved")
        self.assertEqual(resolved.operation_id, operation_id)
        self.assertEqual(resolved.file_name, published.file_name)
        self.assertIsNone(resolved.version)
        self.assertEqual(writer.verify_committed_object(published), digest)

    # Unit proof: test_async_publication_writer hook and rollback residue tests.
    @unittest.skip(
            "[writer-transaction] Native hook and on_rollback residue observation "
            "needs a bound writer transaction produced by the fresh child."
    )
    def test_native_hooks_and_rollback_residue_observed(self):
        self.fail("test_native_hooks_and_rollback_residue_observed has not run")


class PublicationReservationNativeCases(PublicationNativeCase):
    """Blocked native cases for the PREPARED reservation / alias / rollback floor.

    Selector contract for the parent's native lane, one case per isolated execution
    (a protected denial latches this app's abort for the whole execution, and the
    latch is never cleared here):

        bench --site clinic-photo-accept.local run-tests \
          --app clinic_photos --module clinic_photos.tests.test_publication \
          --case PublicationReservationNativeCases --test <bare_method>

    Every case in this module's blocked classes is a **placeholder**, not an
    implemented publication case: the body is ``self.fail(...)`` with no writer call, no
    coordinator observation and no assertion, and the class emits no BEGIN/END or residue
    provenance because it never runs. The skip text is the manifest's canonical
    ``blocked_reason`` verbatim (pinned by ``tests/unit/test_case_runner.py``). Runtime
    prerequisites explain why no native execution has happened; they do not mean the
    assertion logic could not be written. Task 10 therefore contributes entry-contract
    coverage only, and publication acceptance remains unimplemented.
    """

    # Unit proof: test_async_publication_boundaries.PreparedReservationTests.test_prepared_reservation_survives_publication_rollback.
    @unittest.skip(
            "[prepared-reservation] A committed PREPARED reservation needs the "
            "installed Task-6 schema and one enabled synthetic clinician."
    )
    def test_prepared_reservation_survives_rollback(self):
        self.fail("test_prepared_reservation_survives_rollback has not run")

    # Unit proof: test_native_dedup_retarget_before_insert_refused.
    @unittest.skip(
            "[prepared-reservation] A reserved destination exists only after a "
            "committed PREPARED reservation."
    )
    def test_dedup_retarget_reserved_refused(self):
        self.fail("test_dedup_retarget_reserved_refused has not run")

    # Unit proof: test_native_registered_alias_refused.
    @unittest.skip(
            "[published-object] A registered non-reserved protected destination needs "
            "one already committed protected record."
    )
    def test_dedup_retarget_registered_refused(self):
        self.fail("test_dedup_retarget_registered_refused has not run")

    # Unit proof: test_native_dedup_retarget_then_rollback_preserves_original, test_post_insert_identity_branches_refuse.
    @unittest.skip(
            "[writer-transaction] A post-insert retarget needs a real completed "
            "native insert inside the writer transaction to roll back."
    )
    def test_post_insert_retarget_requires_rollback(self):
        self.fail("test_post_insert_retarget_requires_rollback has not run")

    # Unit proof: test_native_rollback_preserves_protected_residue, test_original_path_outside_trusted_roots_is_unknown.
    @unittest.skip(
            "[writer-transaction] A real shutil.move compensation needs the writer "
            "and a live owner from the fresh child."
    )
    def test_original_path_rollback_guarded(self):
        self.fail("test_original_path_rollback_guarded has not run")

    # Unit proof: test_native_rollback_preserves_protected_residue.
    @unittest.skip(
            "[writer-transaction] An unsaved File exists only inside a real bound "
            "publication transaction."
    )
    def test_unsaved_file_rollback_guarded(self):
        self.fail("test_unsaved_file_rollback_guarded has not run")

    # Unit proof: test_protected_cleanup_refuses_committed_records_under_live_owner, ProtectedCleanupBoundaryTests.
    @unittest.skip(
            "[published-object] A committed protected record and its bytes are "
            "needed, plus DocPerm/root teardown observation."
    )
    def test_administrator_delete_refused(self):
        self.fail("test_administrator_delete_refused has not run")


class PublicationAtomicityNativeCases(PublicationNativeCase):
    """Blocked native cases for the Task 8 two-phase coordinator boundary."""

    # Unit proof: test_async_publication_coordinator two-phase tests.
    @unittest.skip(
            "[two-phase-fixtures] Two-phase atomicity needs a committed SUBMITTED "
            "operation, a PREPARED commit and a publication transaction."
    )
    def test_prepared_commit_then_publication_is_atomic(self):
        self.fail("test_prepared_commit_then_publication_is_atomic has not run")

    # Unit proof: test_async_publication_coordinator same-owner tests.
    @unittest.skip(
            "[published-object] Same-owner post-commit verification needs one "
            "committed published object and a live exclusion owner."
    )
    def test_same_owner_rechecks_after_commit(self):
        self.fail("test_same_owner_rechecks_after_commit has not run")

    # Unit proof: test_async_publication_coordinator revocation tests.
    @unittest.skip(
            "[native-user-permission] Revocation between phases needs a clinician "
            "whose enablement or permissions an authorized operator changes."
    )
    def test_revocation_between_phases_blocks_publication(self):
        self.fail("test_revocation_between_phases_blocks_publication has not run")

    # Unit proof: test_async_publication_coordinator unknown-outcome tests.
    @unittest.skip(
            "[fault-seam] An unknown commit outcome needs a controllable commit-path "
            "fault; no runtime fault hook exists and patching installed production "
            "code is out of scope."
    )
    def test_unknown_commit_outcome_is_not_retried(self):
        self.fail("test_unknown_commit_outcome_is_not_retried has not run")

    # Unit proof: test_async_publication_recovery duplicate-launch/non-resume tests.
    @unittest.skip(
            "[prepared-reservation] Duplicate launch and PREPARED non-resume need one "
            "PREPARED operation with a durable reservation."
    )
    def test_duplicate_launch_does_not_resume_prepared(self):
        self.fail("test_duplicate_launch_does_not_resume_prepared has not run")


class PublicationAccessNativeCases(PublicationNativeCase):
    """Native cases for the Task 9 current-access and replay boundary.

    Three cases are authored and selectable; none has been executed in this pass, so
    their native status is UNRUN. The operator-allowlist case uses a second externally
    supplied synthetic clinician and the site's actual (normally empty) allowlist. The
    revoked-actor case now covers both external-operator arms but remains UNRUN; the
    File-veto case still needs a configuration change an authorized operator owns, so
    it stays blocked.
    """

    # Unit proof: test_async_publication_recovery replay tests.
    # Native prerequisites: two committed published annotation versions on one draft, the
    # same Task-6 schema/gates/synthetic fixture as the original case.
    @_case
    def test_replay_after_newer_version_returns_immutable_version(self):
        from clinic_photos.publication import inspection, journal, submission, writer

        actor, patient = self.require_native_prerequisites()
        original_request, data = self.original_request("base")
        original_id = self.stage_and_commit(original_request)
        original = self.publish(original_id)
        original_digest = hashlib.sha256(data).hexdigest()

        # Native draft creation by its own controller: the session actor becomes the
        # immutable author and the version pointers start empty.
        draft = self.frappe.get_doc(
            {"doctype": DRAFT_DOCTYPE, "photo": original.domain_name}
        )
        draft.insert()
        self.created.append((DRAFT_DOCTYPE, draft.name))
        self.assertEqual(int(draft.current_version_number), 0)

        first_request = self.annotation_request(
            draft.name, 0, original.domain_name, "v1"
        )
        first_id = self.stage_and_commit(first_request)
        first = self.publish(first_id)
        self.assertEqual(first.version, 1)

        second_request = self.annotation_request(
            draft.name, 1, original.domain_name, "v2"
        )
        second_id = self.stage_and_commit(second_request)
        second = self.publish(second_id)
        self.assertEqual(second.version, 2)

        draft.reload()
        self.assertEqual(int(draft.current_version_number), 2)
        self.assertEqual(draft.current_version, f"{draft.name}-v2")

        first_row = self.frappe.get_doc(
            VERSION_DOCTYPE, f"{draft.name}-v1"
        )
        second_row = self.frappe.get_doc(
            VERSION_DOCTYPE, f"{draft.name}-v2"
        )
        self.assertEqual(first_row.state, "committed")
        self.assertEqual(first_row.annotation_file, first.file_name)
        self.assertEqual(first_row.storage_key, first.reservation.key)
        self.assertEqual(second_row.annotation_file, second.file_name)
        self.assertNotEqual(first_row.storage_key, second_row.storage_key)
        self.assertEqual(first.reservation.draft, draft.name)
        self.assertEqual(first.reservation.target_identity, "draft:" + draft.name)

        # Replaying the version-N tuple after N+1 resolves the immutable version N; it is
        # not a new append and never resolves the latest version. The File counts of the
        # three exact keys are captured before the replay so a stray File row cannot hide.
        keys = (
            original.reservation.key,
            first.reservation.key,
            second.reservation.key,
        )
        file_counts_before = [
            self.frappe.db.count("File", {"file_url": key}) for key in keys
        ]
        self.assertEqual(file_counts_before, [1, 1, 1])

        replay = submission.stage_submission(first_request, actor)
        self.assertEqual(replay.status, "saved")
        self.assertEqual(replay.operation_id, first_id)
        self.assertEqual(replay.version, 1)
        self.assertEqual(replay.file_name, first.file_name)

        resolved = inspection.resolve_operation(first_id, actor=actor)
        self.assertEqual(resolved.status, "saved")
        self.assertEqual(resolved.operation_id, first_id)
        self.assertEqual(resolved.version, 1)

        # Nothing was appended by the replay, and no earlier object changed.
        self.assertEqual(
            [self.frappe.db.count("File", {"file_url": key}) for key in keys],
            file_counts_before,
        )
        self.assertEqual(
            self.frappe.db.count(VERSION_DOCTYPE, {"draft": draft.name}), 2
        )
        self.assertEqual(
            self.frappe.db.count(
                OPERATION_DOCTYPE, {"request_key": first_request.request_key}
            ),
            1,
        )
        # Actual byte identity of the original and of BOTH immutable versions: the
        # committed object named by each outcome is re-opened and re-hashed.
        self.assertEqual(writer.verify_committed_object(original), original_digest)
        self.assertEqual(
            writer.verify_committed_object(first), first.reservation.expected_sha256
        )
        self.assertEqual(
            writer.verify_committed_object(second), second.reservation.expected_sha256
        )
        original_row = self.frappe.get_doc(PHOTO_DOCTYPE, original.domain_name)
        self.assertEqual(original_row.patient_reference, patient)
        self.assertEqual(original_row.original_file, original.file_name)
        self.assertEqual(original_row.original_sha256, original_digest)
        self.assertEqual(journal.load_published(original_id), original)
        self.assertEqual(journal.load_published(first_id), first)
        self.assertEqual(journal.load_published(second_id), second)

    # Unit proof: test_async_publication_submission lost-response tests.
    # Native prerequisites: one committed published original plus the same Task-6
    # schema/gates/synthetic fixture.
    @_case
    def test_lost_response_replay_resolves_original_tuple(self):
        from clinic_photos.publication import inspection, journal, submission

        actor, patient = self.require_native_prerequisites()
        request, data = self.original_request("lost")
        digest = hashlib.sha256(data).hexdigest()
        operation_id = self.stage_and_commit(request)
        published = self.publish(operation_id)

        # The client dropped the successful response and retries the identical tuple.
        # This models response loss only; it is not an unknown commit acknowledgement.
        replay = submission.stage_submission(request, actor)
        self.assertEqual(replay.status, "saved")
        self.assertEqual(replay.operation_id, operation_id)
        self.assertEqual(replay.file_name, published.file_name)
        self.assertIsNone(replay.version)

        self.assertEqual(
            self.frappe.db.count(
                OPERATION_DOCTYPE, {"request_key": request.request_key}
            ),
            1,
        )
        self.assertEqual(
            self.frappe.db.count(
                PHOTO_DOCTYPE, {"storage_key": published.reservation.key}
            ),
            1,
        )
        self.assertEqual(
            self.frappe.db.count("File", {"file_url": published.reservation.key}), 1
        )

        photo = self.frappe.get_doc(PHOTO_DOCTYPE, published.domain_name)
        self.assertEqual(photo.patient_reference, patient)
        self.assertEqual(photo.original_file, published.file_name)
        self.assertEqual(photo.original_sha256, digest)

        resolved = inspection.resolve_operation(operation_id, actor=actor)
        self.assertEqual(resolved.status, "saved")
        self.assertEqual(resolved.operation_id, operation_id)
        # The original operation tuple is still the one that published this object.
        self.assertEqual(journal.load_published(operation_id), published)

    # Unit proof: test_async_publication_recovery operator-allowlist tests.
    # Native prerequisites: the same Task-6 schema/gates/synthetic fixture plus a
    # second externally supplied synthetic clinician who is not an operator.
    @_case
    def test_operator_allowlist_cross_actor_audit_is_denied(self):
        from clinic_photos.access import frappe_context
        from clinic_photos.publication import inspection, journal, writer

        actor, _ = self.require_native_prerequisites()
        observer = self.require_observer_prerequisite(actor)
        self.assertNotEqual(observer, actor)

        request, data = self.original_request("audit")
        digest = hashlib.sha256(data).hexdigest()
        operation_id = self.stage_and_commit(request)
        published = self.publish(operation_id)

        # Snapshot the committed evidence as the author: the operation identity, its
        # byte identity and the durable counts the denial must leave untouched.
        before_row = frappe_context.get_save_operation(operation_id)
        self.assertEqual(before_row.get("actor"), actor)
        # The committed actor is the canonical author and must not be the canonical
        # observer, or this would not be a cross-actor audit.
        self.assertNotEqual(before_row.get("actor"), observer)
        self.assertEqual(before_row.get("state"), journal.PUBLISHED_STATE)
        before_counts = (
            self._operation_count(),
            self._file_count(),
            self.frappe.db.count(PHOTO_DOCTYPE),
            self.frappe.db.count(VERSION_DOCTYPE),
        )
        self.assertEqual(writer.verify_committed_object(published), digest)

        with self.acting_as(observer):
            # The current clinician check must succeed first; a failure here would be a
            # missing clinical prerequisite, not the non-membership denial under test.
            fresh = inspection.require_current_clinician(observer)
            self.assertEqual(fresh.user, observer)
            self.assertTrue(fresh.enabled)
            self.assertTrue(fresh.clinician)
            self.assertIs(self.frappe.conf.get(PUBLICATION_GATE), True)
            self.assertEqual(getattr(self.frappe.local, "site", ""), APPROVED_SITE)
            with self.assertRaises(inspection.InspectionDenied) as caught:
                inspection.inspect_operation(operation_id, actor=observer)
        self.assertEqual(str(caught.exception), inspection.SAFE_INSPECTION_DENIED)
        # The helper restored the author's session in ``finally``; every ordinary
        # readback below therefore runs as the operation author again.
        self.assertEqual(getattr(self.frappe.session, "user", ""), actor)

        self.assertEqual(
            (
                self._operation_count(),
                self._file_count(),
                self.frappe.db.count(PHOTO_DOCTYPE),
                self.frappe.db.count(VERSION_DOCTYPE),
            ),
            before_counts,
        )
        self.assertEqual(
            frappe_context.get_save_operation(operation_id), before_row
        )
        self.assertEqual(journal.load_published(operation_id), published)
        self.assertEqual(writer.verify_committed_object(published), digest)

    # Unit proof: test_async_publication_recovery revoked-actor tests.
    @_case
    def test_disabled_or_revoked_actor_cannot_replay(self):
        # Both arms run sequentially and independently. Each arm creates its own
        # original/publication and baseline; no partial arm is promoted. These are two
        # explicit calls, not a subTest loop: a failure in the first arm stops the case
        # before the second arm can start.
        self._revocation_case_deadline = time.monotonic() + REVOCATION_CASE_TIMEOUT
        self.exercise_revocation_arm("disabled")
        self.exercise_revocation_arm("role_removed")

    # Unit proof: test_async_publication_recovery File-veto tests.
    @unittest.skip(
            "[published-object] The native File veto needs a committed protected File "
            "plus a native permission decision to veto it."
    )
    def test_native_file_veto_denies_replay(self):
        self.fail("test_native_file_veto_denies_replay has not run")


class PublicationEntryDiagnostic(unittest.TestCase):
    def setUp(self):
        if os.environ.get(DIAGNOSTIC_ENV) != "1":
            self.skipTest("bounded native entry diagnostic is not authorized")

        import frappe

        if getattr(frappe.local, "site", "") != APPROVED_SITE:
            self.skipTest("not the approved disposable site")
        self.frappe = frappe

    def test_default_off_entry_refuses_without_http_request(self):
        from clinic_photos.api import photo_save

        # This diagnostic intentionally runs outside an HTTP request. If a request
        # is present, this body is not the no-request refusal it claims to prove.
        self.assertIsNone(getattr(self.frappe.local, "request", None))
        self.assertFalse(photo_save.publication_enabled())

        before = self.frappe.db.count("Clinic Photo Save Operation")
        payload = {
            "kind": "annotation_append",
            "request_key": "native-diagnostic-no-request-01",
            "draft": "DRAFT-NATIVE-DIAGNOSTIC",
            "expected_version": 0,
            "original": "PHOTO-NATIVE-DIAGNOSTIC",
            "annotation": {
                "schema_version": 1,
                "coordinate_space": "normalized",
                "shapes": [],
            },
        }

        with self.assertRaises(self.frappe.PermissionError) as save_error:
            photo_save.save(payload)
        self.assertEqual(str(save_error.exception), photo_save.SAFE_DENIAL)

        with self.assertRaises(self.frappe.PermissionError) as status_error:
            photo_save.status("0" * 32)
        self.assertEqual(str(status_error.exception), photo_save.SAFE_DENIAL)

        self.assertFalse(photo_save.publication_enabled())
        self.assertEqual(
            self.frappe.db.count("Clinic Photo Save Operation"), before
        )


if __name__ == "__main__":
    unittest.main()
