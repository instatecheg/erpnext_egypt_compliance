import collections
import hashlib
import json
import re
from datetime import datetime
from typing import Dict, List, Optional
from uuid import uuid4

import frappe
import frappe.utils
import pytz
from pydantic import BaseModel, Field, validator

from erpnext_egypt_compliance.erpnext_eta.utils import (
    eta_datetime_issued_format,
    get_company_eta_connector,
    get_item_tax_rate,
)
from frappe import _
from erpnext_egypt_compliance.erpnext_eta.ereceipt_submitter import EReceiptSubmitter
from erpnext_egypt_compliance.erpnext_eta.ereceipt_returns import (
    get_original_receipt,
    normalize_return_amounts,
)

POS_INVOICE_RAW_DATA = {}
COMPANY_DATA = {}


def convert_datetime_to_utc_with_z_suffix(date_time: datetime) -> str:
    return (
        pytz.timezone("Africa/Cairo")
        .localize(date_time, is_dst=None)
        .astimezone(pytz.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def serialize(document_structure):
    """
    Serialize a document structure to a normalized string.
    see https://sdk.preprod.invoicing.eta.gov.eg/document-serialization-approach/
    """
    if isinstance(document_structure, dict):
        
        serialized_string = ""

        for name, value in document_structure.items():
            if isinstance(value, list):
                list_key = name
                serialized_string += '"' + list_key.upper() + '"'
                for item in value:
                    serialized_string += '"' + name.upper() + '"'
                    serialized_string += serialize(item)

            else:
                serialized_string += '"' + name.upper() + '"'
                serialized_string += serialize(value)
        return serialized_string
    else:
         return '"' + str(document_structure) + '"'


class Beneficiary(BaseModel):
    amount: float = Field(default=0.0, description="Amount of the beneficiary.")
    rate: float = Field(default=0.0, description="Rate of the beneficiary.")


class Contractor(BaseModel):
    name: str = Field(default="", description="Contractor name.")
    amount: float = Field(default=0.0, description="Amount of the contractor.")
    rate: float = Field(default=0.0, description="Rate of the contractor.")


class SingleTaxTotal(BaseModel):
    taxType: str
    amount: float

    @validator("amount")
    def round_float_values(cls, value):
        return frappe.utils.flt(value, 5)


class SingleExtraReceiptDiscountData(BaseModel):
    amount: float
    description: str


class SingleTaxableItems(BaseModel):
    taxType: str = Field(..., description="Tax type.")
    amount: float = Field(default=0.0)
    subType: str = Field(default="", description="Sub type.")
    rate: float = Field(..., ge=0, le=100)

    @validator("taxType")
    def validate_tax_types(cls, value, values):
        allowed_taxable_types = [
            "T1",
            "T2",
            "T3",
            "T4",
            "T5",
            "T6",
            "T7",
            "T8",
            "T9",
            "T10",
            "T11",
            "T12",
        ]
        if value not in allowed_taxable_types:
            raise ValueError(f"Invalid tax type. Allowed types are {allowed_taxable_types}")
        return value

    @validator("subType")
    def validate_sub_types(cls, value, values):
        allowed_sub_types = [
            "V001",
            "V002",
            "V003",
            "V004",
            "V005",
            "V006",
            "V007",
            "V008",
            "V009",
            "V010",
        ]
        if value not in allowed_sub_types:
            raise ValueError(f"Invalid sub type. Allowed types are {allowed_sub_types}")
        return value


class SingleItemDiscountData(BaseModel):
    amount: float = Field(default=0.0, description="Amount of the discount.")
    description: str = Field(default="", description="Description of the discount.")


class SingleCommercialDiscountData(BaseModel):
    amount: float = Field(default=0.0, description="Amount of the discount.")
    description: str = Field(default="", description="Description of the discount.")


class SingleItemData(BaseModel):
    internalCode: str = Field(..., description="Internal code of the item.")
    description: str = Field(..., description="Description of the item.")
    itemType: str = Field(..., description="Item type.")
    itemCode: str = Field(..., description="Item code.")
    unitType: str = Field(..., description="Unit type.")
    quantity: float = Field(..., description="Number of units of the defined unit type being sold.")
    unitPrice: float = Field(..., description="cost per quantity of the product.")
    netSale: float = Field(..., description="Total amount for the receipt line after applying discount.")
    taxableItems: Optional[List[SingleTaxableItems]] = Field(
        description="Structure representing the taxableItems information."
    )
    totalSale: float = Field(..., description="Total amount for the receipt line after applying discount.")
    total: float = Field(..., description="Total amount for the receipt line after applying discount.")

    commercialDiscountData: Optional[List[SingleCommercialDiscountData]] = None
    itemDiscountData: Optional[List[SingleItemDiscountData]] = None
    # valueDifference: Optional[float] = Field(
    #     default=0, description="Value difference when selling goods already taxed."
    # )
    @validator('netSale', 'quantity', 'unitPrice', 'totalSale', 'total', pre=True, always=True)
    def round_float_values(cls, value):
        return frappe.utils.flt(value, 5)


class ReceiptBuyer(BaseModel):
    type: str = Field(
        default="P",
        description="Buyer Type Codes. B:business in Egypt, P:natural person, F:foreigner.",
    )
    id: str = Field(default="", description="Buyer ID.")
    name: str = Field(
        default="",
        description="Registration name of the company or name and surname of the person.",
    )
    mobileNumber: str = Field(default="", description="Mobile number.")
    paymentNumber: str = Field(default="", description="Payment number.")

    @validator("type")
    def validate_type(cls, value):
        allowed_types = ["B", "P", "F"]
        if value not in allowed_types:
            raise ValueError(f"Invalid type. Allowed types are {allowed_types}")
        return value

    @validator("id")
    def validate_id(cls, value, values):
        customer = POS_INVOICE_RAW_DATA.get("customer")
        customer_tax_id = frappe.db.get_value("Customer", customer, "tax_id") or ""
        customer_tax_id = str(customer_tax_id).strip()

        if values.get("type") == "B":
            # Company Registration No. (RIN)
            if not re.fullmatch(r"[0-9]{14}", customer_tax_id):
                frappe.throw(
                _("Customer {0} must have a valid 14-digit Tax ID for business receipts.").format(customer),
                title=_("ETA Validation"),
            )
            return customer_tax_id
        
        if values.get("type") == "P":
            threshold = frappe.db.get_value(
                "ETA POS Connector",
                POS_INVOICE_RAW_DATA.get("pos_profile"),
                "buyer_id_required_amount",
            )
            if threshold is None:
                threshold = 50000
            threshold = frappe.utils.flt(threshold)
            requires_id = bool(threshold) and POS_INVOICE_RAW_DATA.get("grand_total", 0) >= threshold
            is_valid_national_id = bool(re.fullmatch(r"[0-9]{14}", customer_tax_id))
            if requires_id and not is_valid_national_id:
                frappe.throw(
                    _("Customer {0} must have a valid 14-digit national ID because this receipt reaches the configured buyer ID threshold.").format(customer),
                    title=_("ETA Validation"),
                )
            return customer_tax_id if is_valid_national_id else ""

        return customer_tax_id


    @validator("name")
    def get_default_name(cls, value, values):
        value = POS_INVOICE_RAW_DATA.get("customer_name") or "Walk-in Customer"
        return value


class BranchAddress(BaseModel):
    country: str = Field(default="EG", description="Country represented by ISO-3166-2 2 symbol code.")
    governate: str = Field(..., description="Governate.")
    regionCity: str = Field(..., description="Region and city information.")
    street: str = Field(..., description="Street information.")
    buildingNumber: str = Field(..., description=" Building information (number, name or both).")
    postalCode: str = Field(default="", description="Postal code.")
    floor: str = Field(default="", description="Floor information.")
    room: str = Field(default="", description="Room information.")
    landmark: str = Field(default="", description="Landmark information.")
    additionalInformation: str = Field(default="", description="Additional information.")


class ReceiptSeller(BaseModel):
    rin: str = Field(..., description="Registration number.")
    companyTradeName: str = Field(..., description="Registration name of the company.")
    branchCode: str = Field(..., description="Branch code as registered with tax authority")
    # TODO: deviceSerialNumber, syndicateLicenseNumber
    deviceSerialNumber: str = Field(default="", description="This is the POS serial number.")
    syndicateLicenseNumber: str = Field(default="", description="This is the syndicate license number.")
    activityCode: str = Field(..., description="Activity types define the allowed activities for the company.")
    branchAddress: BranchAddress = Field(..., description="Structure representing the branchAddress information.")


class ReceiptDocumentType(BaseModel):
    receiptType: str = Field(default="s", description="Receipt Type Codes. s: Sale Receipt, r: Return Receipt")
    typeVersion: str = Field(default="1.2", description="SDK Version")


class ReceiptHeader(BaseModel):
    dateTimeIssued: str = Field(..., description="Date and time of the receipt issue")
    receiptNumber: str = Field(..., description="Receipt Number. Unique per branch within the same submission")
    uuid: str = Field(default_factory="", description="Unique ID for the receipt")
    previousUUID: str = Field(default="", description="When receipt type is return")
    referenceUUID: Optional[str] = Field(default=None, description="Original sales receipt UUID for returns.")
    referenceOldUUID: str = Field(
        default="",
        description="validation failure and requirement to change something.",
    )
    currency: str = Field(default="EGP", description="Currency Code")
    # exchangeRate: float = Field(default=1.0, description="Exchange rate of the currency to EGP")
    sOrderNameCode: Optional[str] = Field(
        default="",
        description="Reference to the sales order for informational purposes.",
    )
    orderdeliveryMode: str = Field(default="FC", description="Order Delivery Mode Codes.")
    grossWeight: float = Field(default=0.0, description="Total weight of the goods delivered. Unit: KG")
    netWeight: float = Field(default=0.0, description="Net weight of the goods delivered. . Unit: KG")

    @validator("dateTimeIssued", pre=True, always=True)
    def eta_datetime_format(cls, value):
        seconds = POS_INVOICE_RAW_DATA.get("posting_time").seconds
        return eta_datetime_issued_format(value, seconds)


    # @validator("exchangeRate")
    # def exchange_rate_required(cls, value, values):
    # 	# exchange rate is required if currency is not EGP.
    # 	return POS_INVOICE_RAW_DATA.get("conversion_rate") or value or 1.0

    @validator("orderdeliveryMode")
    def check_orderdelivery_mode(cls, value):
        allowed_values = ["FC", "TO", "TC"]
        # FC, default value
        if not value:
            value = "FC"
        if value not in allowed_values:
            raise ValueError(f"orderdeliveryMode must be one of {allowed_values}")
        return value

# @validator("uuid", pre=True, always=True)
def validate_and_generate_uuid(ereceipt):
    # Serialize and normalize the receipt object
    document_structure = {key: value for key, value in ereceipt.items() if key != "uuid"}
    serialized_text = serialize(document_structure)
    
    # Hash the normalized text using SHA256
    hash_value = hashlib.sha256(serialized_text.encode("utf-8")).digest()
    # Convert the hash value to a hexadecimal string of 64 characters
    uuid = hash_value.hex()

    return uuid

class Receipt(BaseModel):
    header: ReceiptHeader
    documentType: ReceiptDocumentType
    seller: ReceiptSeller
    buyer: ReceiptBuyer
    itemData: List[SingleItemData]
    totalSales: float
    totalCommercialDiscount: float = 0.0
    totalItemsDiscount: float = 0.0
    # extraReceiptDiscountData: List[SingleExtraReceiptDiscountData]
    netAmount: float
    # feesAmount: float
    totalAmount: float
    taxTotals: List[SingleTaxTotal]
    paymentMethod: str
    # adjustment: float
    contractor: Contractor
    beneficiary: Beneficiary


class SingleSignature(BaseModel):
    signatureType: str = Field(default="T", description="Signature Type Codes.")
    value: str = Field(default="ANY", description="Signature value.")


class ReceiptsResponse(BaseModel):
    receipts: List[Receipt]
    # signatures: List[SingleSignature]


class ItemWiseTaxDetails(BaseModel):
    """
    `__root__` is used to indicate that the model's structure is a simple dictionary with no additional named fields.
    """

    data : Dict[str, List[float]]


@frappe.whitelist()
def build_erceipt_json(docname: str, doctype: str):
    """Entry point for creating the POS E-Receipt json."""

    # Set the global raw data: POS_INVOICE_RAW_DATA, COMPANY_DATA
    set_global_raw_data(docname, doctype)

    header: ReceiptHeader = get_pos_ereceipt_header()
    document_type: ReceiptDocumentType = ReceiptDocumentType(
        receiptType="r" if POS_INVOICE_RAW_DATA.get("is_return") else "s"
    )
    seller: ReceiptSeller = get_pos_receipt_seller()
    buyer: ReceiptBuyer = get_pos_receipt_buyer()
    item_data: List[SingleItemData] = get_pos_receipt_item_data()
    total_sales: float = frappe.utils.flt(sum([item.totalSale for item in item_data]), 5)
    net_amount: float = frappe.utils.flt(sum([item.netSale for item in item_data]), 5)
    total_amount: float = sum([item.total for item in item_data])
    total_items_discount = frappe.utils.flt(
        sum(sum(discount.amount for discount in item.itemDiscountData or []) for item in item_data),
        5,
    )
    total_commercial_discount = frappe.utils.flt(
        sum(sum(discount.amount for discount in item.commercialDiscountData or []) for item in item_data),
        5,
    )
    if frappe.utils.flt(total_sales - total_items_discount - total_commercial_discount, 2) != frappe.utils.flt(net_amount, 2):
        frappe.throw(
            _("Generated eReceipt discounts do not reconcile with the invoice net amount."),
            title=_("ETA Validation"),
        )
    invoice_total = frappe.utils.flt(POS_INVOICE_RAW_DATA.get("grand_total"), 2)
    if frappe.utils.flt(total_amount, 2) != invoice_total:
        frappe.throw(
            _("Generated eReceipt total {0} does not match invoice total {1}.").format(
                frappe.utils.fmt_money(total_amount, currency=POS_INVOICE_RAW_DATA.get("currency")),
                frappe.utils.fmt_money(invoice_total, currency=POS_INVOICE_RAW_DATA.get("currency")),
            ),
            title=_("ETA Validation"),
        )
    invoice_total = frappe.utils.flt(POS_INVOICE_RAW_DATA.get("grand_total"), 2)
    if frappe.utils.flt(total_amount, 2) != invoice_total:
        frappe.throw(
            _("Generated eReceipt total {0} does not match invoice total {1}.").format(
                frappe.utils.fmt_money(total_amount, currency=POS_INVOICE_RAW_DATA.get("currency")),
                frappe.utils.fmt_money(invoice_total, currency=POS_INVOICE_RAW_DATA.get("currency")),
            ),
            title=_("ETA Validation"),
        )
    # TODO make payment method dynamic
    payment_method: str = "C"
    adjustment: float = 0.0
    contractor: Contractor = Contractor()
    beneficiary: Beneficiary = Beneficiary()

    # total_commercial_discount: float = get_pos_receipt_total_commercial_discount()
    # total_items_discount: float = get_pos_receipt_total_items_discount()
    # extra_receipt_discount_data: List[SingleExtraReceiptDiscountData] = get_extra_receipt_discount_data()
    # fees_amount: float = get_pos_receipt_fees_amount()
    
    # List comprehension to extract taxableItems
    taxable_items_list = [item.taxableItems for item in item_data]
    taxable_items_list = [taxable_item for sublist in taxable_items_list for taxable_item in sublist]
    tax_totals: List[SingleTaxTotal] = get_pos_receipt_tax_totals(taxable_items_list)
    receipts: List[Receipt] = []
    receipt = Receipt(
            header=header,
            documentType=document_type,
            seller=seller,
            buyer=buyer,
            itemData=item_data,
            totalSales=total_sales,
            totalCommercialDiscount=total_commercial_discount,
            totalItemsDiscount=total_items_discount,
            # extraReceiptDiscountData=extra_receipt_discount_data,
            netAmount=net_amount,
            # feesAmount=fees_amount,
            totalAmount=total_amount,
            taxTotals=tax_totals,
            paymentMethod=payment_method,
            adjustment=adjustment,
            contractor=contractor,
            beneficiary=beneficiary,
        )
    
    seconds = POS_INVOICE_RAW_DATA.get("posting_time").seconds
    date_formated = eta_datetime_issued_format(POS_INVOICE_RAW_DATA.get("posting_date"), seconds)
    receipt.header.dateTimeIssued = date_formated
    uuid = validate_and_generate_uuid(receipt.model_dump(exclude_none=True))
    receipt.header.uuid = uuid
    receipts.append(receipt)
    # signatures: List[SingleSignature] = [SingleSignature()]
    receipts_response: ReceiptsResponse = ReceiptsResponse(receipts=receipts)

    receipts_response: str = receipts_response
    
    return receipts_response

@frappe.whitelist()
def download_ereceipt_json(docname, doctype):
    try:
        file_content = build_erceipt_json(docname, doctype)
        ereceipt_as_json = file_content.model_dump_json(exclude_none=True)
        return download_eta_ereceipt_json(docname, ereceipt_as_json)
    except ValueError as e:
        frappe.log_error(title="Download E-Receipt", message=e, reference_doctype="POS Invoice", reference_name=docname)
        frappe.throw(
                _(str(e)),
                title=_("Download e-Receipt Failed"),
            )
    
def download_eta_ereceipt_json(docname, file_content):
    frappe.local.response.filename = f"eReceipt-{docname}.json"
    frappe.local.response.filecontent = file_content
    frappe.local.response.type = "download"


@frappe.whitelist()
def submit_ereceipt(docname, pos_profile, doctype, raise_throw=True) -> dict:
    """Submit the POS E-Receipt to the API."""
    try:
        doc = frappe.get_doc(doctype, docname)
        doc.check_permission("submit")
        if doc.get("is_return"):
            if doctype != "POS Invoice" or doc.docstatus != 1:
                frappe.throw(_("Submit the POS return locally before sending its eReceipt to ETA."))
            if doc.get("pos_profile") != pos_profile:
                frappe.throw(_("Use the return invoice's POS connector."))
            if doc.get("eta_status") != "Invalid" and (
                doc.get("eta_status") or doc.get("eta_uuid") or doc.get("eta_submissionid")
            ):
                frappe.throw(_("This return already has an ETA submission. Fetch its status instead of submitting again."))
        ereceipt = build_erceipt_json(docname, doctype)
        connector = frappe.get_doc("ETA POS Connector", pos_profile)
        if connector:
            eta_submitter = EReceiptSubmitter(connector)
            return eta_submitter.submit_ereceipt(ereceipt.model_dump(exclude_none=True), doctype)
    except Exception as e:
        frappe.log_error(title="Submit E-Receipt", message=frappe.get_traceback(), reference_doctype="POS Invoice", reference_name=docname)
        if raise_throw:
            frappe.throw(
                str(e),
                title=_("Submitting e-Receipt Failed"),
            )
        return {"error": str(e)}
        
@frappe.whitelist()
def fetch_ereceipt_status(docname, raise_throw=True):
    """Pull the latest submission status from ETA and sync it back onto the POS Invoice.

    Delegates to ``ETA Log.get_submission_status``, which is the only code path that
    actually knows how to call ``EReceiptSubmitter`` correctly (connector + submission id)
    and write the result back to both the ETA Log and the originating document.
    """
    try:
        log_name = frappe.db.get_value(
            "ETA Log Documents",
            {"reference_doctype": "POS Invoice", "reference_document": docname},
            "parent",
        )
        if not log_name:
            submission_id = frappe.db.get_value("POS Invoice", docname, "eta_submissionid")
            frappe.throw(
                _("No ETA Log was found for {0} (submission id: {1}). Submit the eReceipt first.").format(
                    docname, submission_id or _("none")
                )
            )
        log = frappe.get_doc("ETA Log", log_name)
        log.get_submission_status()
        result = frappe.db.get_value(
            "POS Invoice", docname, ["eta_status", "eta_error_message"], as_dict=True
        )
        if raise_throw:
            frappe.msgprint(_(str(result)))
        return result
    except Exception as e:
        frappe.log_error(title="Fetch e-Receipt Status", message=frappe.get_traceback(), reference_doctype="POS Invoice", reference_name=docname)
        if raise_throw:
            frappe.throw(
                str(e),
                title=_("Fetch e-Receipt Failed"),
            )
        return {"error": str(e)}


def _pos_total_qty():
    """Add _total_qty to the POS Invoice Item."""
    total = collections.defaultdict(int)
    for item in POS_INVOICE_RAW_DATA.get("items"):
        total[item.get("item_code")] += item.get("qty")
    POS_INVOICE_RAW_DATA["_total_qty"] = total


def set_global_raw_data(docname: str, doctype: str) -> None:
    """Get the raw POS data from the database."""
    global POS_INVOICE_RAW_DATA
    global COMPANY_DATA

    # Set the global POS data
    doc = frappe.get_doc(doctype, docname)
    doc.check_permission("read")
    POS_INVOICE_RAW_DATA = doc.as_dict()
    posting_time = frappe.utils.get_timedelta(POS_INVOICE_RAW_DATA.get("posting_time"))
    if posting_time is None:
        frappe.throw(_("Set a valid posting time before generating an eReceipt."))
    POS_INVOICE_RAW_DATA["posting_time"] = posting_time
    POS_INVOICE_RAW_DATA["posting_date"] = frappe.utils.getdate(doc.posting_date)
    if doc.get("is_return"):
        if doctype != "POS Invoice":
            frappe.throw(_("Return eReceipts are supported only for POS Invoices."))
        original = get_original_receipt(doc)
        POS_INVOICE_RAW_DATA["_original_receipt_uuid"] = original.eta_uuid
        normalize_return_amounts(POS_INVOICE_RAW_DATA)
    _pos_total_qty()

    # Set the global company data
    COMPANY_DATA = frappe.get_doc("Company", POS_INVOICE_RAW_DATA.get("company")).as_dict()


def get_pos_ereceipt_header() -> ReceiptHeader:
    """Get the POS E-Receipt header."""
    header = ReceiptHeader(
        dateTimeIssued=POS_INVOICE_RAW_DATA.get("posting_date"),
        receiptNumber=POS_INVOICE_RAW_DATA.get("name"),
        currency=POS_INVOICE_RAW_DATA.get("currency"),
        uuid="",
        referenceOldUUID=(
            POS_INVOICE_RAW_DATA.get("eta_uuid")
            if POS_INVOICE_RAW_DATA.get("eta_status") == "Invalid"
            else ""
        ),
    )
    if POS_INVOICE_RAW_DATA.get("is_return"):
        header.referenceUUID = POS_INVOICE_RAW_DATA["_original_receipt_uuid"]
    return header


def get_pos_receipt_seller() -> ReceiptSeller:
    """Get the POS E-Receipt Seller."""
    connector = frappe.get_doc("ETA POS Connector", POS_INVOICE_RAW_DATA.get("pos_profile"))
    branch_name = connector.get("eta_branch") or COMPANY_DATA.get("eta_default_branch")
    if not branch_name:
        frappe.throw(_("Set an ETA branch on the POS connector or company before submitting eReceipts."))
    branch = frappe.get_doc("Branch", branch_name).as_dict()
    branch_address = frappe.get_doc("Address", branch.get("eta_branch_address")).as_dict()
    country_code = frappe.db.get_value("Country", branch_address.country, "code")
    device_serial = str(frappe.db.get_value("ETA POS Connector", POS_INVOICE_RAW_DATA.get("pos_profile"), "serial_number"))
    seller = ReceiptSeller(
        rin=COMPANY_DATA.get("eta_tax_id"),
        companyTradeName=COMPANY_DATA.get("eta_issuer_name"),
        branchCode=branch.get("eta_branch_id"),
        deviceSerialNumber=device_serial,
        # syndicateLicenseNumber=company.get("company"),
        activityCode=COMPANY_DATA.get("eta_default_activity_code"),
        branchAddress=BranchAddress(
            country=country_code,
            governate=branch_address.get("state"),
            regionCity=branch_address.get("city"),
            street=branch_address.get("address_line1"),
            buildingNumber=branch_address.get("building_number") or branch_address.get("address_line2"),
            postalCode=branch_address.get("pincode") or "",
            # floor=company.get("company"),
            # room=company.get("company"),
            # landmark=company.get("company"),
            # additionalInformation=company.get("company"),
        ),
    )
    return seller


def get_pos_receipt_buyer() -> ReceiptBuyer:
    """Get the POS E-Receipt Buyer."""
    customer = frappe.get_doc("Customer", POS_INVOICE_RAW_DATA.get("customer")).as_dict()
        
    buyer = ReceiptBuyer(
        type=customer.get("eta_receiver_type"),
        id=customer.get("tax_id"),
        name=customer.get("customer_name") or "Walk-in Customer"
    )
    return buyer


def _calculate_item_total(_item: dict, _net_sale: float, _taxable_items: List[SingleTaxableItems]) -> float:
    """Get the item total."""
    calculation_parts = [_net_sale]
    calculation_parts.extend([tax.amount for tax in _taxable_items])
    _item_total = sum(calculation_parts)
    return _eta_round(_item_total)


def _get_tax_amount(item_tax_detail: float, net_rate: float, qty: float, _exchange_rate: float) -> float:
    return _eta_round(item_tax_detail * net_rate * qty * _exchange_rate)


def _get_taxable_items(_item: dict) -> List[SingleTaxableItems]:
    """Get the item tax data."""
    taxable_items = []
    if POS_INVOICE_RAW_DATA.get("taxes"):
        for tax in POS_INVOICE_RAW_DATA.get("taxes"):
            rate = get_item_tax_rate(
                POS_INVOICE_RAW_DATA, _item.get("name"), tax.get("name"), _item.get("item_code"), tax
            )
            if rate is None:
                continue
            sub_type = tax.get("eta_tax_sub_type") or tax.get("eta_tax_type")
            tax_type = frappe.db.get_value("ETA Tax Type", sub_type, "parent_eta_tax_type")
            if not tax_type:
                tax_type = tax.get("eta_tax_type")
            if tax_type not in {f"T{i}" for i in range(1, 13)} or sub_type not in {
                f"V{i:03}" for i in range(1, 11)
            }:
                frappe.throw(
                    _("ETA tax type and subtype must be configured for tax row {0}.").format(tax.name),
                    title=_("ETA Validation"),
                )
            amount = _get_tax_amount(
                (rate / 100),
                _item.get("net_rate"),
                _item.get("qty"),
                _item.get("_exchange_rate") or 1,
            )
            taxable_items.append(
                SingleTaxableItems(
                    taxType=tax_type,
                    subType=sub_type,
                    amount=amount,
                    rate=rate,
                )
            )

    return taxable_items


def _eta_round(_value: float, decimal: int = 2) -> float:
    """
    Round unit price to the specified number of decimal places, with a maximum of 5 decimal places.
    If the precision is not provided, it is fetched from the precision settings for "Sales Invoice Item net_rate".
    """
    if not decimal:
        decimal = frappe.get_precision("Sales Invoice Item", "net_rate") or 2

    precision = min(decimal, 5)  # Ensure decimal places is not more than 5
    return round(_value, precision)


def _get_item_metrics(_item: dict) -> Dict:
    exchange_rate = _item.get("_exchange_rate") or 1

    net_sale = _item.get("net_amount")
    line_discount = frappe.utils.flt(_item.get("discount_amount")) + frappe.utils.flt(
        _item.get("distributed_discount_amount")
    )
    total_sale = net_sale + line_discount
    quantity = _item.get("qty")
    if not quantity:
        frappe.throw(_("Item {0} must have a non-zero quantity for an eReceipt.").format(_item.get("item_code")))
    unit_price = _eta_round((total_sale / quantity) * exchange_rate)

    taxable_items = _get_taxable_items(_item)
    item_total = _eta_round(net_sale + sum(tax.amount for tax in taxable_items))

    item_unit_type = frappe.get_value("UOM", _item.get("uom"), "eta_uom") or frappe.get_value(
        "ETA Settings", "ETA Settings", "eta_uom"
    )
    item_meta = frappe.get_meta("Item")
    item_code = _item.get("eta_item_code")
    for fieldname in ("eta_item_code", "eta_egs_code"):
        if not item_code and item_meta.has_field(fieldname):
            item_code = frappe.db.get_value("Item", _item.get("item_code"), fieldname)
    item_code = item_code or _item.get("item_code")
    return {
        "unit_price": unit_price,
        "exchange_rate": exchange_rate,
        "net_sale": net_sale,
        "discount_amount": line_discount,
        "taxable_items": taxable_items,
        "item_total": item_total,
        "item_unit_type": item_unit_type,
        "item_code": item_code,
        "total_sale": total_sale,
    }


def get_pos_receipt_item_data() -> List[SingleItemData]:
    """Get the POS E-Receipt ItemData."""
    item_data = []
    for item in POS_INVOICE_RAW_DATA.get("items"):
        item_metrics = _get_item_metrics(item)
        item_data.append(
            SingleItemData(
                internalCode=item.get("item_code"),
                description=item.get("item_name"),
                itemType=(
                    item.get("eta_code_type")
                    or (
                        frappe.db.get_value("Item", item.get("item_code"), "item_type")
                        if frappe.get_meta("Item").has_field("item_type")
                        else None
                    )
                    or "EGS"
                ),
                quantity=item.get("qty"),
                itemCode=item_metrics.get("item_code"),
                unitType=item_metrics.get("item_unit_type"),
                unitPrice=item_metrics.get("unit_price"),
                # TODO: Semi-validated, Hardcoded
                netSale=item_metrics.get("net_sale"),
                totalSale=item_metrics.get("total_sale"),
                taxableItems=item_metrics.get("taxable_items"),
                total=item_metrics.get("item_total"),
                commercialDiscountData=(
                    [SingleCommercialDiscountData(amount=item_metrics.get("discount_amount"), description="Commercial discount")]
                    if item_metrics.get("discount_amount")
                    else []
                ),
                itemDiscountData=[],
            )
        )
    return item_data

def get_pos_receipt_tax_totals(taxableItems):
    total = collections.defaultdict(int)
    for item in taxableItems:
        total[item.taxType] += frappe.utils.flt(item.amount, 5)

    tax_totals = []
    for tax_type, amount in total.items():
        tax_totals.append(SingleTaxTotal(
            taxType=tax_type,
            amount=amount
        )) 
    return tax_totals