import json
from pathlib import Path

import pytest

from erpnext_egypt_compliance import hooks
from erpnext_egypt_compliance.patches.v1_0 import claim_pos_invoice_eta_fields


APP = Path(__file__).parents[1] / "erpnext_egypt_compliance"
FIELDS = json.loads((APP / "fixtures" / "custom_field.json").read_text())
RECEIPT_FIELDS = [
    field for field in FIELDS
    if field["dt"] == "POS Invoice"
    or field["name"] in {"Item-eta_egs_code", "Item-eta_gs1_code"}
]


@pytest.fixture(autouse=True)
def db_transaction():
    """These packaging tests do not require a Frappe site."""
    yield


def test_erpnext_is_the_only_required_app():
    assert hooks.required_apps == ["erpnext"]


def test_receipt_fields_are_packaged_and_exported():
    names = {
        name for fixture in hooks.fixtures if fixture["dt"] == "Custom Field"
        for key, operator, values in fixture["filters"]
        if key == "name" and operator == "in" for name in values
    }
    assert len(RECEIPT_FIELDS) == 23
    assert {field["name"] for field in RECEIPT_FIELDS} <= names
    assert all(field["module"] == "ERPNext ETA" for field in RECEIPT_FIELDS)


def test_receipt_layout_anchors_do_not_require_etams():
    for field in RECEIPT_FIELDS:
        standard = json.loads(
            (APP.parents[1] / "erpnext" / "erpnext"
             / ("accounts" if field["dt"] == "POS Invoice" else "stock")
             / "doctype" / field["dt"].lower().replace(" ", "_")
             / (field["dt"].lower().replace(" ", "_") + ".json")).read_text()
        )
        available = {row["fieldname"] for row in standard["fields"]}
        available.update(row["fieldname"] for row in FIELDS if row["dt"] == field["dt"])
        assert field["insert_after"] in available, field["name"]


def test_pos_status_supports_compliance_and_legacy_history():
    field = next(row for row in RECEIPT_FIELDS if row["name"] == "POS Invoice-eta_status")
    assert {"Submitted", "Valid", "Invalid", "Rejected", "Failed", "Accepted"} <= set(
        field["options"].splitlines()
    )
    uuid = next(row for row in RECEIPT_FIELDS if row["name"] == "POS Invoice-eta_uuid")
    assert "Valid" in uuid["depends_on"]


def test_upgrade_uses_the_same_definitions_as_fresh_install(monkeypatch):
    calls = []
    monkeypatch.setattr(
        claim_pos_invoice_eta_fields, "create_custom_fields",
        lambda fields, **kwargs: calls.append((fields, kwargs)),
    )
    claim_pos_invoice_eta_fields.execute()
    fields, options = calls[0]
    assert sorted(field["name"] for rows in fields.values() for field in rows) == sorted(
        field["name"] for field in RECEIPT_FIELDS
    )
    assert options == {"update": True}
