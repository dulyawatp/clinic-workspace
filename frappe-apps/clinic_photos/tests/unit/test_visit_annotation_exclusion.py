import contextvars
import copy
import pickle
from dataclasses import replace

import pytest

from clinic_photos.publication import exclusion, staging
from clinic_photos.publication import visit_annotation_journal_codec as codec
from test_visit_annotation_staging import intent, site


def test_visit_exclusion_order_contention_and_cleanup(site, monkeypatch):
    item = intent()
    monkeypatch.setattr(exclusion.frappe_context, "current_site_context",
                        lambda: (site.bound_site, site.sites))
    other = replace(item, binding=replace(item.binding, appointment="another"))
    ids = ["1" * 32, "2" * 32, "3" * 32]
    rows = {}
    for operation, value in zip(ids, (item, item, other)):
        rows[operation] = {**codec.submitted_values(value, staging.stage_visit_annotation(value)),
                           "name": operation}
    reads = []
    events = []
    monkeypatch.setattr(exclusion, "_read_operation",
                        lambda operation, **kw: events.append("read") or reads.append(operation) or rows[operation].copy())
    acquired = []
    original = exclusion._acquire

    def acquire(path):
        events.append("lock")
        acquired.append(path)
        return original(path)

    monkeypatch.setattr(exclusion, "_acquire", acquire)
    with exclusion.hold_visit_annotation_exclusion(ids[0]) as verified:
        assert verified.intent == item
        tree = exclusion._site_lock_directory(site.sites, item.binding.site)
        op_path, target_path = exclusion._lock_paths(tree.site_path, ids[0], item.target_identity)
        assert acquired[:2] == [op_path, target_path]
        assert events[:4] == ["lock", "read", "lock", "read"]
        with pytest.raises(exclusion.ExclusionBusy):
            with exclusion.hold_visit_annotation_exclusion(ids[0]):
                pytest.fail("duplicate entered")
        with pytest.raises(exclusion.ExclusionBusy):
            with exclusion.hold_visit_annotation_exclusion(ids[1]):
                pytest.fail("same draft entered")
        with exclusion.hold_visit_annotation_exclusion(ids[2]) as independent:
            assert independent.intent == other
    with exclusion.hold_visit_annotation_exclusion(ids[1]) as verified:
        assert verified.intent == item
    assert reads
    assert all(row["state"] == "SUBMITTED" for row in rows.values())


def test_visit_exclusion_rejects_tampered_row_without_yield(site, monkeypatch):
    item = intent()
    monkeypatch.setattr(exclusion.frappe_context, "current_site_context",
                        lambda: (site.bound_site, site.sites))
    operation = "1" * 32
    row = {**codec.submitted_values(item, staging.stage_visit_annotation(item)), "name": operation}
    row["draft"] = "client-controlled"
    monkeypatch.setattr(exclusion, "_read_operation", lambda *args, **kwargs: row)
    with pytest.raises(exclusion.ExclusionDenied):
        with exclusion.hold_visit_annotation_exclusion(operation):
            pytest.fail("tampered row entered")


@pytest.mark.parametrize("field,value", [
    ("name", "2" * 32), ("kind", "annotation_append"), ("site", "other"),
    ("actor", "other"), ("state", "PREPARED"),
])
def test_visit_exclusion_rejects_wrong_bindings(site, monkeypatch, field, value):
    item = intent()
    monkeypatch.setattr(exclusion.frappe_context, "current_site_context",
                        lambda: (site.bound_site, site.sites))
    operation = "1" * 32
    row = {**codec.submitted_values(item, staging.stage_visit_annotation(item)), "name": operation}
    row[field] = value
    monkeypatch.setattr(exclusion, "_read_operation", lambda *args, **kwargs: row)
    with pytest.raises(exclusion.ExclusionDenied):
        with exclusion.hold_visit_annotation_exclusion(operation):
            pytest.fail("wrong binding entered")


def test_second_read_is_current_locked_and_detects_state_change(site, monkeypatch):
    item = intent()
    monkeypatch.setattr(exclusion.frappe_context, "current_site_context",
                        lambda: (site.bound_site, site.sites))
    row = {**codec.submitted_values(item, staging.stage_visit_annotation(item)), "name": "1" * 32}
    reads = []

    def read(operation, *, for_update):
        reads.append(for_update)
        return {**row, "state": "PREPARED" if for_update else "SUBMITTED"}

    monkeypatch.setattr(exclusion, "_read_operation", read)
    with pytest.raises(exclusion.ExclusionDenied):
        with exclusion.hold_visit_annotation_exclusion(row["name"]):
            pytest.fail("stale snapshot entered")
    assert reads == [False, True]


def test_second_read_rejects_different_stage_for_same_content(site, monkeypatch):
    item = intent()
    monkeypatch.setattr(exclusion.frappe_context, "current_site_context",
                        lambda: (site.bound_site, site.sites))
    first = {**codec.submitted_values(item, staging.stage_visit_annotation(item)), "name": "1" * 32}
    second = {**codec.submitted_values(item, staging.stage_visit_annotation(item)), "name": first["name"]}
    assert first["staged_key"] != second["staged_key"]
    monkeypatch.setattr(exclusion, "_read_operation",
                        lambda operation, *, for_update: (second if for_update else first).copy())
    with pytest.raises(exclusion.ExclusionDenied):
        with exclusion.hold_visit_annotation_exclusion(first["name"]):
            pytest.fail("changed staging entered")


def test_foreign_doctype_rejected(site, monkeypatch):
    item = intent()
    monkeypatch.setattr(exclusion.frappe_context, "current_site_context",
                        lambda: (site.bound_site, site.sites))
    row = {**codec.submitted_values(item, staging.stage_visit_annotation(item)),
           "name": "1" * 32, "doctype": "Other"}
    monkeypatch.setattr(exclusion, "_read_operation", lambda *args, **kwargs: row)
    with pytest.raises(exclusion.ExclusionDenied):
        with exclusion.hold_visit_annotation_exclusion(row["name"]):
            pytest.fail("foreign doctype entered")


def test_liveness_checked_after_second_decode(site, monkeypatch):
    item = intent()
    monkeypatch.setattr(exclusion.frappe_context, "current_site_context",
                        lambda: (site.bound_site, site.sites))
    row = {**codec.submitted_values(item, staging.stage_visit_annotation(item)), "name": "1" * 32}
    monkeypatch.setattr(exclusion, "_read_operation", lambda *args, **kwargs: row.copy())
    events = []
    decode = codec.load_submitted
    verify_tree = exclusion._verify_stable_tree
    verify_lock = exclusion._verify_lock

    def load(*args, **kwargs):
        result = decode(*args, **kwargs)
        events.append("decode")
        return result

    def tree(*args):
        events.append("tree")
        return verify_tree(*args)

    def lock(*args):
        events.append("verify")
        return verify_lock(*args)

    monkeypatch.setattr(codec, "load_submitted", load)
    monkeypatch.setattr(exclusion, "_verify_stable_tree", tree)
    monkeypatch.setattr(exclusion, "_verify_lock", lock)
    with exclusion.hold_visit_annotation_exclusion(row["name"]):
        assert events[-4:] == ["decode", "tree", "verify", "verify"]


def test_lost_lock_after_second_decode_refuses_and_releases(site, monkeypatch):
    item = intent()
    monkeypatch.setattr(exclusion.frappe_context, "current_site_context",
                        lambda: (site.bound_site, site.sites))
    row = {**codec.submitted_values(item, staging.stage_visit_annotation(item)), "name": "1" * 32}
    monkeypatch.setattr(exclusion, "_read_operation", lambda *args, **kwargs: row.copy())
    decode = codec.load_submitted
    verify = exclusion._verify_lock
    decoded = []

    def load(*args, **kwargs):
        result = decode(*args, **kwargs)
        decoded.append(True)
        return result

    def lock(held):
        if len(decoded) == 2:
            raise exclusion.ExclusionLost(exclusion.SAFE_EXCLUSION_LOST)
        return verify(held)

    monkeypatch.setattr(codec, "load_submitted", load)
    monkeypatch.setattr(exclusion, "_verify_lock", lock)
    with pytest.raises(exclusion.ExclusionLost):
        with exclusion.hold_visit_annotation_exclusion(row["name"]):
            pytest.fail("lost lock entered")
    monkeypatch.setattr(exclusion, "_verify_lock", verify)
    with exclusion.hold_visit_annotation_exclusion(row["name"]) as verified:
        assert verified.intent == item


def test_owner_exact_binding_lifecycle_and_context(site, monkeypatch):
    item = intent()
    monkeypatch.setattr(exclusion.frappe_context, "current_site_context",
                        lambda: (site.bound_site, site.sites))
    row = {**codec.submitted_values(item, staging.stage_visit_annotation(item)), "name": "1" * 32}
    monkeypatch.setattr(exclusion, "_read_operation", lambda *args, **kwargs: row.copy())
    with exclusion.hold_visit_annotation_exclusion(row["name"]) as owner:
        assert (owner.operation_id, owner.site, owner.actor, owner.target_identity) == (
            row["name"], item.binding.site, item.binding.actor, item.target_identity)
        assert dict(owner.submitted_projection) == row
        assert owner.intent == item
        assert owner.require_live() is owner
        with pytest.raises(TypeError):
            owner.submitted_projection["draft"] = "fake"
        with pytest.raises((AttributeError, TypeError)):
            owner.actor = "other"
        for duplicate in (copy.copy, copy.deepcopy, pickle.dumps):
            with pytest.raises(TypeError):
                duplicate(owner)
        with pytest.raises(exclusion.ExclusionError):
            contextvars.copy_context().run(owner.require_live)
        assert owner.require_live() is owner
    with pytest.raises(exclusion.ExclusionError):
        owner.require_live()
    with exclusion.hold_visit_annotation_exclusion(row["name"]) as newer:
        with pytest.raises(exclusion.ExclusionError):
            owner.require_live()
        assert newer.require_live() is newer


def test_owner_denies_forgery_site_switch_and_lock_loss(site, monkeypatch):
    item = intent()
    current = [site.bound_site]
    monkeypatch.setattr(exclusion.frappe_context, "current_site_context",
                        lambda: (current[0], site.sites))
    row = {**codec.submitted_values(item, staging.stage_visit_annotation(item)), "name": "1" * 32}
    monkeypatch.setattr(exclusion, "_read_operation", lambda *args, **kwargs: row.copy())
    with exclusion.hold_visit_annotation_exclusion(row["name"]) as owner:
        forged = object.__new__(type(owner))
        with pytest.raises(exclusion.ExclusionError):
            forged.require_live()
        current[0] = "other"
        with pytest.raises(exclusion.ExclusionError):
            owner.require_live()
        current[0] = site.bound_site
        with pytest.raises(exclusion.ExclusionError):
            owner.require_live()
    with exclusion.hold_visit_annotation_exclusion(row["name"]) as owner:
        monkeypatch.setattr(exclusion, "_verify_lock",
                            lambda lock: (_ for _ in ()).throw(exclusion.ExclusionLost("lost")))
        with pytest.raises(exclusion.ExclusionLost):
            owner.require_live()
        with pytest.raises(exclusion.ExclusionError):
            owner.require_live()


def test_owner_live_check_does_not_reuse_submitted_row_as_current_authority(site, monkeypatch):
    item = intent()
    monkeypatch.setattr(exclusion.frappe_context, "current_site_context",
                        lambda: (site.bound_site, site.sites))
    row = {**codec.submitted_values(item, staging.stage_visit_annotation(item)), "name": "1" * 32}
    reads = []

    def read(*args, **kwargs):
        reads.append(kwargs["for_update"])
        return row.copy()

    monkeypatch.setattr(exclusion, "_read_operation", read)
    with exclusion.hold_visit_annotation_exclusion(row["name"]) as owner:
        assert reads == [False, True]
        row["state"] = "PREPARED"
        assert owner.require_live() is owner
        assert owner.submitted_projection["state"] == "SUBMITTED"
        assert reads == [False, True]


@pytest.mark.parametrize("drift", ["site", "sites_path"])
def test_initial_handoff_rejects_site_drift_and_releases_locks(site, monkeypatch, drift):
    item = intent()
    current = [site.bound_site, site.sites]
    monkeypatch.setattr(exclusion.frappe_context, "current_site_context",
                        lambda: tuple(current))
    row = {**codec.submitted_values(item, staging.stage_visit_annotation(item)), "name": "1" * 32}
    monkeypatch.setattr(exclusion, "_read_operation", lambda *args, **kwargs: row.copy())
    decode = codec.load_submitted
    decoded = []

    def load(*args, **kwargs):
        result = decode(*args, **kwargs)
        decoded.append(True)
        if len(decoded) == 2:
            if drift == "site":
                current[0] = "other"
            else:
                current[1] = site.site
        return result

    monkeypatch.setattr(codec, "load_submitted", load)
    with pytest.raises(exclusion.ExclusionError):
        with exclusion.hold_visit_annotation_exclusion(row["name"]):
            pytest.fail("site drift entered")
    current[0] = site.bound_site
    current[1] = site.sites
    monkeypatch.setattr(codec, "load_submitted", decode)
    with exclusion.hold_visit_annotation_exclusion(row["name"]) as owner:
        assert owner.require_live() is owner


def test_owner_rejects_different_thread_object_with_same_ident(site, monkeypatch):
    item = intent()
    monkeypatch.setattr(exclusion.frappe_context, "current_site_context",
                        lambda: (site.bound_site, site.sites))
    row = {**codec.submitted_values(item, staging.stage_visit_annotation(item)), "name": "1" * 32}
    monkeypatch.setattr(exclusion, "_read_operation", lambda *args, **kwargs: row.copy())
    original_thread = exclusion.threading.current_thread()
    with exclusion.hold_visit_annotation_exclusion(row["name"]) as owner:
        class OtherThread:
            ident = original_thread.ident

        monkeypatch.setattr(exclusion.threading, "current_thread", lambda: OtherThread())
        with pytest.raises(exclusion.ExclusionError):
            owner.require_live()
        monkeypatch.setattr(exclusion.threading, "current_thread", lambda: original_thread)
        assert owner.require_live() is owner
