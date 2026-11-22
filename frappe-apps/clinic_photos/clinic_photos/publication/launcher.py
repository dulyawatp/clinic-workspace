"""Native queue notification, synchronous child launcher and minute rediscovery.

``enqueue_submission`` is the Task 3 after-commit adapter: it enqueues one opaque
operation ID on the existing short queue and nothing else. It deliberately does not
ask for ``enqueue_after_commit`` again because the caller already owns after-commit
delivery; a second deferral could leave the native callback unexecuted. A queue error
propagates to the Task 3 callback, which keeps the committed row SUBMITTED.

``launch_submission`` is the queued synchronous runner. It derives the site from the
worker's server context, refuses anything outside the approved site set (the lab site,
or an additional site named exactly by its own configuration) and the gate, and
starts one child from a fixed executable/module/argv/environment/cwd. Child stdout and
stderr are drained into a bounded buffer and are never reprinted. A timeout is
reported as unknown: it is not rollback, not proof the child died and not permission to
release exclusion or retry PREPARED work.

``rediscover_submissions`` is the existing scheduler's minute entry. It scans at most
25 *notifiable* SUBMITTED operations of the current server site in fair
``(creation, name)`` order, queues opaque IDs only, and stores a site-scoped Redis
cursor with a 24-hour TTL. At the end of the window the cursor wraps. A row the journal
would refuse is never notified: a legacy image-bearing row, a synthetic row without a
valid staged reference and any unknown kind stay untouched, and the scan skips them
(within a fixed page budget) so they cannot starve the window. Eligibility is decided
from metadata only: the legacy payload column is checked with one parameterised
``IS NOT NULL`` statement per page and is never selected, and every identity is bound
as a query parameter. Redis/queue failure leaves durable work pending; it never
publishes, adopts an actor, reserves a key or retries PREPARED work.

Importing this module is inert: no Frappe import, no site lookup, no queue, database or
filesystem activity happens at import time.
"""

import os
import re
import selectors
import subprocess
import time

from clinic_photos.publication import sites

GATE_CONFIG_KEY = "clinic_photos_publication_enabled"
# Static candidate set for the pre-bootstrap argv guard; the site's own loaded
# configuration is the actual decision (see ``sites.is_allowed``).
ALLOWED_SITES = sites.candidate_sites()

PYTHON_EXECUTABLE = "/home/frappe/frappe-bench/env/bin/python"
SITES_PATH = "/home/frappe/frappe-bench/sites"
PROCESS_MODULE = "clinic_photos.publication.process"
QUEUE = "short"

CHILD_DEADLINE_SECONDS = 120
CHILD_GRACE_SECONDS = 5
OUTPUT_LIMIT_BYTES = 64 * 1024
LAUNCHER_JOB_TIMEOUT_SECONDS = CHILD_DEADLINE_SECONDS + CHILD_GRACE_SECONDS + 30

REDISCOVERY_BATCH = 25
SCAN_MAX_PAGES = 8
CURSOR_TTL_SECONDS = 24 * 60 * 60
CURSOR_KEY_PREFIX = "clinic_photos:rediscovery:v1"

# Only these accepted request kinds may be notified; anything else fails closed.
ANNOTATION_APPEND = "annotation_append"
SYNTHETIC_ORIGINAL_CREATE = "synthetic_original_create"

_OPERATION_ID = re.compile(r"[0-9a-f]{32}")
_STAGED_KEY = re.compile(r"[0-9a-f]{32}\.(?:jpg|png)")
_STAGED_SHA256 = re.compile(r"[0-9a-f]{64}")
_STAGED_FORMATS = {"jpg": "JPEG", "png": "PNG"}
_STAGED_FIELDS = ("staged_key", "staged_sha256", "staged_size", "staged_format")
_STAGED_TEXT_FIELDS = ("staged_key", "staged_sha256", "staged_format")
# Metadata the rediscovery scan may read. The legacy image payload column is never
# selected: eligibility only asks whether a row still carries it.
_SCAN_FIELDS = ("name", "creation", "kind") + _STAGED_FIELDS

OPERATION_DOCTYPE = "Clinic Photo Save Operation"
SUBMITTED_STATE = "SUBMITTED"

_CHILD_ENVIRONMENT = {
    "HOME": "/home/frappe",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "PATH": "/home/frappe/frappe-bench/env/bin:/usr/local/bin:/usr/bin:/bin",
    "PYTHONIOENCODING": "utf-8",
}


class LauncherUnavailable(Exception):
    """Fixed-message launcher refusal; it carries no payload or identity."""


def _frappe():
    import frappe

    return frappe


def _valid_operation_id(value) -> bool:
    return isinstance(value, str) and _OPERATION_ID.fullmatch(value) is not None


def _gate_enabled(frappe) -> bool:
    conf = getattr(frappe, "conf", None)
    if conf is None:
        return False
    return conf.get(GATE_CONFIG_KEY) is True


def _server_site(frappe) -> str:
    site = getattr(getattr(frappe, "local", None), "site", "")
    if not isinstance(site, str) or site not in ALLOWED_SITES:
        raise LauncherUnavailable
    if not sites.is_allowed(site, getattr(frappe, "conf", None)):
        raise LauncherUnavailable
    return site


def child_environment() -> dict:
    """Fixed server-constructed environment; request headers cannot contribute."""
    return dict(_CHILD_ENVIRONMENT)


def _child_argv(site: str, operation_id: str) -> list[str]:
    return [
        PYTHON_EXECUTABLE,
        "-m",
        PROCESS_MODULE,
        "--site",
        site,
        "--operation-id",
        operation_id,
    ]


def enqueue_submission(operation_id: str) -> None:
    """After-commit notification: enqueue one opaque identity on the short queue."""
    if not _valid_operation_id(operation_id):
        raise LauncherUnavailable
    frappe = _frappe()
    _server_site(frappe)
    if not _gate_enabled(frappe):
        raise LauncherUnavailable
    frappe.enqueue(
        "clinic_photos.publication.launcher.launch_submission",
        queue=QUEUE,
        timeout=LAUNCHER_JOB_TIMEOUT_SECONDS,
        operation_id=operation_id,
    )


def _visit_server_site(frappe) -> str:
    from clinic_photos.publication import process

    site = getattr(getattr(frappe, "local", None), "site", "")
    if (not process._valid_visit_site(site) or
        not process._visit_gate_enabled(site, getattr(frappe, "conf", None))):
        raise LauncherUnavailable
    return site


def enqueue_visit_submission(operation_id: str) -> None:
    """After-commit callback target; enqueue only the opaque visit identity."""
    if not _valid_operation_id(operation_id):
        raise LauncherUnavailable
    frappe = _frappe()
    _visit_server_site(frappe)
    frappe.enqueue(
        "clinic_photos.publication.launcher.launch_visit_submission",
        queue=QUEUE, timeout=LAUNCHER_JOB_TIMEOUT_SECONDS, operation_id=operation_id,
    )


def launch_visit_submission(operation_id: str) -> None:
    """Launch at most one visit child for an exact committed SUBMITTED operation."""
    if not _valid_operation_id(operation_id):
        return None
    try:
        frappe = _frappe()
        site = _visit_server_site(frappe)
        if (type(getattr(frappe.db, "transaction_writes", None)) is not int or
            frappe.db.transaction_writes != 0):
            return None
        from clinic_photos.publication import journal
        from clinic_photos.publication.contracts import SubmittedOperation, VISIT_ORIGINAL_CREATE

        submitted = journal.load_submission(operation_id)
        if (not isinstance(submitted, SubmittedOperation) or submitted.id != operation_id or
            submitted.site != site or submitted.kind != VISIT_ORIGINAL_CREATE or
            _visit_server_site(frappe) != site):
            return None
        argv = [PYTHON_EXECUTABLE, "-m", PROCESS_MODULE, "--visit",
                "--site", site, "--operation-id", operation_id]
        _execute_child(argv)
    except Exception:
        return None


def _execute_child(
    argv,
    *,
    env=None,
    cwd=SITES_PATH,
    deadline=CHILD_DEADLINE_SECONDS,
    grace=CHILD_GRACE_SECONDS,
):
    """Run one child and return ``(exit_code, timed_out, stdout, stderr)``.

    No shell, no inherited environment, no unbounded ``communicate`` capture. Both
    pipes are read through non-blocking file descriptors while the direct child runs,
    so a descendant that keeps the inherited pipes open cannot extend the call past
    the deadline/grace budget. Output remains capped at ``OUTPUT_LIMIT_BYTES`` per
    stream. On the deadline the direct child is terminated, then killed after the
    grace period. A grandchild that keeps the pipes open is not tracked; that
    surviving-child possibility is why a timeout remains unknown rather than a
    rollback claim.
    """
    process = subprocess.Popen(
        argv,
        cwd=cwd,
        env=child_environment() if env is None else env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        close_fds=True,
    )
    captured = {"stdout": bytearray(), "stderr": bytearray()}
    selector = selectors.DefaultSelector()
    names = {}
    try:
        for name, stream in (("stdout", process.stdout), ("stderr", process.stderr)):
            descriptor = stream.fileno()
            os.set_blocking(descriptor, False)
            selector.register(descriptor, selectors.EVENT_READ)
            names[descriptor] = name

        def collect(events, *, keep_draining):
            for key, _mask in events:
                descriptor = key.fd
                target = captured[names[descriptor]]
                for _ in range(16):
                    if not keep_draining and len(target) >= OUTPUT_LIMIT_BYTES:
                        break
                    try:
                        chunk = os.read(descriptor, 8192)
                    except BlockingIOError:
                        break
                    except OSError:
                        chunk = b""
                    if not chunk:
                        try:
                            selector.unregister(descriptor)
                        except (KeyError, ValueError):
                            pass
                        break
                    room = OUTPUT_LIMIT_BYTES - len(target)
                    if room > 0:
                        target.extend(chunk[:room])

        timed_out = False
        exit_code = None
        cutoff = time.monotonic() + deadline
        while process.poll() is None:
            remaining = cutoff - time.monotonic()
            if remaining <= 0:
                break
            collect(selector.select(timeout=min(0.05, remaining)), keep_draining=True)

        if process.poll() is None:
            timed_out = True
            process.terminate()
            try:
                process.wait(timeout=grace)
            except subprocess.TimeoutExpired:
                process.kill()
                try:
                    process.wait(timeout=grace)
                except subprocess.TimeoutExpired:
                    pass
            exit_code = None
        else:
            exit_code = process.returncode

        # One non-blocking pass captures output already buffered when the direct child
        # exited; a descendant may keep writing, so this never waits for EOF.
        collect(selector.select(timeout=0), keep_draining=False)

        return (
            exit_code,
            timed_out,
            bytes(captured["stdout"]),
            bytes(captured["stderr"]),
        )
    finally:
        selector.close()
        for stream in (process.stdout, process.stderr):
            try:
                stream.close()
            except Exception:
                pass


def launch_submission(operation_id: str) -> None:
    """Run one synchronous child for this queued notification; never claims saved."""
    if not _valid_operation_id(operation_id):
        return None
    try:
        frappe = _frappe()
        site = _server_site(frappe)
        if not _gate_enabled(frappe):
            return None
        _execute_child(_child_argv(site, operation_id))
    except Exception:
        # Launcher, child or native construction failure is not rollback evidence and
        # not a saved outcome. The durable operation state remains authoritative.
        return None


def _cursor_key(site: str) -> str:
    return f"{CURSOR_KEY_PREFIX}:{site}"


def _load_cursor(frappe, key):
    try:
        value = frappe.cache.get_value(key, use_local_cache=False)
    except Exception:
        return None
    if (
        isinstance(value, (tuple, list))
        and len(value) == 2
        and value[0] is not None
        and isinstance(value[1], str)
    ):
        return (value[0], value[1])
    return None


def _scan_submitted(frappe, site: str, cursor):
    """Fetch at most ``REDISCOVERY_BATCH`` notifiable SUBMITTED rows after the cursor.

    Ordering is ``(creation, name)``: rows sharing the cursor's creation sort by name
    first, then later creations follow. One extra row distinguishes "more work" from
    "wrap the cursor".

    Rows the journal can no longer verify are skipped instead of notified: a legacy
    image-bearing row, a synthetic row without a valid staged reference and any kind
    other than the two accepted kinds. Skipping them must not stall the window, so the
    scan may examine more than one page per call, up to ``SCAN_MAX_PAGES``. A row with
    no usable ordering key (missing creation or a non-text name) is never notified and
    stops the page instead, because the cursor cannot express a position past it. It
    returns the eligible rows, whether unexamined work remains after the last row
    examined, and that last examined row so the cursor advances past the skipped ones.
    """
    eligible = []
    position = None
    more = False
    page_cursor = cursor

    for _ in range(SCAN_MAX_PAGES):
        page, page_more = _fetch_page(frappe, site, page_cursor)
        if not page:
            more = False
            break
        legacy = _legacy_rows(frappe, [row.get("name") for row in page])
        examined = 0
        unusable = False
        for row in page:
            if row.get("creation") is None or not isinstance(row.get("name"), str):
                # No usable ordering key: never notify it and never advance the cursor
                # past it, so a raw-restored row is not guessed at or skipped over.
                unusable = True
                break
            examined += 1
            position = row
            page_cursor = (row.get("creation"), row.get("name"))
            if _notifiable(row, legacy):
                eligible.append(row)
                if len(eligible) >= REDISCOVERY_BATCH:
                    break
        if unusable:
            more = True
            break
        more = page_more or examined < len(page)
        if len(eligible) >= REDISCOVERY_BATCH or not page_more:
            break

    return list(eligible[:REDISCOVERY_BATCH]), more, position


def _fetch_page(frappe, site: str, cursor):
    """At most ``REDISCOVERY_BATCH + 1`` SUBMITTED rows after the cursor; metadata only."""
    limit = REDISCOVERY_BATCH + 1
    filters = {"state": SUBMITTED_STATE, "site": site}

    if cursor is None:
        rows = frappe.db.get_values(
            OPERATION_DOCTYPE,
            filters=filters,
            fieldname=list(_SCAN_FIELDS),
            order_by="creation asc, name asc",
            limit=limit,
            as_dict=True,
        )
    else:
        creation, name = cursor
        rows = frappe.db.get_values(
            OPERATION_DOCTYPE,
            filters=dict(filters, creation=creation, name=(">", name)),
            fieldname=list(_SCAN_FIELDS),
            order_by="name asc",
            limit=limit,
            as_dict=True,
        )
        if len(rows) < limit:
            rows = list(rows) + list(
                frappe.db.get_values(
                    OPERATION_DOCTYPE,
                    filters=dict(filters, creation=(">", creation)),
                    fieldname=list(_SCAN_FIELDS),
                    order_by="creation asc, name asc",
                    limit=limit - len(rows),
                    as_dict=True,
                )
            )

    return list(rows), len(rows) > REDISCOVERY_BATCH


_LEGACY_PRESENCE_SQL = (
    "select `name` from `tabClinic Photo Save Operation` "
    "where `name` in ({placeholders}) and `image_base64` is not null"
)


def _legacy_rows(frappe, names):
    """Names among ``names`` whose row still stores the legacy image payload.

    Presence must be ``IS NOT NULL``, which no ORM filter here can express: in the
    pinned Frappe 16, ``("is", "set")`` on a Long Text compiles to ``!= ''`` and is
    evaluated under the shipped PAD SPACE collation, where ``'   ' = ''`` — a
    whitespace-only payload would be invisible and then notified. So the presence check
    is one parameterised statement over exactly this page's identities:

    * the statement text is a module constant; only the ``%s`` placeholder count varies
      with the (bounded) page size, and every identity is bound as a parameter, so a
      raw-restored name can never alter it;
    * only ``name`` is selected: the payload is never read, returned, logged or queued;
    * ``IS NOT NULL`` also matches an empty string, so such a row is conservatively
      treated as legacy and refused rather than guessed at.

    Any failure propagates: the caller then queues nothing and leaves the cursor where
    it was.
    """
    wanted = [name for name in names if isinstance(name, str) and name]
    if not wanted:
        return set()
    rows = frappe.db.sql(
        _LEGACY_PRESENCE_SQL.format(placeholders=", ".join(["%s"] * len(wanted))),
        tuple(wanted),
        as_dict=True,
    )
    return {
        row.get("name")
        for row in rows or ()
        if isinstance(row, dict) and isinstance(row.get("name"), str)
    }


def _staged_reference_ok(row) -> bool:
    """Whether the row's staged metadata can name one authentic staging object."""
    key = row.get("staged_key")
    if not isinstance(key, str) or _STAGED_KEY.fullmatch(key) is None:
        return False
    sha256 = row.get("staged_sha256")
    if not isinstance(sha256, str) or _STAGED_SHA256.fullmatch(sha256) is None:
        return False
    size = row.get("staged_size")
    if isinstance(size, bool) or not isinstance(size, int) or size < 1:
        return False
    if _STAGED_FORMATS.get(key.rsplit(".", 1)[-1]) != row.get("staged_format"):
        return False
    return True


def _staged_evidence_present(row) -> bool:
    """Presence of any staged field on one row.

    ``staged_size`` is an ``Int`` column: an installed database returns ``0`` for a row
    that never set it, so ``0``/``None``/``""`` are absent and a bool or any other value
    counts as present (inconsistent evidence). The text fields keep the usual
    ``None``/``""`` absence rule.
    """
    for fieldname in _STAGED_TEXT_FIELDS:
        if row.get(fieldname) not in (None, ""):
            return True
    size = row.get("staged_size")
    if isinstance(size, bool):
        return True
    return size not in (None, 0, "")


def _notifiable(row, legacy) -> bool:
    """Whether this SUBMITTED row may still be published, from metadata alone.

    Anything the journal would refuse — a legacy image-bearing row, a synthetic row
    without a valid staged reference, an unknown or missing kind, staged metadata on an
    annotation row — is not notifiable. Such a row stays SUBMITTED and untouched.
    """
    name = row.get("name")
    if not isinstance(name, str) or name in legacy:
        return False
    kind = row.get("kind")
    if kind == ANNOTATION_APPEND:
        return not _staged_evidence_present(row)
    if kind == SYNTHETIC_ORIGINAL_CREATE:
        return _staged_reference_ok(row)
    return False


def rediscover_submissions() -> int:
    """Queue up to 25 notifiable SUBMITTED identities; never publish or retry PREPARED."""
    try:
        frappe = _frappe()
        site = _server_site(frappe)
    except Exception:
        return 0
    if not _gate_enabled(frappe):
        return 0

    key = _cursor_key(site)
    cursor = _load_cursor(frappe, key)
    try:
        rows, more, position = _scan_submitted(frappe, site, cursor)
    except Exception:
        # An unreadable scan or eligibility probe queues nothing and leaves the cursor
        # where it was: durable work stays pending instead of being guessed at.
        return 0

    enqueued = 0
    last = None
    failed = False
    for row in rows:
        name = row.get("name")
        if not _valid_operation_id(name):
            # A raw-restored/corrupted name is not a notifiable identity: consume the
            # scan position but never queue it or count it as successful.
            last = row
            continue
        try:
            enqueue_submission(name)
        except Exception:
            # Queue/Redis unavailable: stop here and keep the remaining durable rows
            # pending. The cursor is not advanced past the failed identity.
            failed = True
            break
        enqueued += 1
        last = row

    try:
        if failed:
            if last is not None:
                frappe.cache.set_value(
                    key,
                    (last["creation"], last["name"]),
                    expires_in_sec=CURSOR_TTL_SECONDS,
                )
        elif not more:
            frappe.cache.delete_value(key)
        elif position is not None:
            # Nothing notifiable is left before this position, including the skipped
            # rows, so the next window continues after it.
            frappe.cache.set_value(
                key,
                (position["creation"], position["name"]),
                expires_in_sec=CURSOR_TTL_SECONDS,
            )
    except Exception:
        # Losing the cursor can repeat notifications but cannot lose durable work.
        pass

    return enqueued
