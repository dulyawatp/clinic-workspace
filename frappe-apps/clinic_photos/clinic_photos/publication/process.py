"""Fresh native child that may later own publication for one SUBMITTED operation.

The module is started by the launcher with a fixed server-selected argument vector
from the bench ``sites`` directory::

    python -m clinic_photos.publication.process --site <site> --operation-id <id>

Only an approved publication site and a server-generated opaque operation ID are
accepted. The lab site is always approved; any additional site must be named
exactly by that site's own configuration. The child creates its own Frappe context
from ``set_admin_as_user=False``,
so bootstrap never runs publication as Administrator. It then loads the committed
SUBMITTED operation through the internal journal seam, compares the stored site with
the actual site, adopts the persisted submission actor through native ``set_user`` and
performs a fresh enabled-clinician/target-authority check before the coordinator is
consulted.

Task 4 deliberately ships no coordinator. ``coordinator.publish_submission`` is
imported lazily; while the module is absent the child refuses before any prepared key,
File or content byte exists. A missing or failing coordinator is never replaced by a
successful stub. Exit codes and stdout markers are bounded and PHI-free; they never
establish a saved outcome. Durable operation evidence, not an exit status, remains the
publication authority.
"""

import re
import sys

from clinic_photos.publication import sites

SITES_PATH = "/home/frappe/frappe-bench/sites"
# Static candidate set for the pre-bootstrap argv guard; the site's own loaded
# configuration is the actual decision (see ``sites.is_allowed``).
ALLOWED_SITES = sites.candidate_sites()
GATE_CONFIG_KEY = "clinic_photos_publication_enabled"

COORDINATOR_MODULE = "clinic_photos.publication.coordinator"
COORDINATOR_CALLABLE = "publish_submission"

EXIT_COMPLETED = 0
EXIT_REFUSED = 2
EXIT_UNAVAILABLE = 3

STATUS_COMPLETED = "completed"
STATUS_REFUSED = "refused"
STATUS_UNAVAILABLE = "unavailable"

# ``contracts.SaveOutcome`` statuses this child may read as a definite end state. Only
# ``saved`` can be completion, and only for the exact operation this child was given.
OUTCOME_SAVED = "saved"
OUTCOME_REJECTED = frozenset({"rejected", "conflict"})

_OPERATION_ID = re.compile(r"[0-9a-f]{32}")


def _frappe():
    import frappe

    return frappe


def _valid_operation_id(value) -> bool:
    return isinstance(value, str) and _OPERATION_ID.fullmatch(value) is not None


def _valid_site(value) -> bool:
    return isinstance(value, str) and value in ALLOWED_SITES


def _result(status: str, operation_id) -> int:
    """Emit one bounded outcome marker and map it to a process exit code.

    The marker carries only a fixed status word and, when it is a valid opaque
    identity, the operation ID. Actor, site, payload, paths and child tracebacks are
    never printed.
    """
    if _valid_operation_id(operation_id):
        print(f"status={status} operation_id={operation_id}")
    else:
        print(f"status={status}")
    if status == STATUS_COMPLETED:
        return EXIT_COMPLETED
    if status == STATUS_REFUSED:
        return EXIT_REFUSED
    return EXIT_UNAVAILABLE


def _outcome_status(outcome, operation_id: str) -> str:
    """Read the coordinator's returned outcome as a bounded child marker.

    Completion is only ``contracts.SaveOutcome("saved", <this operation>)``: the child
    re-reads no evidence and never upgrades ``unknown``, ``pending``, a malformed value
    or another operation's identity. A definite ``rejected``/``conflict`` already
    aborted the still-SUBMITTED row, so it maps to the refusal marker; everything else
    stays unavailable.
    """
    if getattr(outcome, "operation_id", None) != operation_id:
        return STATUS_UNAVAILABLE
    status = getattr(outcome, "status", None)
    if status == OUTCOME_SAVED:
        return STATUS_COMPLETED
    if status in OUTCOME_REJECTED:
        return STATUS_REFUSED
    return STATUS_UNAVAILABLE


def _gate_enabled(conf) -> bool:
    if conf is None:
        return False
    return conf.get(GATE_CONFIG_KEY) is True


def _context_is_clean(frappe) -> bool:
    """Refuse inherited callbacks or writes instead of clearing them.

    A fresh process must not carry transaction callbacks or uncommitted writes from
    bootstrap into the publication attempt. The installed ``CallbackManager`` keeps
    its pending callables in ``_functions``; the database exposes a write counter.
    Unknown shapes are treated as unclean, not silently allowed.
    """
    database = getattr(frappe, "db", None)
    if database is None:
        return False
    for manager_name in (
        "before_commit",
        "after_commit",
        "before_rollback",
        "after_rollback",
    ):
        manager = getattr(database, manager_name, None)
        if manager is None:
            return False
        pending = getattr(manager, "_functions", None)
        if pending is None or len(pending):
            return False
    writes = getattr(database, "transaction_writes", None)
    return writes == 0


def run(operation_id: str, *, site: str) -> int:
    """Handle one opaque SUBMITTED identity in a fresh native context.

    Invalid identity/site refuses before importing or initializing the framework.
    Gate, site, clean-context, state/target and coordinator failures refuse or report
    unavailable without commit, rollback, File/content write or a saved claim. Completion
    is reported only for a ``saved`` coordinator outcome naming this operation.
    """
    if not _valid_operation_id(operation_id) or not _valid_site(site):
        return _result(STATUS_REFUSED, operation_id)

    frappe = None
    try:
        frappe = _frappe()
        frappe.init(site=site, sites_path=SITES_PATH)
        frappe.connect(set_admin_as_user=False)

        if getattr(frappe.local, "site", "") != site:
            return _result(STATUS_REFUSED, operation_id)
        if not sites.is_allowed(site, getattr(frappe, "conf", None)):
            return _result(STATUS_REFUSED, operation_id)
        if not _gate_enabled(getattr(frappe, "conf", None)):
            return _result(STATUS_REFUSED, operation_id)
        if not _context_is_clean(frappe):
            return _result(STATUS_REFUSED, operation_id)

        from clinic_photos.publication import journal
        from clinic_photos.publication import submission as submission_boundary

        try:
            committed = journal.load_submission(operation_id)
        except journal.JournalError:
            return _result(STATUS_REFUSED, operation_id)

        if committed.site != site:
            return _result(STATUS_REFUSED, operation_id)

        # Adopt the persisted submission actor; the initial lookup itself grants no
        # domain or publication authority.
        frappe.set_user(committed.actor)
        try:
            submission_boundary.require_target_authority(
                committed.request, committed.actor, site
            )
        except submission_boundary.SubmissionError:
            return _result(STATUS_REFUSED, operation_id)

        if not _context_is_clean(frappe):
            return _result(STATUS_REFUSED, operation_id)

        import importlib

        try:
            coordinator = importlib.import_module(COORDINATOR_MODULE)
        except ImportError:
            return _result(STATUS_UNAVAILABLE, operation_id)
        publish_submission = getattr(coordinator, COORDINATOR_CALLABLE, None)
        child_scope = getattr(coordinator, "child_process_publication", None)
        if not callable(publish_submission) or not callable(child_scope):
            return _result(STATUS_UNAVAILABLE, operation_id)

        # The coordinator owns the later preparation/publication transactions. This
        # child never commits, rolls back or clears the transaction on its behalf, and
        # only this bootstrap registers the child entry, so a request or job callback
        # cannot publish directly. Its returned outcome is the only thing that may
        # report completion; an uncertain or foreign outcome never does.
        with child_scope():
            outcome = publish_submission(committed)
        return _result(_outcome_status(outcome, operation_id), operation_id)
    except Exception:
        # No traceback, child exception text or payload is printed. The durable
        # operation state, not this exit code, remains the outcome authority.
        return _result(STATUS_UNAVAILABLE, operation_id)
    finally:
        if frappe is not None:
            try:
                frappe.destroy()
            except Exception:
                pass


def main(argv=None) -> int:
    """Strict fixed-order argument guard for the module entry point."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    if (
        len(arguments) != 4
        or arguments[0] != "--site"
        or arguments[2] != "--operation-id"
    ):
        return _result(STATUS_REFUSED, None)
    return run(arguments[3], site=arguments[1])


if __name__ == "__main__":
    sys.exit(main())
