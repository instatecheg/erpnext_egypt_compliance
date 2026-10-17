import json
from pathlib import Path

from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    """Create receipt fields on upgrades from the same definitions used on fresh installs."""
    fixture_path = Path(__file__).parents[2] / "fixtures" / "custom_field.json"
    custom_fields = {}
    for field in json.loads(fixture_path.read_text()):
        if field["dt"] == "POS Invoice" or field["name"] in {
            "Item-eta_egs_code", "Item-eta_gs1_code"
        }:
            definition = {
                key: value for key, value in field.items()
                if key not in {"creation", "modified", "owner", "modified_by", "docstatus", "idx"}
            }
            custom_fields.setdefault(field["dt"], []).append(definition)
    create_custom_fields(custom_fields, update=True)
