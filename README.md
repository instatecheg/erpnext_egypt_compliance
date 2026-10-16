# ERPNext Egypt Compliance (ETA Integration)

[![License: GPLv3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)

A comprehensive integration module for ERPNext that streamlines and automates Egyptian tax compliance processes by connecting directly with the Egyptian Tax Authority's (ETA) systems.

## 🧩 About the Integration

The **EG Compliance** integration with **ERPNext** ensures businesses in Egypt can meet tax obligations accurately and on time—minimizing errors, reducing preparation time, and automating the submission of key documents like VAT returns and electronic invoices.

This integration equips ERPNext users with the necessary tools to comply with Egyptian tax laws efficiently by embedding real-time compliance within ERPNext, simplifying the tax process from start to finish.

## 🔑 Key Features

- **VAT Calculation and Returns** - Automated VAT calculation and return filing
- **Electronic Invoicing** - Generate and submit e-invoices to ETA
- **Tax Return Filing** - Streamlined tax return submission process
- **Tax Document Management** - Centralized management of all tax-related documents
- **Real-time ETA Integration** - Direct connection with Egyptian Tax Authority systems
- **Dashboard & Reporting** - Comprehensive status reports and monitoring
- **Invoice Signing** - Digital signature integration for compliance
- **E-Invoice Download** - Easy access to official e-invoice documents

## 🚀 Installation

### Prerequisites

- Frappe and ERPNext version 15
- ETA Portal registration
- Valid Egyptian Tax Authority credentials
- Python 3.10+ and Pydantic 2.x (installed with the app)
- Git (if installing from source)
- To Download the Latest Compatible E-Invoice Signer Use This link: https://github.com/Axentorllc/erpnext_egypt_compliance/releases/download/v1.5/ERPNext.ETA.Signer.-.By.Axentor.-.v1.5.zip
### Installation

```bash
# Navigate to your Frappe bench directory
cd /path/to/your/frappe-bench

# Install the app using bench
bench get-app --branch main erpnext_egypt_compliance https://github.com/instatecheg/erpnext_egypt_compliance.git

# Install the app on your site
bench --site your-site.com install-app erpnext_egypt_compliance

# Build assets
bench build

# Restart the bench
bench restart
```

### Frappe Cloud / standalone installation

This fork keeps the app name `erpnext_egypt_compliance` and preserves upstream
history and attribution. Add `https://github.com/instatecheg/erpnext_egypt_compliance`
to your custom bench's apps, select branch `main`, and deploy it alongside Frappe
and ERPNext v15. Install `erpnext_egypt_compliance` on the site. Do not install
ETAMS or E-Invoice-Egypt on a fresh site; neither is required.

Fresh installs import the app's Custom Field fixtures automatically, including
the 21 POS Invoice fields and two Item code compatibility fields. Upgrade patches
are not the fresh-install mechanism: Frappe marks existing patches completed when
an app is first installed. The fixtures therefore carry the complete definitions.

On a fresh site, verify the fields and their database columns without contacting
ETA:

```bash
bench --site your-site.com execute erpnext_egypt_compliance.installation.verify_standalone_installation
```

CI runs this check before and after migration on a new site without legacy ETA
apps. Unit tests alone do not prove clean installation or ETA production validity.
Configure company, branch, tax codes and connector credentials separately on each
site; no production credentials or business records are included in fixtures.

**Existing ETAMS sites:** do not uninstall ETAMS based only on these fixtures.
Its uninstall hook deletes shared Custom Field records without checking their
module. A backed-up, separately tested migration must preserve historical
UUIDs/statuses and restore shared metadata, retire legacy scripts/jobs and resolve
overlapping DocTypes before removal. This fresh-install change does not perform
that migration or disable a legacy app.

### Stage 2 (not included)

- Generate and attach receipt QR codes and add them to receipt printing.
- Retrieve official ETA eInvoice PDFs through the supported document API.

Reserved QR fields are schema only; their presence does not mean QR generation
or PDF retrieval has been implemented.

### Post-Installation Setup

After installation, you need to configure the integration:

1. **Register ERPNext on ETA Portal**
2. **Configure the ETA Connector in ERPNext**
3. **Company Setup**
4. **Connect Customers, Items, and UOM to ETA Portal**
5. **Sales Taxes and Charges Template Setup**
6. **Create User in ERPNext**
7. **Install ETA Application Windows**

For a pilot on a site that also has another ETA app installed, pause the scheduler
and disable that app's automatic submission first. Leave the ETA Connector and
ETA POS Connector unconfigured until preproduction credentials are available.
Sales Invoices without a default ETA Connector can still be submitted locally;
adding a connector enables ETA validation for that company's invoices.

### Verification

To verify the installation:

1. Check if the app appears in your ERPNext Apps list
2. Verify that ETA-related menus and options are available
3. Test the connection to ETA portal
4. Create a test invoice to ensure functionality

## ▶️ Usage

### Creating and Submitting E-Invoices

1. **Create a Sales Invoice in ERPNext**
2. **Sign the Sales Invoice Using the ETA Windows App**
3. **Submit the Invoice to ETA**

### Managing E-Invoices

- **Download E-Invoice** - Retrieve official e-invoice documents
- **Cancel E-Invoice** - Cancel submitted invoices when necessary

### Reversing a POS eReceipt

ETA's published eReceipt API does not provide the eInvoice cancellation operation.
Use a return receipt instead; cancelling an ERPNext document alone does not reverse
its ETA receipt.

1. Open the submitted POS Invoice with ETA status **Valid**.
2. Select **eReceipt > Create Return eReceipt**. This opens an unsaved ERPNext
   return for review, without submitting or cancelling anything.
3. Review the original invoice, returned quantities, discounts and refund. Save
   the return and use **Download eReceipt Json** to review the payload.
4. Submit the return locally when approved. Only then use **Submit eReceipt**
   and confirm the separate ETA submission. This uses the POS connector's configured
   environment, including Production if configured.
5. Fetch the return's ETA status until final validation completes. HTTP 202 is
   acceptance for processing, not final validity. The original receipt remains
   Valid; its reversal is a separate return receipt.

The payload uses receipt type `r`, the original sale's UUID in `referenceUUID`,
and positive ETA quantities and amounts for ERPNext's negative return values.
The return must use the original company, customer, currency and POS profile,
reference a Valid sales receipt, and be issued within 540 days of the sale.
ERPNext's normal return checks still apply. An Invalid return can be corrected
and resubmitted using `referenceOldUUID`; do not submit an already-processing or
Valid return again.

If a legacy ETA app is installed alongside compliance, disable its automatic
receipt submission for this POS before submitting the return locally. Otherwise
that app's document hooks can enqueue a separate submission.

References: [ETA receipt submission API](https://sdk.invoicing.eta.gov.eg/ereceiptapi/02-submit-receipt/)
and [return receipt v1.2](https://sdk.invoicing.eta.gov.eg/documents/return-receipt-v1-2/).

### Monitoring and Reporting

- **ETA Sales Invoices Status Report** - Track invoice submission status
- **Dashboard ETA** - Real-time monitoring of compliance activities

## 🌟 Benefits

- **Automated Compliance** - Reduces manual errors and ensures timely submissions
- **Time Savings** - Streamlines tax processes and reduces preparation time
- **Real-time Integration** - Direct connection with ETA systems for immediate feedback
- **Comprehensive Reporting** - Detailed dashboards and status tracking
- **Regulatory Compliance** - Ensures adherence to Egyptian tax laws and regulations

## 📚 Documentation

For detailed documentation, visit: [EG Compliance Documentation](https://docs.axentor.co/EG%20Compliance/Introduction)

## 📹 Videos

- [ERPNext Egypt Compliance App by Axentor](https://docs.axentor.co/EG%20Compliance/erpnext-egypt-compliance-app-by-axentor)

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## 📄 License

This project is licensed under the **GPLv3** License - see the [LICENSE](LICENSE) file for details.

## 📞 Support

For support and questions, please refer to the official documentation or contact the development team.

---

**Note**: This integration is specifically designed for businesses operating in Egypt and requires proper ETA registration and credentials.
