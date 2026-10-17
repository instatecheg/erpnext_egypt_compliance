frappe.ui.form.on('POS Invoice', {
  refresh(frm) {
		frm.trigger("eta_add_download_ereceipt_button");
		frm.trigger("eta_submit_ereceipt")
		frm.trigger("add_fetch_status_button")
    if (frm.doc.docstatus === 1 && !frm.doc.is_return && frm.doc.eta_status === "Valid") {
      frm.add_custom_button(__("Create Return eReceipt"), () => {
        frappe.confirm(
          __("ETA eReceipts are reversed with a return receipt, not cancelled. Create a return draft for review? Nothing will be submitted to ETA."),
          () => frappe.model.open_mapped_doc({
            method: "erpnext_egypt_compliance.erpnext_eta.ereceipt_returns.make_return_ereceipt",
            frm: frm,
          })
        );
      }, __("eReceipt"));
    }
    if (frm.doc.is_return) {
      frm.set_intro(__("Review the return quantities, refund and original invoice. Download eReceipt Json to check the ETA payload. Submit eReceipt is a separate action after local submission."));
    }
  },
  eta_add_download_ereceipt_button(frm) {
	  frm.add_custom_button('Download eReceipt Json', () => {
		var url = frappe.urllib.get_base_url() + '/api/method/erpnext_egypt_compliance.erpnext_eta.ereceipt_schema.download_ereceipt_json?docname=' + encodeURIComponent(frm.doc.name) + "&doctype=" +  encodeURIComponent(frm.doc.doctype)
      
			$.ajax({
				url: url,
				type: 'GET',
				success: function (result) {
					if (jQuery.isEmptyObject(result)) {
						frappe.msgprint('Failed to load e-Receipt format.');
					}
					else {
						window.location = url;
					}
					if (result.exc){
						console.log(exc)
					}
				},
				error:(r) => {
					if (r.responseJSON.exc_type) {
						console.log(r)
						var server_massages =  jQuery.parseJSON(r.responseJSON._server_messages)
						var object = JSON.parse(server_massages)
						frappe.throw({message: object.message , title: object.title})

					}
				}
			})
		}, "eReceipt")
	},
	eta_submit_ereceipt(frm) {
		frm.add_custom_button("Submit eReceipt", () => {
			const submit = () => {
				frappe.call({
					method: "erpnext_egypt_compliance.erpnext_eta.ereceipt_schema.submit_ereceipt",
					args: {
						docname: frm.doc.name,
						pos_profile: frm.doc.pos_profile,
						doctype: frm.doc.doctype,
					},
					freeze: true,
					freeze_message: __("Submitting ..."),
					callback: function (r) {
						if (!r.exc) {
							frappe.msgprint({
								title: __("ETA eReceipt Submission"),
								message: `<pre>${frappe.utils.escape_html(JSON.stringify(r.message, null, 2))}</pre>`,
							});
							frm.reload_doc();
						}
					},
				});
			};
			if (frm.doc.is_return) {
				if (frm.is_dirty() || frm.doc.docstatus !== 1) {
					frappe.throw(__("Save and submit the POS return locally before sending it to ETA."));
				}
				frappe.confirm(
					__("Submit this return eReceipt to ETA using the configured POS environment? This creates a separate tax document; it does not cancel or delete the original receipt."),
					submit
				);
			} else {
				submit();
			}
		}, "eReceipt")
	},
	add_fetch_status_button(frm) {
		frm.add_custom_button("Fetch eReceipt Status", () => {
			frm.trigger("fetch_eta_status")
		}, "eReceipt")
	},
	fetch_eta_status(frm) {
		frappe.call({
			method: "erpnext_egypt_compliance.erpnext_eta.ereceipt_schema.fetch_ereceipt_status",
			args: { docname: frm.doc.name },
			freeze: true,
			freeze_message: __("Fetch Status..."),
			callback: function (r) {
				console.log(r.message)
				if (!r.exc) {
					frappe.show_alert("E-Receipt Status Updated");
					frm.reload_doc();
				}
			}
		})

	},

});
