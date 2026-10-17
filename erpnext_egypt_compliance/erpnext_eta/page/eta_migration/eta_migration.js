frappe.pages["eta-migration"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper, title: __("ETA Migration"), single_column: true,
	});
	const method = "erpnext_egypt_compliance.etams_cloud_migration.";
	const body = $('<div class="p-4"></div>').appendTo(page.main);
	const escape = frappe.utils.escape_html;
	let timer;

	async function action(name, confirmation) {
		await frappe.call({ method: method + "start_action", args: { action: name, confirmation } });
		frappe.show_alert({ message: __("Migration action queued; wait for the verification result."), indicator: "blue" });
		await refresh();
	}

	function mapping_dialog(rows) {
		const dialog = new frappe.ui.Dialog({
			title: __("Map Legacy ETA Settings"),
			fields: [{
				fieldname: "mappings", fieldtype: "Table", label: __("Mappings"),
				cannot_add_rows: true, cannot_delete_rows: true, data: rows,
				fields: [
					{ fieldname: "legacy_doctype", fieldtype: "Data", label: __("Legacy Type"), read_only: 1, in_list_view: 1 },
					{ fieldname: "legacy_name", fieldtype: "Data", label: __("Legacy Settings"), read_only: 1, in_list_view: 1 },
					{ fieldname: "target_doctype", fieldtype: "Data", hidden: 1, read_only: 1 },
					{ fieldname: "connector", fieldtype: "Dynamic Link", options: "target_doctype", label: __("Compliance Connector"), reqd: 1, in_list_view: 1 },
					{ fieldname: "reviewed", fieldtype: "Check", label: __("Branch / Tax / Signer Reviewed"), in_list_view: 1 },
				],
			}],
			primary_action_label: __("Validate and Save"),
			primary_action: async (values) => {
				await frappe.call({
					method: method + "set_mappings",
					args: { mappings: JSON.stringify(values.mappings) },
				});
				dialog.hide();
				await refresh();
			},
		});
		dialog.show();
	}

	async function refresh() {
		clearTimeout(timer);
		const { message: status } = await frappe.call({ method: method + "get_status" });
		body.empty();
		$("<p>").addClass("text-muted").text(__(
			"Offline checks only: this page never sends documents to ETA. Keep the scheduler paused and stop all invoice writers. Preserve your backup, files and encryption key."
		)).appendTo(body);
		$("<h4>").text(__("Migration phase: {0}", [status.phase])).appendTo(body);
		$("<p>").text(status.scheduler_paused ? __("Scheduler paused") : __("STOP: Scheduler is not paused")).appendTo(body);
		if (["not_bootstrapped", "retired_legacy"].includes(status.phase)) {
			$("<p>").text(status.previous_retirement
				? __("This site used the earlier retirement process. Run Verify to check its archive and invoice snapshots.")
				: __("For a raw ETAMS restore, run the explicit server bootstrap before install/migrate. A fresh compliance site can run standalone verification without bootstrap.")
			).appendTo(body);
		}
		const issues = $("<ul>").appendTo(body);
		status.blockers.forEach((issue) => $("<li>").addClass("text-danger").text(issue).appendTo(issues));
		const table = $('<table class="table table-bordered"><thead><tr></tr></thead><tbody></tbody></table>').appendTo(body);
		[__("Archived Source"), __("Rows"), __("State")].forEach((label) => $("<th>").text(label).appendTo(table.find("thead tr")));
		Object.entries(status.tables).forEach(([name, value]) => {
			const row = $("<tr>").appendTo(table.find("tbody"));
			[name, value.count ?? "-", value.missing_table ? __("Table was already missing") : __("Fingerprint recorded; run Verify")]
				.forEach((text) => $("<td>").text(text).appendTo(row));
		});
		if (status.job) {
			$("<h5>").text(__("Latest action: {0} / {1}", [status.job.action, status.job.status])).appendTo(body);
			if (status.job.result) $("<pre>").text(JSON.stringify(status.job.result, null, 2)).appendTo(body);
			if (status.job.detail) $("<p>").addClass("text-danger").text(status.job.detail).appendTo(body);
			if (status.job.error_log) {
				$("<a>").attr("href", "/app/error-log/" + encodeURIComponent(status.job.error_log))
					.text(__("Open failure details")).appendTo(body);
			}
		}
		$("<p>").text(__("Cleanup removes legacy metadata, scripts and jobs. Historical source tables, invoice columns and encrypted legacy credentials are deliberately retained; physical database compaction is not part of this wizard.")).appendTo(body);
		body.append(`<a href="/app/eta-legacy-record">${escape(__("Browse read-only legacy archive"))}</a>`);
		page.clear_inner_toolbar();
		page.add_inner_button(__("Refresh"), refresh);
		if (status.mappings.length) page.add_inner_button(__("Map Connectors"), () => mapping_dialog(status.mappings));
		page.set_primary_action(__("Verify"), () => action("verify"));
		if (status.phase === "detached") {
			page.add_inner_button(__("Clean Up ETAMS"), () => {
				frappe.prompt({
					fieldname: "confirmation", fieldtype: "Data", label: __("Type CLEAN UP ETAMS"),
					description: __("Confirm that your backup is preserved, all writers are stopped and connector mappings are verified."),
					reqd: 1,
				}, (values) => action("cleanup", values.confirmation), __("Confirm Legacy Cleanup"), __("Queue Cleanup"));
			});
		}
		if (["queued", "running"].includes(status.job?.status)) {
			timer = setTimeout(() => {
				if (frappe.get_route()[0] === "eta-migration") refresh();
			}, 5000);
		}
	}

	frappe.pages["eta-migration"].on_page_show = refresh;
};
