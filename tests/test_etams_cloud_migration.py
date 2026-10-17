import ast
import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import frappe
import pytest

from erpnext_egypt_compliance import etams_cloud_migration as cloud


@pytest.fixture(autouse=True)
def db_transaction(monkeypatch):
    monkeypatch.setattr(frappe.local, "flags", frappe._dict(in_test=False), raising=False)
    monkeypatch.setattr(cloud, "now_datetime", lambda: "2026-10-16 00:00:00")
    yield


def state():
    return {
        "phase": "detached", "tables": {}, "metadata": {}, "metadata_hashes": {},
        "columns": {}, "invoices": {}, "connector_mappings": {},
    }


def test_cloud_module_never_imports_etams():
    tree = ast.parse(Path(cloud.__file__).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(not item.name.startswith("etams") for item in node.names)
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("etams")


def test_page_is_manager_only_and_never_calls_eta():
    root = Path(cloud.__file__).parent / "erpnext_eta/page/eta_migration"
    assert json.loads((root / "eta_migration.json").read_text())["roles"] == [{"role": "System Manager"}]
    script = (root / "eta_migration.js").read_text()
    assert "CLEAN UP ETAMS" in script
    assert "start_action" in script
    assert "submit_receipt" not in script


@pytest.mark.parametrize("method,args", [
    (cloud.get_status, ()), (cloud.set_mappings, ([],)),
    (cloud.start_action, ("verify",)), (cloud.bootstrap, ()),
])
def test_entrypoints_enforce_roles_before_database_access(monkeypatch, method, args):
    def denied(*args, **kwargs):
        raise frappe.PermissionError("manager required")
    monkeypatch.setattr(cloud.frappe, "only_for", denied)
    with pytest.raises(frappe.PermissionError):
        method(*args)


def test_bootstrap_requires_explicit_confirmation(monkeypatch):
    monkeypatch.setattr(cloud, "require_manager", lambda: None)
    monkeypatch.setattr(cloud, "require_paused", lambda: None)
    with pytest.raises(frappe.ValidationError, match="ARCHIVE AND DETACH"):
        cloud.bootstrap()


def test_bootstrap_requires_maintenance_mode(monkeypatch):
    monkeypatch.setattr(cloud, "require_manager", lambda: None)
    monkeypatch.setattr(cloud, "require_paused", lambda: None)
    monkeypatch.setattr(frappe.local, "conf", frappe._dict(maintenance_mode=0), raising=False)
    with pytest.raises(frappe.ValidationError, match="maintenance"):
        cloud.bootstrap("ARCHIVE AND DETACH ETAMS")


@pytest.mark.parametrize("action,confirmation", [("delete", None), ("cleanup", None)])
def test_unconfirmed_or_unknown_actions_rejected(monkeypatch, action, confirmation):
    monkeypatch.setattr(cloud, "require_manager", lambda: None)
    monkeypatch.setattr(cloud, "require_paused", lambda: None)
    with pytest.raises(frappe.ValidationError):
        cloud.start_action(action, confirmation)


def test_snapshot_preserves_baseline_columns(monkeypatch):
    seen = []
    monkeypatch.setattr(cloud, "raw_rows", lambda dt, fields: seen.append((dt, fields)) or [{"name": "INV"}])
    assert cloud.snapshot({"Sales Invoice": ["name", "eta_uuid"]})["Sales Invoice"]["count"] == 1
    assert seen == [("Sales Invoice", ["name", "eta_uuid"])]


def test_changed_invoices_block_cleanup(monkeypatch):
    saved = state()
    monkeypatch.setattr(cloud.legacy, "verify_archive", lambda rows: None)
    monkeypatch.setattr(cloud, "snapshot", lambda cols: {"changed": True})
    with pytest.raises(frappe.ValidationError, match="Invoice ETA"):
        cloud.verify_sources(saved)


def test_modified_source_blocks_cleanup(monkeypatch):
    saved = state()
    saved["tables"] = {"ETA Logs": {"count": 1, "sha256": "old", "columns": ["name"]}}
    monkeypatch.setattr(cloud.legacy, "verify_archive", lambda rows: None)
    monkeypatch.setattr(cloud, "snapshot", lambda cols: {})
    monkeypatch.setattr(frappe.local, "db", SimpleNamespace(table_exists=lambda dt: True), raising=False)
    monkeypatch.setattr(cloud, "raw_rows", lambda *args: iter([{"name": "changed"}]))
    with pytest.raises(frappe.ValidationError, match="source changed"):
        cloud.verify_sources(saved)


def test_missing_source_reappearing_blocks_cleanup(monkeypatch):
    saved = state()
    saved["tables"] = {"ETA Documents": {"missing_table": True}}
    monkeypatch.setattr(cloud.legacy, "verify_archive", lambda rows: None)
    monkeypatch.setattr(cloud, "snapshot", lambda cols: {})
    monkeypatch.setattr(frappe.local, "db", SimpleNamespace(table_exists=lambda dt: True), raising=False)
    with pytest.raises(frappe.ValidationError, match="appeared"):
        cloud.verify_sources(saved)


def test_completed_migration_does_not_require_original_tables(monkeypatch):
    saved = state()
    saved["phase"] = "complete"
    saved["tables"] = {"ETA Logs": {"count": 0, "sha256": "original", "columns": ["name"]}}
    checks = []
    monkeypatch.setattr(cloud.legacy, "verify_archive", lambda rows: checks.append(rows))
    monkeypatch.setattr(cloud, "snapshot", lambda cols: {})
    cloud.verify_sources(saved)
    assert checks[0] == {"ETAMS Cloud: ETA Logs": {"count": 0, "sha256": "original"}}


def test_mapping_requires_existing_target(monkeypatch):
    saved = state()
    saved["tables"] = {"ETA Receipt Settings": {"count": 1}}
    monkeypatch.setattr(cloud, "legacy_rows", lambda *a: [frappe._dict(name="POS OLD")])
    with pytest.raises(frappe.ValidationError, match="Map legacy"):
        cloud.verify_connector_mappings(saved)


@pytest.mark.parametrize("mappings", ["{}", '[{"legacy_name":"old"}]', '"invalid"'])
def test_malformed_mappings_fail_explicitly(monkeypatch, mappings):
    monkeypatch.setattr(cloud, "require_manager", lambda: None)
    monkeypatch.setattr(cloud, "require_paused", lambda: None)
    monkeypatch.setattr(cloud, "filelock", lambda *a, **kw: nullcontext())
    monkeypatch.setattr(cloud, "load_state", state)
    with pytest.raises(frappe.ValidationError, match="Each mapping"):
        cloud.set_mappings(mappings)


def test_failure_is_persisted_and_reraised(monkeypatch):
    monkeypatch.setattr(cloud, "require_manager", lambda: None)
    monkeypatch.setattr(cloud, "require_paused", lambda: None)
    monkeypatch.setattr(cloud, "filelock", lambda *a, **kw: nullcontext())
    def fail():
        raise frappe.ValidationError("changed invoice")
    monkeypatch.setattr(cloud, "verify", fail)
    calls = []
    monkeypatch.setattr(cloud, "save_job", calls.append)
    monkeypatch.setattr(frappe.local, "db", SimpleNamespace(rollback=lambda: calls.append("rollback")), raising=False)
    monkeypatch.setattr(frappe, "log_error", lambda **kw: SimpleNamespace(name="ERROR-1"))
    with pytest.raises(frappe.ValidationError, match="changed invoice"):
        cloud.run_action("verify")
    assert calls[-1] == {"action": "verify", "status": "failed", "error_log": "ERROR-1"}
    assert "rollback" in calls


def test_shared_doctypes_and_fields_are_recognized():
    assert {"ETA UOM", "ETA Settings", "ETA Tax Type"} <= cloud.canonical_doctypes()
    assert "POS Invoice-eta_uuid" in {row["name"] for row in cloud.canonical_fields()}
    assert "eta_unrelated_app_field" not in cloud.LEGACY_FIELDS["POS Invoice"]


def test_cleanup_cannot_run_without_bootstrap(monkeypatch):
    monkeypatch.setattr(cloud, "require_paused", lambda: None)
    monkeypatch.setattr(cloud, "load_state", lambda: None)
    with pytest.raises(frappe.ValidationError, match="bootstrap"):
        cloud.cleanup()


def test_source_does_not_drop_tables_or_invoice_columns():
    code = Path(cloud.__file__).read_text()
    assert "DROP TABLE" not in code
    assert "DROP COLUMN" not in code
    assert "from etams" not in code


def test_bootstrap_detaches_only_after_verified_archive(monkeypatch):
    import frappe.installer

    events = []
    monkeypatch.setattr(cloud, "require_manager", lambda: None)
    monkeypatch.setattr(cloud, "require_paused", lambda: None)
    monkeypatch.setattr(frappe.local, "conf", frappe._dict(maintenance_mode=1), raising=False)
    monkeypatch.setattr(cloud, "filelock", lambda *a, **kw: nullcontext())
    monkeypatch.setattr(cloud, "load_state", lambda: None)
    monkeypatch.setattr(frappe, "get_installed_apps", lambda: ["frappe", "erpnext", "etams"])
    monkeypatch.setattr(frappe.installer, "add_module_defs", lambda *a, **kw: None)
    monkeypatch.setattr(frappe, "setup_module_map", lambda **kw: None)
    monkeypatch.setattr(frappe, "reload_doc", lambda *a, **kw: None)
    monkeypatch.setattr(cloud, "inventory", lambda: (["ETA Logs"], {}))
    monkeypatch.setattr(cloud, "snapshot", lambda cols: {})
    monkeypatch.setattr(cloud, "raw_rows", lambda *a: iter([frappe._dict(name="OLD-LOG")]))
    monkeypatch.setattr(cloud, "archive_rows", lambda *a: events.append("archive"))
    monkeypatch.setattr(cloud, "save_state", lambda saved: events.append(saved["phase"]))
    monkeypatch.setattr(cloud, "verify_sources", lambda saved: events.append("verified"))
    monkeypatch.setattr(frappe, "get_all", lambda *a, **kw: [])
    monkeypatch.setattr(frappe, "clear_cache", lambda: None)
    monkeypatch.setattr(frappe.local, "db", SimpleNamespace(
        get_table_columns=lambda dt: ["name", "docstatus", "eta_uuid"],
        table_exists=lambda dt: True,
        set_global=lambda key, value: events.append(("detach", json.loads(value))),
    ), raising=False)
    result = cloud.bootstrap("ARCHIVE AND DETACH ETAMS")
    assert result["phase"] == "detached"
    assert events.index("archive") < events.index("verified") < events.index(("detach", ["frappe", "erpnext"]))
    assert events[-1] == "detached"


def test_archive_verification_failure_never_detaches_registration(monkeypatch):
    saved = state()
    monkeypatch.setattr(cloud.legacy, "verify_archive", lambda rows: (_ for _ in ()).throw(
        frappe.ValidationError("archive corrupt")
    ))
    with pytest.raises(frappe.ValidationError, match="archive corrupt"):
        cloud.verify_sources(saved)


def test_connector_matching_does_not_refresh_tokens(monkeypatch):
    import frappe.utils.password

    saved = state()
    saved["tables"] = {"ETA Receipt Settings": {"count": 1}}
    saved["connector_mappings"] = {"ETA Receipt Settings:OLD": "NEW"}
    saved["reviewed_connectors"] = ["ETA Receipt Settings:OLD"]
    row = frappe._dict(name="OLD", pos_profile="POS", serial_number="SERIAL",
                       client_id="CLIENT", environment="Production")
    connector = frappe._dict(**row)
    connector.get_password = lambda field: "test-secret"
    monkeypatch.setattr(cloud, "legacy_rows", lambda *a: [row])
    monkeypatch.setattr(frappe.local, "db", SimpleNamespace(exists=lambda *a: True), raising=False)
    monkeypatch.setattr(frappe, "get_doc", lambda *a: connector)
    monkeypatch.setattr(frappe.utils.password, "get_decrypted_password", lambda *a: "test-secret")
    cloud.verify_connector_mappings(saved)
    connector.environment = "Pre-Production"
    with pytest.raises(frappe.ValidationError, match="environment"):
        cloud.verify_connector_mappings(saved)


def test_cleanup_reanchors_unrelated_fields_without_dropping_columns(monkeypatch):
    calls = []
    old = frappe._dict(name="POS Invoice-eta_old", dt="POS Invoice",
                      fieldname="eta_old", insert_after="customer")
    custom = frappe._dict(name="POS Invoice-custom_note", dt="POS Invoice", insert_after="eta_old")
    monkeypatch.setattr(cloud, "canonical_fields", lambda: [])
    monkeypatch.setattr(frappe, "get_all", lambda *a, **kw: [old] if "filters" in kw else [old, custom])
    monkeypatch.setattr(frappe.local, "db", SimpleNamespace(
        set_value=lambda *a: calls.append(a)
    ), raising=False)
    monkeypatch.setattr(frappe, "delete_doc", lambda *a, **kw: calls.append(a))
    cloud.remove_retired_fields([old.name])
    assert calls == [
        ("Custom Field", custom.name, "insert_after", "customer"),
        ("Custom Field", old.name),
    ]


def test_cleanup_preserves_compliance_doctypes_and_checks_before_commit(monkeypatch):
    saved = state()
    saved["metadata"] = {
        "DocType": ["ETA UOM", "ETA Logs"], "Module Def": ["ETA"],
        "Custom Field": ["POS Invoice-eta_uuid"],
    }
    calls = []
    monkeypatch.setattr(cloud, "canonical_doctypes", lambda: {"ETA UOM"})
    monkeypatch.setattr(cloud, "canonical_fields", lambda: [{"name": "POS Invoice-eta_uuid"}])
    monkeypatch.setattr(cloud, "remove_retired_fields", lambda names: None)
    monkeypatch.setattr(frappe.local, "db", SimpleNamespace(
        set_value=lambda *a: calls.append(("set", *a)),
        exists=lambda *a: True, get_value=lambda *a: "ETA",
    ), raising=False)
    monkeypatch.setattr(frappe, "get_all", lambda *a, **kw: [])
    monkeypatch.setattr(frappe, "delete_doc", lambda *a, **kw: calls.append(("delete", *a)))
    monkeypatch.setattr(frappe, "clear_cache", lambda: None)
    monkeypatch.setattr(frappe, "get_single", lambda dt: SimpleNamespace(update_versions=lambda: None))
    monkeypatch.setattr(cloud, "verify", lambda current=None: calls.append(("verify", (current or {}).get("phase"))))
    monkeypatch.setattr(cloud, "save_state", lambda current: calls.append(("commit", current["phase"])))
    assert cloud.cleanup_metadata(saved)["legacy_tables_retained"]
    assert ("delete", "DocType", "ETA Logs") in calls
    assert ("delete", "DocType", "ETA UOM") not in calls
    assert ("delete", "Custom Field", "POS Invoice-eta_uuid") not in calls
    assert calls[-2:] == [("verify", "complete"), ("commit", "complete")]


def test_cleanup_restores_uninstall_flag_on_failure(monkeypatch):
    monkeypatch.setattr(cloud, "require_paused", lambda: None)
    monkeypatch.setattr(cloud, "load_state", state)
    monkeypatch.setattr(cloud, "verify", lambda: None)
    def fail(saved):
        raise frappe.ValidationError("ownership changed")
    monkeypatch.setattr(cloud, "cleanup_metadata", fail)
    with pytest.raises(frappe.ValidationError, match="ownership changed"):
        cloud.cleanup()
    assert not frappe.flags.in_uninstall
