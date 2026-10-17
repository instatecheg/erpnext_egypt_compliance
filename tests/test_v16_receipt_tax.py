from types import SimpleNamespace

import frappe
import pytest

from erpnext_egypt_compliance.erpnext_eta import ereceipt_schema as schema
from erpnext_egypt_compliance.erpnext_eta.utils import get_item_tax_rate


@pytest.fixture(autouse=True)
def db_transaction(monkeypatch):
    monkeypatch.setattr(
        frappe.local, "db",
        SimpleNamespace(get_value=lambda *a: "T1"), raising=False,
    )


def test_v16_child_tax_rate_is_used_with_receipt_tax_mapping(monkeypatch):
    monkeypatch.setattr(schema, "POS_INVOICE_RAW_DATA", {
        "taxes": [frappe._dict(name="TAX-1", eta_tax_type="T1", eta_tax_sub_type="V009")],
        "item_wise_tax_details": [{"item_row": "ITEM-1", "tax_row": "TAX-1", "rate": 14}],
    })
    result = schema._get_taxable_items({
        "name": "ITEM-1", "item_code": "FISH", "net_rate": 100, "qty": 2
    })
    assert len(result) == 1
    assert result[0].taxType == "T1"
    assert result[0].subType == "V009"
    assert result[0].rate == 14
    assert result[0].amount == 28


def test_tax_rate_supports_legacy_json_for_restored_invoices():
    assert get_item_tax_rate({}, "ITEM-1", "TAX-1", "FISH", {
        "item_wise_tax_detail": '{"FISH": [14, 28]}'
    }) == 14
