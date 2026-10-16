from types import SimpleNamespace

from erpnext_egypt_compliance.erpnext_eta import pre_validation


def test_invoice_without_connector_can_be_submitted_locally(monkeypatch):
    doc = SimpleNamespace(name="TEST-INV-001", get=lambda field: None)
    monkeypatch.setattr(
        pre_validation.frappe,
        "get_value",
        lambda *args: "Test Company" if args[0] == "Sales Invoice" else False,
    )

    def get_connector(company, throw_if_no_connector=True):
        assert company == "Test Company"
        assert throw_if_no_connector is False
        return None

    monkeypatch.setattr(pre_validation, "get_company_eta_connector", get_connector)
    def unexpected_invoice_validation(*args, **kwargs):
        raise AssertionError("Should not validate without a connector")

    monkeypatch.setattr(pre_validation, "get_invoice_asjson", unexpected_invoice_validation)

    assert pre_validation.validate_eta_before_submit(doc) is None
