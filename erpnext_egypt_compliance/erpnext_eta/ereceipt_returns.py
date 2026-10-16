import re

import frappe
from frappe import _
from frappe.utils import date_diff, flt


def get_original_receipt(doc):
    if not doc.get("return_against"):
        frappe.throw(_("A return eReceipt must reference the original POS Invoice."))
    original = frappe.get_doc("POS Invoice", doc.return_against)
    original.check_permission("read")
    if original.docstatus != 1 or original.is_return or original.get("eta_status") != "Valid":
        frappe.throw(_("The original POS Invoice must have a Valid ETA sales receipt."))
    if not re.fullmatch(r"[0-9a-fA-F]{64}", original.get("eta_uuid") or ""):
        frappe.throw(_("The original POS Invoice must have a valid ETA receipt UUID."))
    for field in ("company", "customer", "currency", "pos_profile"):
        if doc.get(field) != original.get(field):
            frappe.throw(_("The return and original POS Invoice must have the same {0}.").format(field))
    age = date_diff(doc.posting_date, original.posting_date)
    if age < 0 or age > 540:
        frappe.throw(_("Issue the return within 540 days of the original receipt, not before it."))
    return original


@frappe.whitelist()
def make_return_ereceipt(source_name, target_doc=None):
    """Map an unsaved ERPNext return for review; never submit it locally or to ETA."""
    from erpnext.accounts.doctype.pos_invoice.pos_invoice import make_sales_return

    frappe.has_permission("POS Invoice", "create", throw=True)
    original = frappe.get_doc("POS Invoice", source_name)
    original.check_permission("read")
    draft = make_sales_return(source_name, target_doc)
    get_original_receipt(draft)
    for field in draft.meta.fields:
        if field.fieldname.startswith("eta_") and field.fieldtype not in (
            "Section Break", "Column Break", "Tab Break", "HTML"
        ):
            draft.set(field.fieldname, None)
    return draft


def normalize_return_amounts(data):
    """ERPNext credits are negative; ETA return receipts use positive magnitudes."""
    if flt(data.get("grand_total")) >= 0:
        frappe.throw(_("A return eReceipt must have a negative ERPNext grand total."))
    for item in data.get("items") or []:
        if flt(item.get("qty")) >= 0 or flt(item.get("net_amount")) > 0:
            frappe.throw(_("Return eReceipt items must have negative quantities and non-positive net amounts."))
        for field in ("qty", "net_amount", "discount_amount", "distributed_discount_amount"):
            item[field] = abs(flt(item.get(field)))
    data["grand_total"] = abs(flt(data["grand_total"]))
