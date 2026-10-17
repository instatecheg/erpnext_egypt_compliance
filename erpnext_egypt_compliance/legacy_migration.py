"""Explicit, offline ETAMS retirement checks. No ETA API calls are made here."""

import hashlib
import itertools
import json
from pathlib import Path

import frappe
from frappe.utils import now_datetime


MANIFEST = "etams-retirement-manifest"


def archive_name(doctype, name):
    return hashlib.sha256(json.dumps([doctype, name]).encode()).hexdigest()


def encode(row):
    return json.dumps(row, sort_keys=True, default=str, ensure_ascii=False)


def source_rows(doctype, fields=None):
    last = ""
    while True:
        rows = frappe.get_all(
            doctype, fields=fields or ["*"], filters={"name": [">", last]},
            order_by="name asc", limit_page_length=250,
        )
        if not rows:
            return
        yield from rows
        last = rows[-1].name


def digest_rows(rows):
    digest = hashlib.sha256()
    count = 0
    for row in rows:
        digest.update((encode(row) + "\n").encode())
        count += 1
    return {"count": count, "sha256": digest.hexdigest()}


def verify_archive(tables):
    for doctype, expected in tables.items():
        if expected.get("missing_table"):
            continue
        rows = frappe.get_all(
            "ETA Legacy Record", filters={"source_doctype": doctype},
            fields=["payload"], order_by="source_name asc", limit_page_length=0,
        )
        digest = hashlib.sha256()
        for row in rows:
            digest.update((row.payload + "\n").encode())
        if {"count": len(rows), "sha256": digest.hexdigest()} != expected:
            raise frappe.ValidationError(f"Archive contents changed: {doctype}")


def invoice_snapshot():
    result = {}
    for doctype in ("Sales Invoice", "POS Invoice"):
        columns = set(frappe.db.get_table_columns(doctype))
        fields = sorted({"name", "docstatus"} | {col for col in columns if col.startswith("eta_")})
        result[doctype] = digest_rows(source_rows(doctype, fields))
    return result


def require_paused():
    if not frappe.conf.pause_scheduler:
        raise frappe.ValidationError("Disable the site scheduler before preparing ETAMS removal.")
    if "erpnext_egypt_compliance" not in frappe.get_installed_apps():
        raise frappe.ValidationError("Install compliance before retiring ETAMS.")


def prepare():
    """Archive ETAMS tables, preserving each raw row and verifying its contents."""
    require_paused()
    if "etams" not in frappe.get_installed_apps():
        raise frappe.ValidationError("ETAMS is not installed on this site.")
    if frappe.db.count("ETA Receipt Settings"):
        raise frappe.ValidationError(
            "Migrate and verify all ETA Receipt Settings connectors before retirement; "
            "automatic credential conversion is not supported."
        )
    if frappe.get_all(
        "Client Script", filters={"enabled": 1, "script": ["like", "%etams%"]}, limit=1
    ) or frappe.get_all(
        "Server Script", filters={"disabled": 0, "script": ["like", "%etams%"]}, limit=1
    ):
        raise frappe.ValidationError("Disable database scripts referencing etams before retirement.")
    counts = {}
    for doctype in frappe.get_all("DocType", filters={"module": "ETA"}, pluck="name"):
        if not frappe.db.table_exists(doctype):
            counts[doctype] = {"missing_table": True}
            continue
        if doctype == "ETA Token Cache":
            continue
        source_digest = hashlib.sha256()
        count = 0
        rows = iter(source_rows(doctype))
        while batch := list(itertools.islice(rows, 250)):
            existing = set(frappe.get_all(
                "ETA Legacy Record",
                filters={"name": ["in", [archive_name(doctype, row.name) for row in batch]]},
                pluck="name",
            ))
            inserts = []
            timestamp = now_datetime()
            for row in batch:
                payload = encode(row)
                source_digest.update((payload + "\n").encode())
                key = archive_name(doctype, row.name)
                values = {
                    "source_doctype": doctype, "source_name": row.name,
                    "source_parent": row.get("parent"),
                    "receipt_uuid": row.get("eta_uuid") or row.get("uuid"),
                    "payload": payload,
                }
                if key in existing:
                    frappe.db.set_value("ETA Legacy Record", key, values, update_modified=False)
                else:
                    inserts.append((
                        key, timestamp, timestamp, "Administrator", "Administrator", 0,
                        *values.values(),
                    ))
                count += 1
            if inserts:
                frappe.db.bulk_insert(
                    "ETA Legacy Record",
                    ["name", "creation", "modified", "owner", "modified_by", "docstatus",
                     "source_doctype", "source_name", "source_parent", "receipt_uuid", "payload"],
                    inserts, chunk_size=250,
                )
            frappe.db.commit()
        counts[doctype] = {"count": count, "sha256": source_digest.hexdigest()}
        archived = frappe.get_all(
            "ETA Legacy Record", filters={"source_doctype": doctype},
            fields=["payload"], order_by="source_name asc", limit_page_length=0,
        )
        archived_digest = hashlib.sha256()
        for row in archived:
            archived_digest.update((row.payload + "\n").encode())
        if counts[doctype] != {"count": len(archived), "sha256": archived_digest.hexdigest()}:
            raise frappe.ValidationError(f"Archive verification failed for {doctype}")
        frappe.db.commit()
        print(f"Verified archive: {doctype}: {count} records")
    manifest = {"tables": counts, "invoices": invoice_snapshot()}
    values = {"source_doctype": "Migration Manifest", "source_name": "etams", "payload": encode(manifest)}
    if frappe.db.exists("ETA Legacy Record", MANIFEST):
        frappe.db.set_value("ETA Legacy Record", MANIFEST, values)
    else:
        frappe.get_doc({"doctype": "ETA Legacy Record", **values}).insert(
            ignore_permissions=True, set_name=MANIFEST
        )
    frappe.db.commit()
    return {"archive_counts": counts, "invoice_snapshots": manifest["invoices"]}


def before_etams_uninstall():
    require_paused()
    payload = frappe.db.get_value("ETA Legacy Record", MANIFEST, "payload")
    if not payload:
        raise frappe.ValidationError("Run compliance legacy_migration.prepare before uninstalling ETAMS.")
    manifest = json.loads(payload)
    verify_archive(manifest["tables"])
    if manifest["invoices"] != invoice_snapshot():
        raise frappe.ValidationError("Invoice data changed after preparation. Run prepare again.")
    for doctype, expected in manifest["tables"].items():
        if expected.get("missing_table"):
            if frappe.db.table_exists(doctype):
                raise frappe.ValidationError(f"{doctype} appeared after preparation. Run prepare again.")
            continue
        if digest_rows(source_rows(doctype)) != expected:
            raise frappe.ValidationError(f"{doctype} changed after archive; run prepare again.")

    definitions = json.loads(
        (Path(__file__).parent / "fixtures" / "custom_field.json").read_text()
    )
    preserved = {row["name"] for row in definitions}
    from etams.setup import get_custom_fields

    for doctype, fields in get_custom_fields().items():
        for field in fields:
            name = f"{doctype}-{field['fieldname']}"
            if name not in preserved:
                frappe.db.delete("Custom Field", {"dt": doctype, "fieldname": field["fieldname"]})
    for name in preserved:
        if frappe.db.exists("Custom Field", name):
            frappe.db.set_value("Custom Field", name, "module", "ERPNext ETA", update_modified=False)
    frappe.clear_cache()


def reconcile_retired_fields():
    """Apply canonical receipt metadata after other apps sync their old customizations."""
    if "etams" in frappe.get_installed_apps():
        return
    if not frappe.db.exists("DocType", "ETA Legacy Record"):
        return
    if not frappe.db.exists("ETA Legacy Record", MANIFEST):
        return
    from erpnext_egypt_compliance.patches.v1_0.claim_pos_invoice_eta_fields import execute

    execute()
    retired_targets = (
        "ETA Country", "ETA Tax SubType", "ETA Receipt Settings", "ETA Logs",
        "ETA Activity Codes", "ETA Logs Table", "ETA Documents",
    )
    for field in frappe.get_all(
        "Custom Field", filters={"options": ["in", retired_targets]},
        fields=["name", "dt", "options"],
    ):
        if not frappe.db.exists("DocType", field.options):
            frappe.db.delete("Custom Field", {"name": field.name})
            frappe.clear_cache(doctype=field.dt)


def verify_retirement():
    if "etams" in frappe.get_installed_apps():
        raise frappe.ValidationError("ETAMS is still installed.")
    manifest = json.loads(frappe.db.get_value("ETA Legacy Record", MANIFEST, "payload"))
    if manifest["invoices"] != invoice_snapshot():
        raise frappe.ValidationError("Invoice ETA data differs from the pre-uninstall snapshot.")
    verify_archive(manifest["tables"])
    legacy_jobs = frappe.get_all(
        "Scheduled Job Type", filters={"method": ["like", "etams.%"]}, pluck="name"
    )
    if legacy_jobs:
        raise frappe.ValidationError("Legacy ETAMS scheduled jobs are still registered.")
    from erpnext_egypt_compliance.installation import verify_standalone_installation

    return {"invoice_data_unchanged": True, "archive_verified": True,
            **verify_standalone_installation()}
