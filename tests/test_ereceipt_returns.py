from datetime import timedelta
from unittest.mock import Mock

import frappe
import pytest

from erpnext_egypt_compliance.erpnext_eta import ereceipt_returns, ereceipt_schema


@pytest.fixture(autouse=True)
def db_transaction(monkeypatch):
    # These unit tests must not initialize or mutate a site database.
    def validation_error(message, *args, **kwargs):
        raise frappe.ValidationError(message)

    monkeypatch.setattr(frappe, "throw", validation_error)
    monkeypatch.setattr(frappe.local, "flags", frappe._dict(in_test=False), raising=False)
    monkeypatch.setattr(ereceipt_returns, "_", lambda message: message)


@pytest.fixture
def original():
    doc = frappe._dict(
        name="SALE-1", docstatus=1, is_return=0, eta_status="Valid", eta_uuid="a" * 64,
        company="Company", customer="Customer", currency="EGP", pos_profile="POS",
        posting_date="2026-10-15",
    )
    doc.check_permission = Mock()
    return doc


@pytest.fixture
def return_doc(original):
    doc = original.copy()
    doc.update(name="RETURN-1", return_against="SALE-1", is_return=1, posting_date="2026-10-16")
    return frappe._dict(doc)


def test_valid_original_is_read_permission_checked(monkeypatch, original, return_doc):
    monkeypatch.setattr(ereceipt_returns.frappe, "get_doc", lambda *args: original)
    assert ereceipt_returns.get_original_receipt(return_doc) is original
    original.check_permission.assert_called_once_with("read")


@pytest.mark.parametrize("field,value", [
    ("eta_status", "Submitted"), ("eta_status", "Invalid"), ("docstatus", 2),
    ("is_return", 1), ("eta_uuid", ""), ("eta_uuid", "invalid"),
])
def test_ineligible_original_is_rejected(monkeypatch, original, return_doc, field, value):
    original[field] = value
    monkeypatch.setattr(ereceipt_returns.frappe, "get_doc", lambda *args: original)
    with pytest.raises(frappe.ValidationError):
        ereceipt_returns.get_original_receipt(return_doc)


@pytest.mark.parametrize("field", ["company", "customer", "currency", "pos_profile"])
def test_mismatched_return_is_rejected(monkeypatch, original, return_doc, field):
    return_doc[field] = "Different"
    monkeypatch.setattr(ereceipt_returns.frappe, "get_doc", lambda *args: original)
    with pytest.raises(frappe.ValidationError):
        ereceipt_returns.get_original_receipt(return_doc)


@pytest.mark.parametrize("days,allowed", [(-1, False), (0, True), (540, True), (541, False)])
def test_return_date_boundary(monkeypatch, original, return_doc, days, allowed):
    return_doc.posting_date = frappe.utils.add_days(original.posting_date, days)
    monkeypatch.setattr(ereceipt_returns.frappe, "get_doc", lambda *args: original)
    if allowed:
        assert ereceipt_returns.get_original_receipt(return_doc) is original
    else:
        with pytest.raises(frappe.ValidationError):
            ereceipt_returns.get_original_receipt(return_doc)


def test_missing_original_is_rejected(return_doc):
    return_doc.return_against = ""
    with pytest.raises(frappe.ValidationError):
        ereceipt_returns.get_original_receipt(return_doc)


def test_positive_eta_magnitudes_preserve_discount_and_tax_rate():
    data = {
        "grand_total": -1279,
        "items": [{"qty": -1, "net_amount": -1279, "discount_amount": 0,
                   "distributed_discount_amount": -59, "net_rate": 1279}],
    }
    ereceipt_returns.normalize_return_amounts(data)
    assert data["grand_total"] == 1279
    assert data["items"][0] == {
        "qty": 1, "net_amount": 1279, "discount_amount": 0,
        "distributed_discount_amount": 59, "net_rate": 1279,
    }


@pytest.mark.parametrize("total,qty,net", [(100, -1, -100), (-100, 1, -100), (-100, 0, 0), (-100, -1, 100)])
def test_invalid_signs_are_not_silently_converted(total, qty, net):
    with pytest.raises(frappe.ValidationError):
        ereceipt_returns.normalize_return_amounts(
            {"grand_total": total, "items": [{"qty": qty, "net_amount": net}]}
        )


@pytest.mark.parametrize("is_return", [False, True])
def test_receipt_reference_is_return_only(monkeypatch, is_return):
    monkeypatch.setattr(ereceipt_schema, "POS_INVOICE_RAW_DATA", {
        "posting_date": frappe.utils.getdate("2026-10-16"), "posting_time": timedelta(hours=12),
        "name": "RETURN-1", "currency": "EGP", "is_return": is_return,
        "_original_receipt_uuid": "a" * 64, "eta_status": "Invalid", "eta_uuid": "b" * 64,
    })
    header = ereceipt_schema.get_pos_ereceipt_header().model_dump(exclude_none=True)
    assert header["referenceOldUUID"] == "b" * 64
    if is_return:
        assert header["referenceUUID"] == "a" * 64
    else:
        assert "referenceUUID" not in header


@pytest.mark.parametrize("status", ["Submitted", "Valid", "Cancelled"])
def test_return_duplicate_submission_is_blocked_before_build(monkeypatch, status):
    doc = frappe._dict(
        is_return=1, docstatus=1, pos_profile="POS", eta_status=status,
        check_permission=Mock(),
    )
    monkeypatch.setattr(frappe, "get_doc", lambda *args: doc)
    monkeypatch.setattr(frappe, "log_error", Mock())
    monkeypatch.setattr(frappe, "get_traceback", lambda: "validation failed")
    build = Mock(side_effect=AssertionError("Must not build or send a duplicate"))
    monkeypatch.setattr(ereceipt_schema, "build_erceipt_json", build)
    monkeypatch.setattr(ereceipt_schema, "_", lambda message: message)
    with pytest.raises(frappe.ValidationError, match="already has an ETA submission"):
        ereceipt_schema.submit_ereceipt("RETURN-1", "POS", "POS Invoice")
    build.assert_not_called()


@pytest.mark.parametrize("docstatus,profile,message", [
    (0, "POS", "locally"), (2, "POS", "locally"), (1, "Different", "connector"),
])
def test_return_submission_requires_local_submission_and_own_connector(
    monkeypatch, docstatus, profile, message
):
    doc = frappe._dict(
        is_return=1, docstatus=docstatus, pos_profile="POS", eta_status="",
        check_permission=Mock(),
    )
    monkeypatch.setattr(frappe, "get_doc", lambda *args: doc)
    monkeypatch.setattr(frappe, "log_error", Mock())
    monkeypatch.setattr(frappe, "get_traceback", lambda: "validation failed")
    monkeypatch.setattr(ereceipt_schema, "_", lambda message: message)
    with pytest.raises(frappe.ValidationError, match=message):
        ereceipt_schema.submit_ereceipt("RETURN-1", profile, "POS Invoice")


@pytest.mark.parametrize("field", ["eta_uuid", "eta_submissionid"])
def test_return_submission_with_tracking_but_blank_status_is_blocked(monkeypatch, field):
    doc = frappe._dict(
        is_return=1, docstatus=1, pos_profile="POS", eta_status="",
        check_permission=Mock(),
    )
    doc[field] = "already-submitted"
    monkeypatch.setattr(frappe, "get_doc", lambda *args: doc)
    monkeypatch.setattr(frappe, "log_error", Mock())
    monkeypatch.setattr(frappe, "get_traceback", lambda: "validation failed")
    monkeypatch.setattr(ereceipt_schema, "_", lambda message: message)
    with pytest.raises(frappe.ValidationError, match="already has an ETA submission"):
        ereceipt_schema.submit_ereceipt("RETURN-1", "POS", "POS Invoice")
