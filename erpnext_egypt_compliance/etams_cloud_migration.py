"""Explicit, offline retirement of a restored ETAMS database without ETAMS code."""

import itertools
import json
from pathlib import Path

import frappe
from frappe.utils import cint, now_datetime
from frappe.utils.synchronization import filelock

from erpnext_egypt_compliance import legacy_migration as legacy

STATE = "etams-cloud-migration"
PREFIX = "ETAMS Cloud: "
APP = "erpnext_egypt_compliance"
LEGACY_DOCTYPES = {
    "ETA Logs", "ETA Logs Table", "ETA Country", "ETA Activity Codes",
    "ETA Tax SubType", "ETA Receipt Settings", "ETA Documents",
    "ETA Settings", "ETA UOM", "ETA Tax Type", "ETA Default Address By Customer Type", "ETA Token Cache",
}
CONNECTOR_TYPES = {"ETA Receipt Settings": "ETA POS Connector", "ETA Settings": "ETA Connector"}
METADATA = ("DocType", "Custom Field", "Workspace", "Report", "Page", "Web Form", "Print Format")
INVOICE_FIELDS = {
    "eta_tab", "eta_status", "eta_validate_status", "eta_document_status", "eta_column_section",
    "eta_submissionid", "eta_attached_qr", "eta_qr", "eta_qr_data", "eta_success_section",
    "eta_uuid", "eta_hashkey", "etas_column_section", "eta_reference_old_uuid", "eta_previous_uuid",
    "eta_issued_datetime", "eta_send_with_current_date", "eta_longid", "eta_failure_section",
    "eta_error_message", "eta_cr_message",
}
LEGACY_FIELDS = {
    "UOM": {"eta_uom"}, "Customer": {"eta_tax_type"},
    "Item": {"eta_coding_section", "eta_egs_code", "eta_column_section", "eta_gs1_code"},
    "Address": {
        "eta_address_section", "eta_country", "eta_governorate", "eta_regioncity", "eta_street",
        "eta_buildingnumber", "eta_column_section", "eta_postalcode", "eta_floor", "eta_room",
        "eta_landmark", "eta_additionalinformation",
    },
    "Item Tax Template Detail": {"eta_tax_type", "eta_tax_subtype"},
    "POS Invoice": INVOICE_FIELDS,
    "Sales Invoice": INVOICE_FIELDS | {
        "is_export_invoice", "delivery_date", "eta_milestone_section", "eta_milestone_eligibility",
        "eta_milestone_building", "eta_milestone_validation", "eta_milestone_submission",
        "eta_milestone_error", "eta_status_section", "eta_submission_mode", "eta_receipt_settings",
        "eta_duplicate_section", "eta_duplicate_count", "eta_duplicate_uuids",
    },
}


def require_manager():
    frappe.only_for("System Manager")


def require_paused():
    if not frappe.conf.pause_scheduler:
        raise frappe.ValidationError("Pause the site scheduler and drain all writers before migration.")


def load_state():
    if not frappe.db.exists("DocType", "ETA Legacy Record"):
        return None
    payload = frappe.db.get_value("ETA Legacy Record", STATE, "payload")
    return json.loads(payload) if payload else None


def save_state(state):
    values = {
        "source_doctype": "Migration Manifest", "source_name": STATE,
        "payload": legacy.encode(state),
    }
    if frappe.db.exists("ETA Legacy Record", STATE):
        frappe.db.set_value("ETA Legacy Record", STATE, values)
    else:
        frappe.get_doc({"doctype": "ETA Legacy Record", **values}).insert(
            ignore_permissions=True, set_name=STATE
        )
    frappe.db.commit()


def raw_rows(doctype, fields=None):
    """Read physical columns without loading a legacy controller or its schema."""
    table = frappe.qb.DocType(doctype)
    last = ""
    while True:
        query = frappe.qb.from_(table).select(
            *([table[field] for field in fields] if fields else [table.star])
        ).where(table.name > last).orderby(table.name).limit(250)
        rows = query.run(as_dict=True)
        if not rows:
            return
        yield from rows
        last = rows[-1].name


def legacy_rows(doctype, expected=None):
    if expected and expected.get("issingle"):
        yield {"name": doctype, **frappe.db.get_singles_dict(doctype)}
    else:
        yield from raw_rows(doctype, expected.get("columns") if expected else None)


def snapshot(columns):
    return {dt: legacy.digest_rows(raw_rows(dt, fields)) for dt, fields in columns.items()}


def archive_rows(label, rows):
    rows = iter(rows)
    while batch := list(itertools.islice(rows, 250)):
        timestamp = now_datetime()
        existing = set(frappe.get_all(
            "ETA Legacy Record",
            filters={"name": ["in", [legacy.archive_name(PREFIX + label, row["name"]) for row in batch]]},
            pluck="name",
        ))
        inserts = []
        for row in batch:
            name = legacy.archive_name(PREFIX + label, row["name"])
            values = {
                "source_doctype": PREFIX + label, "source_name": row["name"],
                "source_parent": row.get("parent"), "payload": legacy.encode(row),
            }
            if name in existing:
                frappe.db.set_value("ETA Legacy Record", name, values, update_modified=False)
            else:
                inserts.append((name, timestamp, timestamp, "Administrator", "Administrator", 0,
                                *values.values()))
        if inserts:
            frappe.db.bulk_insert(
                "ETA Legacy Record",
                ["name", "creation", "modified", "owner", "modified_by", "docstatus",
                 "source_doctype", "source_name", "source_parent", "payload"],
                inserts, chunk_size=250,
            )
        frappe.db.commit()


def canonical_fields():
    return json.loads((Path(__file__).parent / "fixtures/custom_field.json").read_text())


def canonical_doctypes():
    root = Path(__file__).parent / "erpnext_eta/doctype"
    return {json.loads(path.read_text())["name"] for path in root.glob("*/*.json")}


def inventory():
    modules = frappe.get_all("Module Def", filters={"app_name": "etams"}, pluck="name")
    if frappe.db.exists("Module Def", "ETA") and "ETA" not in modules:
        owner = frappe.db.get_value("Module Def", "ETA", "app_name")
        if owner not in (None, "", "etams"):
            raise frappe.ValidationError("Module ETA belongs to another app; resolve ownership first.")
        modules.append("ETA")
    docs = frappe.get_all("DocType", filters={"module": ["in", modules]}, pluck="name") if modules else []
    for dt in CONNECTOR_TYPES:
        if dt not in docs and frappe.db.table_exists(dt) and "client_id" in frappe.db.get_table_columns(dt):
            docs.append(dt)
    unknown = set(docs) - LEGACY_DOCTYPES
    if unknown:
        raise frappe.ValidationError("Unrecognized ETAMS DocTypes require review: " + ", ".join(sorted(unknown)))
    metadata = {}
    for dt in METADATA:
        rows = frappe.get_all(dt, filters={"module": ["in", modules]}, fields=["*"]) if modules else []
        if dt == "Custom Field":
            candidates = frappe.get_all(
                dt, filters={"dt": ["in", list(LEGACY_FIELDS)]}, fields=["*"]
            )
            rows = list({row.name: row for row in rows + [
                row for row in candidates if row.fieldname in LEGACY_FIELDS.get(row.dt, set())
            ]}.values())
        metadata[dt] = rows
    for dt, field in (("Scheduled Job Type", "method"), ("Client Script", "script"), ("Server Script", "script")):
        metadata[dt] = frappe.get_all(dt, filters={field: ["like", "%etams%"]}, fields=["*"])
    metadata["Property Setter"] = [
        row for row in frappe.get_all(
            "Property Setter", filters={"doc_type": ["in", list(LEGACY_FIELDS)]}, fields=["*"]
        ) if row.field_name in LEGACY_FIELDS.get(row.doc_type, set())
    ]
    for dt in ("DocField", "DocPerm"):
        metadata[dt] = frappe.get_all(dt, filters={"parent": ["in", docs]}, fields=["*"]) if docs else []
    metadata["Module Def"] = [
        frappe.db.get_value("Module Def", name, "*", as_dict=True) for name in modules
    ]
    return docs, metadata


def bootstrap(confirm=None):
    """Bench execute only; run before install-app/migrate on the restored copy."""
    require_manager()
    require_paused()
    if confirm != "ARCHIVE AND DETACH ETAMS":
        raise frappe.ValidationError("Pass confirm='ARCHIVE AND DETACH ETAMS' after backing up and stopping writers.")
    if not frappe.conf.maintenance_mode:
        raise frappe.ValidationError("Enable maintenance mode for command-line bootstrap.")
    with filelock("etams-cloud-migration", timeout=1):
        previous = load_state()
        if previous and previous["phase"] in ("detached", "complete"):
            verify_sources(previous)
            return {"phase": previous["phase"], "already_prepared": True}
        if "etams" not in frappe.get_installed_apps():
            raise frappe.ValidationError("No restored ETAMS registration found. Use the verification page instead.")
        # Only compliance's own storage is synced; no ETAMS hooks or controllers run.
        from frappe.installer import add_module_defs

        add_module_defs(APP, ignore_if_duplicate=True)
        frappe.setup_module_map(include_all_apps=True)
        frappe.reload_doc("erpnext_eta", "doctype", "eta_legacy_record", force=True)
        docs, metadata = inventory()
        columns = {
            dt: sorted({"name", "docstatus"} | {
                c for c in frappe.db.get_table_columns(dt) if c.startswith(("eta_", "etas_"))
            }) for dt in ("Sales Invoice", "POS Invoice")
        }
        state = previous or {
            "phase": "archiving", "columns": columns, "invoices": snapshot(columns),
            "tables": {}, "metadata": {}, "metadata_hashes": {}, "connector_mappings": {},
        }
        save_state(state)
        for dt in docs:
            if dt == "ETA Token Cache":
                continue
            if frappe.db.table_exists(dt):
                archive_rows(dt, raw_rows(dt))
                state["tables"][dt] = {
                    **legacy.digest_rows(raw_rows(dt)),
                    "columns": list(frappe.db.get_table_columns(dt)),
                }
            elif frappe.db.get_value("DocType", dt, "issingle"):
                rows = list(legacy_rows(dt, {"issingle": True}))
                archive_rows(dt, rows)
                state["tables"][dt] = {**legacy.digest_rows(rows), "issingle": True}
            else:
                state["tables"][dt] = {"missing_table": True}
            save_state(state)
            print(f"Archived and fingerprinted: {dt}")
        for dt, rows in metadata.items():
            archive_rows("Metadata " + dt, rows)
            state["metadata"][dt] = [row["name"] for row in rows]
            state["metadata_hashes"][dt] = legacy.digest_rows(sorted(rows, key=lambda row: row["name"]))
        save_state(state)
        verify_sources(state)
        jobs = frappe.get_all(
            "Scheduled Job Type", filters={"method": ["like", "etams.%"]}, pluck="name"
        )
        for name in jobs:
            frappe.db.set_value("Scheduled Job Type", name, "stopped", 1)
        # Do not call remove_app: that imports legacy hooks and drops tables.
        installed = [app for app in frappe.get_installed_apps() if app != "etams"]
        frappe.db.set_global("installed_apps", json.dumps(installed))
        state["phase"] = "detached"
        save_state(state)
        frappe.clear_cache()
        return {"phase": "detached", "next": "Install/migrate compliance, then open /app/eta-migration."}


def verify_sources(state):
    legacy.verify_archive({
        PREFIX + dt: {k: v for k, v in expected.items() if k not in ("columns", "issingle")}
        for dt, expected in state["tables"].items()
    })
    for dt, expected in state["metadata_hashes"].items():
        archived = frappe.get_all(
            "ETA Legacy Record", filters={"source_doctype": PREFIX + "Metadata " + dt},
            fields=["source_name", "payload"], limit_page_length=0,
        )
        actual = legacy.digest_rows(
            json.loads(row.payload) for row in sorted(archived, key=lambda row: row.source_name)
        )
        if actual != expected:
            raise frappe.ValidationError(f"Archived metadata changed: {dt}")
    if snapshot(state["columns"]) != state["invoices"]:
        raise frappe.ValidationError("Invoice ETA values or invoice identities changed since bootstrap.")
    if state["phase"] != "complete":
        for dt, expected in state["tables"].items():
            exists = expected.get("issingle") or frappe.db.table_exists(dt)
            if expected.get("missing_table"):
                if exists:
                    raise frappe.ValidationError(f"Legacy table appeared after bootstrap: {dt}")
            elif not exists or legacy.digest_rows(legacy_rows(dt, expected)) != {
                k: v for k, v in expected.items() if k not in ("columns", "issingle")
            }:
                raise frappe.ValidationError(f"Legacy source changed after bootstrap: {dt}")


def blockers():
    issues = []
    for dt, enabled in (("Client Script", {"enabled": 1}), ("Server Script", {"disabled": 0})):
        for row in frappe.get_all(dt, filters={**enabled, "script": ["like", "%etams%"]}, pluck="name"):
            issues.append(f"Disable or replace {dt}: {row}")
    if "etams" in frappe.get_installed_apps():
        issues.append("Run the explicit command-line bootstrap before cleanup.")
    if APP not in frappe.get_installed_apps():
        issues.append("Install compliance before cleanup.")
    retired = sorted(LEGACY_DOCTYPES - canonical_doctypes())
    for row in frappe.get_all(
        "DocField", filters={"options": ["in", retired]}, fields=["parent", "fieldname", "options"]
    ):
        if row.parent not in LEGACY_DOCTYPES:
            issues.append(f"Replace legacy link in {row.parent}.{row.fieldname}: {row.options}")
    for row in frappe.get_all(
        "Custom Field", filters={"options": ["in", retired]}, fields=["dt", "fieldname", "options"]
    ):
        if row.fieldname not in LEGACY_FIELDS.get(row.dt, set()):
            issues.append(f"Replace legacy custom link in {row.dt}.{row.fieldname}: {row.options}")
    state = load_state()
    modules = (state or {}).get("metadata", {}).get("Module Def", ["ETA"])
    for link in frappe.get_all(
        "DocField", filters={"fieldtype": "Link", "options": "Module Def"},
        fields=["parent", "fieldname"],
    ):
        if link.parent in METADATA or not frappe.db.exists("DocType", link.parent):
            continue
        if frappe.db.get_value("DocType", link.parent, "issingle"):
            if frappe.db.get_single_value(link.parent, link.fieldname) in modules:
                issues.append(f"Review unsupported legacy module reference: {link.parent}.{link.fieldname}")
        elif frappe.db.table_exists(link.parent) and frappe.get_all(
            link.parent, filters={link.fieldname: ["in", modules]}, limit_page_length=1
        ):
            issues.append(f"Archive/migrate additional module-linked records: {link.parent}")
    return issues


def verify_connector_mappings(state):
    from frappe.utils.password import get_decrypted_password

    targets = set()
    for dt, target_dt in CONNECTOR_TYPES.items():
        expected = state["tables"].get(dt)
        if not expected or expected.get("missing_table") or expected.get("issingle"):
            continue
        for row in legacy_rows(dt, expected):
            target = state["connector_mappings"].get(dt + ":" + row.name)
            if not target or not frappe.db.exists(target_dt, target):
                raise frappe.ValidationError(f"Map legacy {dt} to a saved {target_dt}: {row.name}")
            if (target_dt, target) in targets:
                raise frappe.ValidationError("Map each legacy configuration to a distinct connector.")
            targets.add((target_dt, target))
            connector = frappe.get_doc(target_dt, target)
            fields = ("pos_profile", "serial_number", "client_id") if dt == "ETA Receipt Settings" else ("company", "client_id")
            for field in fields:
                if not row.get(field) or row[field] != connector.get(field):
                    raise frappe.ValidationError(f"Mapped connector differs on {field}: {row.name}")
            if row.get("company") and row.company != connector.get("company"):
                raise frappe.ValidationError(f"Mapped connector company differs: {row.name}")
            for field in ("branch_id", "country_abbr", "governorate", "region_city", "street", "building_number", "company_id"):
                if row.get(field) and str(row[field]) != str(connector.get(field) or ""):
                    raise frappe.ValidationError(f"Mapped connector differs on {field}: {row.name}")
            old_secret = get_decrypted_password(dt, row.name, "client_secret")
            new_secret = connector.get_password("client_secret")
            if not old_secret or old_secret != new_secret:
                raise frappe.ValidationError(f"Mapped connector credentials differ: {row.name}")
            if connector.get("auto_submit_receipts"):
                raise frappe.ValidationError(f"Disable automatic receipts during migration: {target}")
            environment = row.get("environment")
            if not environment and "production_environment" in row:
                environment = "Production" if cint(row.production_environment) else "Pre-Production"
            if environment not in ("Production", "Pre-Production") or environment != connector.get("environment"):
                raise frappe.ValidationError(f"Confirm the compliance environment matches legacy settings: {row.name}")
            if dt + ":" + row.name not in state.get("reviewed_connectors", []):
                raise frappe.ValidationError(f"Confirm review of company, branch, taxes and signing configuration: {row.name}")


@frappe.whitelist()
def get_status():
    require_manager()
    state = load_state()
    mappings = []
    if state and state["phase"] != "complete":
        for dt, target_dt in CONNECTOR_TYPES.items():
            expected = state["tables"].get(dt)
            if expected and not expected.get("missing_table") and not expected.get("issingle"):
                mappings.extend({
                    "legacy_doctype": dt, "target_doctype": target_dt, "legacy_name": row.name,
                    "connector": state["connector_mappings"].get(dt + ":" + row.name),
                    "reviewed": int(dt + ":" + row.name in state.get("reviewed_connectors", [])),
                } for row in raw_rows(dt, ["name"]))
    job = None
    if frappe.db.exists("DocType", "ETA Legacy Record"):
        payload = frappe.db.get_value("ETA Legacy Record", STATE + "-job", "payload")
        job = json.loads(payload) if payload else None
        if job and job["status"] in ("queued", "running"):
            from frappe.utils.background_jobs import get_job_status

            queue_status = get_job_status("etams-cloud-migration")
            if queue_status not in ("queued", "started"):
                job = {**job, "status": "interrupted",
                       "detail": "Worker stopped, job expired or queue entry is missing. Check failed jobs before retrying."}
    old_manifest = None
    if not state and frappe.db.exists("DocType", "ETA Legacy Record"):
        payload = frappe.db.get_value("ETA Legacy Record", legacy.MANIFEST, "payload")
        old_manifest = json.loads(payload) if payload else None
    return {
        "phase": state["phase"] if state else ("retired_legacy" if old_manifest else "not_bootstrapped"),
        "scheduler_paused": bool(frappe.conf.pause_scheduler),
        "blockers": blockers(), "tables": state["tables"] if state else (old_manifest or {}).get("tables", {}),
        "mappings": mappings, "job": job,
        "previous_retirement": bool(old_manifest),
    }


@frappe.whitelist(methods=["POST"])
def set_mappings(mappings):
    require_manager()
    require_paused()
    with filelock("etams-cloud-migration", timeout=1):
        state = load_state()
        if not state or state["phase"] != "detached":
            raise frappe.ValidationError("Bootstrap must complete before mapping connectors.")
        rows = json.loads(mappings) if isinstance(mappings, str) else mappings
        if not isinstance(rows, list) or any(
            not isinstance(row, dict) or not row.get("legacy_name") or not row.get("connector")
            or row.get("legacy_doctype") not in CONNECTOR_TYPES
            for row in rows
        ):
            raise frappe.ValidationError("Each mapping must contain legacy_doctype, legacy_name and connector.")
        state["connector_mappings"] = {row["legacy_doctype"] + ":" + row["legacy_name"]: row["connector"] for row in rows}
        state["reviewed_connectors"] = [
            row["legacy_doctype"] + ":" + row["legacy_name"] for row in rows if cint(row.get("reviewed"))
        ]
        verify_connector_mappings(state)
        save_state(state)
    return {"saved": True}


def verify(state=None):
    state = state or load_state()
    if not state:
        if frappe.db.exists("ETA Legacy Record", legacy.MANIFEST):
            return legacy.verify_retirement()
        from erpnext_egypt_compliance.installation import verify_standalone_installation
        return verify_standalone_installation()
    verify_sources(state)
    problems = blockers()
    if problems:
        raise frappe.ValidationError("\n".join(problems))
    if state["phase"] != "complete":
        verify_connector_mappings(state)
    else:
        leftovers = []
        for dt in state["metadata"].get("DocType", []):
            if dt not in canonical_doctypes() and frappe.db.exists("DocType", dt):
                leftovers.append(dt)
        for dt, field in (("Scheduled Job Type", "method"), ("Client Script", "script"), ("Server Script", "script")):
            leftovers.extend(frappe.get_all(dt, filters={field: ["like", "%etams%"]}, pluck="name"))
        preserved = {row["name"] for row in canonical_fields()}
        leftovers.extend(
            name for name in state["metadata"].get("Custom Field", [])
            if name not in preserved and frappe.db.exists("Custom Field", name)
        )
        leftovers.extend(
            name for name in state["metadata"].get("Module Def", [])
            if frappe.db.exists("Module Def", name)
        )
        if leftovers:
            raise frappe.ValidationError("Legacy metadata remains: " + ", ".join(leftovers))
    from erpnext_egypt_compliance.installation import verify_standalone_installation
    return {"archive_verified": True, "invoice_data_unchanged": True,
            **verify_standalone_installation()}


def remove_retired_fields(names):
    preserved = {row["name"] for row in canonical_fields()}
    removed = {
        row.name: row for row in frappe.get_all(
            "Custom Field", filters={"name": ["in", names]}, fields=["name", "dt", "fieldname", "insert_after"]
        ) if row.name not in preserved
    } if names else {}
    anchors = {(row.dt, row.fieldname): row.insert_after for row in removed.values()}
    for field in frappe.get_all("Custom Field", fields=["name", "dt", "insert_after"]):
        anchor, seen = field.insert_after, set()
        while (field.dt, anchor) in anchors:
            if anchor in seen:
                raise frappe.ValidationError(f"Circular legacy layout anchor: {field.name}")
            seen.add(anchor)
            anchor = anchors[(field.dt, anchor)]
        if anchor != field.insert_after and field.name not in removed:
            frappe.db.set_value("Custom Field", field.name, "insert_after", anchor)
    for name in removed:
        frappe.delete_doc("Custom Field", name, force=True, ignore_permissions=True)


def cleanup():
    require_paused()
    state = load_state()
    if not state or state["phase"] not in ("detached", "complete"):
        raise frappe.ValidationError("Complete bootstrap before cleanup.")
    verify()
    if state["phase"] == "complete":
        return {"already_complete": True}
    previous = frappe.flags.in_uninstall
    frappe.flags.in_uninstall = True
    try:
        return cleanup_metadata(state)
    finally:
        frappe.flags.in_uninstall = previous


def cleanup_metadata(state):
    owned = canonical_doctypes()
    fields = {row["name"] for row in canonical_fields()}
    for name in state["metadata"].get("DocType", []):
        if name in owned:
            frappe.db.set_value("DocType", name, "module", "ERPNext ETA")
    remove_retired_fields(state["metadata"].get("Custom Field", []))
    for name in state["metadata"].get("Property Setter", []):
        if frappe.db.exists("Property Setter", name):
            frappe.delete_doc("Property Setter", name, force=True, ignore_permissions=True)
    for name in state["metadata"].get("Custom Field", []):
        if name in fields:
            frappe.db.set_value("Custom Field", name, "module", "ERPNext ETA")
    for dt in ("Workspace", "Report", "Page", "Web Form", "Print Format", "DocType", "Module Def"):
        for name in state["metadata"].get(dt, []):
            if dt == "DocType" and name in owned:
                continue
            if frappe.db.exists(dt, name):
                if dt != "Module Def":
                    module = frappe.db.get_value(dt, name, "module")
                    if module not in state["metadata"].get("Module Def", []):
                        raise frappe.ValidationError(f"Ownership changed after bootstrap: {dt} {name}")
                frappe.delete_doc(dt, name, force=True, ignore_permissions=True, ignore_on_trash=True)
    for dt, filters in (
        ("Scheduled Job Type", {"method": ["like", "etams.%"]}),
        ("Client Script", {"script": ["like", "%etams%"]}),
        ("Server Script", {"script": ["like", "%etams%"]}),
    ):
        for name in frappe.get_all(dt, filters=filters, pluck="name"):
            if name not in state["metadata"].get(dt, []):
                raise frappe.ValidationError(f"New legacy reference appeared after bootstrap: {dt} {name}")
            frappe.delete_doc(dt, name, force=True, ignore_permissions=True)
    frappe.clear_cache()
    frappe.get_single("Installed Applications").update_versions()
    # Do not drop invoice columns, source tables, or encrypted legacy credentials.
    verify()
    state["phase"] = "complete"
    verify(state)
    save_state(state)
    return {"archive_verified": True, "invoice_data_unchanged": True, "legacy_tables_retained": True}


def reconcile_completed_migration():
    state = load_state()
    if not state or state["phase"] != "complete":
        return
    if "etams" in frappe.get_installed_apps():
        raise frappe.ValidationError("ETAMS was reintroduced after cloud retirement.")
    from erpnext_egypt_compliance.patches.v1_0.claim_pos_invoice_eta_fields import execute

    remove_retired_fields(state["metadata"].get("Custom Field", []))
    for name in state["metadata"].get("Property Setter", []):
        if frappe.db.exists("Property Setter", name):
            frappe.delete_doc("Property Setter", name, force=True, ignore_permissions=True)
    execute()


def save_job(values):
    name = STATE + "-job"
    values = {"source_doctype": "Migration Job", "source_name": name, "payload": legacy.encode(values)}
    if frappe.db.exists("ETA Legacy Record", name):
        frappe.db.set_value("ETA Legacy Record", name, values)
    else:
        frappe.get_doc({"doctype": "ETA Legacy Record", **values}).insert(ignore_permissions=True, set_name=name)
    frappe.db.commit()


@frappe.whitelist(methods=["POST"])
def start_action(action, confirmation=None):
    require_manager()
    if action not in ("verify", "cleanup"):
        raise frappe.ValidationError("Unknown migration action.")
    require_paused()
    if action == "cleanup" and confirmation != "CLEAN UP ETAMS":
        raise frappe.ValidationError("Confirm cleanup after reviewing blockers and stopping all writers.")
    from frappe.utils.background_jobs import is_job_enqueued

    if is_job_enqueued("etams-cloud-migration"):
        raise frappe.ValidationError("A migration action is already queued or running.")
    save_job({"action": action, "status": "queued", "queued": str(now_datetime())})
    frappe.enqueue(
        "erpnext_egypt_compliance.etams_cloud_migration.run_action",
        queue="long", timeout=7200, action=action, enqueue_after_commit=True,
        job_id="etams-cloud-migration", deduplicate=True,
    )
    return {"queued": True}


def run_action(action):
    require_manager()
    with filelock("etams-cloud-migration", timeout=1):
        save_job({"action": action, "status": "running", "started": str(now_datetime())})
        try:
            require_paused()
            if action not in ("verify", "cleanup"):
                raise frappe.ValidationError("Unknown migration action.")
            result = cleanup() if action == "cleanup" else verify()
        except Exception:
            frappe.db.rollback()
            error = frappe.log_error(title="ETAMS migration failed")
            save_job({"action": action, "status": "failed", "error_log": error.name})
            raise
        save_job({"action": action, "status": "passed", "result": result, "finished": str(now_datetime())})
