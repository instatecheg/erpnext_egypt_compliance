# ERPNext Egypt Compliance (ETA Integration)

## Seafood v16 migration candidate

Branch `version-16-seafood` preserves the upstream v16 tax-table adaptations
and includes the locally tested v15 sale/return and ETAMS retirement workflows.
It is a v16 validation candidate, not a certified production upgrade.
Use Frappe/ERPNext v16 and the Python version required by the selected Frappe
release (the current official v16 branch requires Python 3.14).

Install from `https://github.com/instatecheg/erpnext_egypt_compliance.git`,
branch `version-16-seafood`. Do not mix this branch with a v15 runtime.
QR printing and official PDF retrieval remain stage 2.

### Migration gates

1. On an isolated v15 restoration of the current Cloud backup, install compliance,
   pause schedulers and drain ETA workers. Do not send tax documents from the copy.
2. Deploy the ETAMS guard from
   [`deployment/etams-uninstall-guard.patch`](deployment/etams-uninstall-guard.patch)
   on a temporary v15 retirement branch. Run
   `erpnext_egypt_compliance.legacy_migration.prepare`, uninstall ETAMS and migrate.
   Run `erpnext_egypt_compliance.legacy_migration.verify_retirement`.
   Preparation blocks nonempty legacy POS settings; migrate those connectors first.
   Preserve database, files and site encryption key for the real Cloud migration.
3. Only after the v15 retirement check passes, upgrade that copy to the official
   v16 app branches and the custom v16 candidate branches.
4. Migrate twice, run the retirement/standalone checks, preview sale/return payloads
   offline, and test accounting, stock, payroll, lending and ecommerce workflows.
   Keep ETA submission disabled until go-live is explicitly approved.
5. Repeat with the final backup during a maintenance window, with a rollback
   bench and backup retained. Resume integrations only after reconciliation.

Legacy history is browsable as **ETA Legacy Record**, read-only to System Managers.
The archive stores raw legacy records, not submission-ready compliance logs.
It does not replace a database-and-files backup. Existing invoice ETA values are
checked by checksums before and after retirement.

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

- Frappe and ERPNext v16 for this branch
- ETA Portal registration
- Valid Egyptian Tax Authority credentials
- Python matching the selected Frappe v16 release
- Git (if installing from source)
- To Download the Latest Compatible E-Invoice Signer Use This link: https://github.com/Axentorllc/erpnext_egypt_compliance/releases/download/v1.5/ERPNext.ETA.Signer.-.By.Axentor.-.v1.5.zip
### Installation

```bash
# Navigate to your Frappe bench directory
cd /path/to/your/frappe-bench

# Install the app using bench
bench get-app --branch version-16-seafood erpnext_egypt_compliance https://github.com/instatecheg/erpnext_egypt_compliance.git

# Install the app on your site
bench --site your-site.com install-app erpnext_egypt_compliance

# Build assets
bench build

# Restart the bench
bench restart
```

### Post-Installation Setup

After installation, you need to configure the integration:

1. **Register ERPNext on ETA Portal**
2. **Configure the ETA Connector in ERPNext**
3. **Company Setup**
4. **Connect Customers, Items, and UOM to ETA Portal**
5. **Sales Taxes and Charges Template Setup**
6. **Create User in ERPNext**
7. **Install ETA Application Windows**

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
