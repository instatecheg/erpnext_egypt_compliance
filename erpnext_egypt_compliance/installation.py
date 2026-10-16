import json
from pathlib import Path

import frappe


def verify_standalone_installation():
    """Read-only check for a fresh site installed without legacy ETA apps."""
    installed = frappe.get_installed_apps()
    if "erpnext_egypt_compliance" not in installed or "erpnext" not in installed:
        raise frappe.ValidationError("Install ERPNext and Egypt Compliance first.")
    if {"etams", "e_invoice"}.intersection(installed):
        raise frappe.ValidationError("Run this check on a fresh site without legacy ETA apps.")

    fixture = Path(__file__).parent / "fixtures" / "custom_field.json"
    definitions = json.loads(fixture.read_text())
    errors = []
    receipt_fields = [
        field for field in definitions
        if field["dt"] == "POS Invoice"
        or field["name"] in {"Item-eta_egs_code", "Item-eta_gs1_code"}
    ]
    for definition in receipt_fields:
        meta = frappe.get_meta(definition["dt"])
        field = meta.get_field(definition["fieldname"])
        if not field:
            errors.append(f"Missing field: {definition['name']}")
            continue
        for key in ("fieldtype", "options", "module", "insert_after", "depends_on"):
            if (field.get(key) or "") != (definition.get(key) or ""):
                errors.append(f"Incorrect {key}: {definition['name']}")
        anchor = field.insert_after
        if anchor and not meta.has_field(anchor):
            errors.append(f"Missing layout anchor {anchor}: {definition['name']}")
        if field.fieldtype not in {"Section Break", "Column Break", "Tab Break", "Image", "HTML"}:
            if not frappe.db.has_column(definition["dt"], definition["fieldname"]):
                errors.append(f"Missing database column: {definition['name']}")

    for doctype in ("ETA Connector", "ETA POS Connector", "ETA Log", "ETA Log Documents"):
        if not frappe.db.exists("DocType", doctype):
            errors.append(f"Missing DocType: {doctype}")
    if errors:
        raise frappe.ValidationError("\n".join(errors))
    return {"installed_apps": installed, "verified_receipt_fields": len(receipt_fields)}
