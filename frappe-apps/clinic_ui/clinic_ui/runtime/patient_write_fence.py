"""Site-private path resolution for the explicit write-fence store.

The store and the deployment marker are created only by the operator command::

    bench --site marley-sit.local execute \
      clinic_ui.runtime.patient_write_fence.initialize_for_site

No request lazily initializes either. A missing store, anchor or deployment
marker makes every write return ``409 WRITE_LOCKED`` (or a generic denial for
the marker gate) instead of guessing that an earlier operation did not commit.
"""

from __future__ import annotations

import os

from clinic_ui.core import deployment_marker, fence_store

STORE_DIRNAME = fence_store.STORE_DIRNAME
ANCHOR_FILENAME = fence_store.ANCHOR_FILENAME


def _private_path():
    import frappe

    return frappe.get_site_path("private")


def _paths():
    private = _private_path()
    return (
        os.path.join(private, STORE_DIRNAME),
        os.path.join(private, ANCHOR_FILENAME),
    )


def initialize_for_site():
    """Create the fence store, anchor and deployment marker once per site."""

    import frappe

    private = _private_path()
    directory, anchor_path = _paths()
    info = fence_store.initialize_store(directory, anchor_path)
    deployment_marker.write_marker(private, frappe.local.site, info.deployment_id)
    return info


def open_for_site():
    """Open the anchored store; missing state raises ``StoreUnavailable``."""

    directory, anchor_path = _paths()
    return fence_store.open_store(directory, anchor_path)
