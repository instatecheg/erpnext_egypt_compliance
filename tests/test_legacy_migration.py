import hashlib
import json
from types import SimpleNamespace

import frappe
import pytest

from erpnext_egypt_compliance import legacy_migration as migration


@pytest.fixture(autouse=True)
def db_transaction():
    """The retirement guard tests do not access a site."""
    yield


def test_archive_names_do_not_collide_across_source_doctypes():
    assert migration.archive_name("ETA Logs", "A") != migration.archive_name("ETA Logs Table", "A")
    assert migration.archive_name("ETA Logs", "A") == migration.archive_name("ETA Logs", "A")


def test_digest_detects_value_and_row_changes():
    first = [{"name": "A", "eta_uuid": "a"}]
    assert migration.digest_rows(first) == migration.digest_rows(first)
    assert migration.digest_rows(first) != migration.digest_rows([{"name": "A", "eta_uuid": "b"}])
    assert migration.digest_rows(first) != migration.digest_rows(first * 2)


def test_archive_is_read_only_for_system_managers():
    schema = json.loads(
        (migration.Path(migration.__file__).parent
         / "erpnext_eta/doctype/eta_legacy_record/eta_legacy_record.json").read_text()
    )
    assert schema["permissions"] == [{"role": "System Manager", "read": 1, "report": 1}]
    assert all(field["read_only"] for field in schema["fields"])


def test_retirement_requires_scheduler_pause(monkeypatch):
    monkeypatch.setattr(migration.frappe.local, "conf", frappe._dict(pause_scheduler=False), raising=False)
    with pytest.raises(frappe.ValidationError, match="scheduler"):
        migration.require_paused()


def test_retirement_requires_compliance(monkeypatch):
    monkeypatch.setattr(migration.frappe.local, "conf", frappe._dict(pause_scheduler=True), raising=False)
    monkeypatch.setattr(migration.frappe, "get_installed_apps", lambda: ["frappe", "erpnext", "etams"])
    with pytest.raises(frappe.ValidationError, match="Install compliance"):
        migration.require_paused()


def test_modified_archive_blocks_retirement(monkeypatch):
    monkeypatch.setattr(migration.frappe, "get_all", lambda *args, **kwargs: [])
    with pytest.raises(frappe.ValidationError, match="Archive contents changed"):
        migration.verify_archive({"ETA Logs": {"count": 1, "sha256": "incorrect"}})


def test_verified_archive_passes(monkeypatch):
    payload = '{"name": "LOG-1"}'
    monkeypatch.setattr(
        migration.frappe, "get_all", lambda *args, **kwargs: [SimpleNamespace(payload=payload)]
    )
    migration.verify_archive({
        "ETA Logs": {"count": 1, "sha256": hashlib.sha256((payload + "\n").encode()).hexdigest()}
    })


def test_missing_preparation_blocks_uninstall(monkeypatch):
    monkeypatch.setattr(migration, "require_paused", lambda: None)
    monkeypatch.setattr(migration.frappe.local, "db", SimpleNamespace(get_value=lambda *a: None), raising=False)
    with pytest.raises(frappe.ValidationError, match="prepare"):
        migration.before_etams_uninstall()


def test_reconciliation_does_not_change_sites_still_using_etams(monkeypatch):
    monkeypatch.setattr(migration.frappe, "get_installed_apps", lambda: ["etams"])
    migration.reconcile_retired_fields()


def test_receipt_reconciliation_does_not_replay_fixture_audit_timestamps(monkeypatch):
    from erpnext_egypt_compliance.patches.v1_0 import claim_pos_invoice_eta_fields

    definitions = {}
    monkeypatch.setattr(
        claim_pos_invoice_eta_fields, "create_custom_fields",
        lambda fields, update: definitions.update(fields),
    )
    claim_pos_invoice_eta_fields.execute()
    fields = [field for rows in definitions.values() for field in rows]
    assert len(fields) == 23
    assert all(not {"creation", "modified", "owner", "modified_by", "docstatus", "idx"}.intersection(field)
               for field in fields)
    assert any(field["fieldname"] == "eta_status" for field in fields)


def test_reconciliation_reapplies_receipt_fields_and_removes_dangling_links(monkeypatch):
    from erpnext_egypt_compliance.patches.v1_0 import claim_pos_invoice_eta_fields

    calls = []
    monkeypatch.setattr(migration.frappe, "get_installed_apps", lambda: ["erpnext_egypt_compliance"])
    monkeypatch.setattr(
        migration.frappe.local, "db",
        SimpleNamespace(
            exists=lambda doctype, name: name != "ETA Tax SubType",
            delete=lambda *args: calls.append(args),
        ), raising=False,
    )
    monkeypatch.setattr(
        migration.frappe, "get_all",
        lambda *a, **kw: [frappe._dict(name="Old-Tax", dt="Item Tax Template Detail", options="ETA Tax SubType")],
    )
    monkeypatch.setattr(migration.frappe, "clear_cache", lambda **kw: None)
    monkeypatch.setattr(claim_pos_invoice_eta_fields, "execute", lambda: calls.append("canonical"))
    migration.reconcile_retired_fields()
    assert calls == ["canonical", ("Custom Field", {"name": "Old-Tax"})]
