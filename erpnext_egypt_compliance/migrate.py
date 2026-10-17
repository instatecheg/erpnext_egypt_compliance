import frappe
from erpnext_egypt_compliance.hooks import fixtures, app_title


def after_migrate():
	"""Set module for custom fields in existing installations"""
	from erpnext_egypt_compliance.legacy_migration import reconcile_retired_fields

	reconcile_retired_fields()
	# Extract custom field names from fixtures
	custom_field_names = next(
		(f[2] for fixture in fixtures if fixture.get("dt") == "Custom Field"
		 for f in fixture.get("filters", []) if f[0] == "name" and f[1] == "in"),
		[]
	)

	if not custom_field_names:
		return

	# Update only fields that exist but don't have module set
	fields_to_update = frappe.get_all(
		"Custom Field",
		filters={"name": ["in", custom_field_names], "module": ["in", [None, ""]]},
		pluck="name"
	)

	for field_name in fields_to_update:
		frappe.db.set_value("Custom Field", field_name, "module", app_title, update_modified=False)

	if fields_to_update:
		# Migration hook — explicit commit so subsequent migrations see the backfilled module field.
		frappe.db.commit()  # nosemgrep: frappe-manual-commit
		print(f"Set module for {len(fields_to_update)} custom fields")
