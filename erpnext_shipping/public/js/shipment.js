// Copyright (c) 2020, Frappe and contributors
// For license information, please see license.txt

frappe.ui.form.on("Shipment", {
	refresh: function (frm) {
		if (frm.doc.docstatus === 1 && !frm.doc.shipment_id) {
			frm.add_custom_button(__("Fetch Shipping Rates"), function () {
				return frm.events.fetch_shipping_rates(frm);
			});
		}

		if (frm.doc.shipment_id) {
			frm.add_custom_button(
				__("Print Shipping Label"),
				function () {
					return frm.events.print_shipping_label(frm);
				},
				__("Tools")
			);
		}	
		if (frm.doc.shipment_id) {
			frm.add_custom_button(
				__("Network Print Label"),
				function () {
					// Fetch Shipping settings to check for default_network_printer
					// Mark changed Easypost to Shipping Settings
					frappe.call({
						method: "frappe.client.get",
						args: {
							doctype: "Shipping Settings",
						},
						callback: function (r) {
							if (r.message && r.message.default_network_printer) {
								// Default printer exists, skip the dialog
								frappe.call({
									method: "erpnext_shipping.erpnext_shipping.shipping.net_print_shipping_label",
									args: {
										shipment: frm.doc.name,
										printer_setting: r.message.default_network_printer,
									},
									callback: function (res) {
										if (!res.exc) {
											frappe.msgprint(
												__("Shipping label sent to printer successfully.")
											);
										}
									},
								});
							} else {
								// No default printer, show dialog to select printer
								frappe.prompt(
									[
										{
											label: __("Printer Setting"),
											fieldname: "printer_setting",
											fieldtype: "Link",
											options: "Network Printer Settings",
											reqd: 1,
										},
									],
									function (values) {
										frappe.call({
											method: "erpnext_shipping.erpnext_shipping.shipping.net_print_shipping_label",
											args: {
												shipment: frm.doc.name,
												printer_setting: values.printer_setting,
											},
											callback: function (res) {
												if (!res.exc) {
													frappe.msgprint(
														__("Shipping label sent to printer successfully.")
													);
												}
											},
										});
									},
									__("Select Printer Setting"),
									__("Print")
								);
							}
						},
					});
				},
			
					__("Tools")
			);
			if (frm.doc.tracking_status != "Delivered") {
				frm.add_custom_button(
					__("Update Tracking"),
					function () {
						return frm.events.update_tracking(
							frm,
							frm.doc.service_provider,
							frm.doc.shipment_id
						);
					},
					__("Tools")
				);

				frm.add_custom_button(
					__("Track Status"),
					function () {
						if (frm.doc.tracking_url) {
							const urls = frm.doc.tracking_url.split(", ");
							urls.forEach((url) => window.open(url));
						} else {
							let msg = __(
								"Please complete Shipment (ID: {0}) on {1} and Update Tracking.",
								[frm.doc.shipment_id, frm.doc.service_provider]
							);
							frappe.msgprint({ message: msg, title: __("Incomplete Shipment") });
						}
					},
					__("View")
				);
			}
		}

		if (frm.doc.status === "Booked" || frm.doc.status === "Completed") {
    		frm.add_custom_button(
    		    "Create Sales Invoice", 
    		    function() {
					frappe.call({
						method: "erpnext_shipping.erpnext_shipping.doctype.shipping_settings.shipping_settings.check_settings_if_complete",
						freeze: true,
						freeze_message: "Checking Setttings",
						callback: function(r) {
							if (!r.exc) {
								if (frm.doc.shipment_delivery_note) {
									let dialog = new frappe.ui.Dialog({
										title: __('Add Shipping Cost'),
										fields: [
											{
												label: 'Shipment Cost',
												fieldname: 'shipment_cost',
												fieldtype: 'Currency',
												read_only: 1,
												default: frm.doc.shipment_amount
											},
											{
												label: 'Handling Fee',
												fieldname: 'handling_fee',
												fieldtype: 'Currency',
												default: 2
											}
										],
										primary_action_label: 'Proceed',
										primary_action: function (values) {
											frappe.model.open_mapped_doc({
												method: "erpnext_shipping.erpnext_shipping.doctype.shipping_settings.shipping_settings.make_sales_invoice_from_shipment",
												frm: frm,
												args: {
													delivery_note: frm.doc.shipment_delivery_note[0].delivery_note,
													shipping_total: frm.doc.shipment_amount + (values.handling_fee || 0),
													tracking_url: frm.doc.tracking_url
												},
												freeze: true,
												freeze_message: "Creating New Sales Invoice",
											})
										}
									})
									
									dialog.show()
								}
								else {
									frappe.msgprint({
										title: "Can't Create Sales Invoice",
										indicator: "orange",
										message: "The shipment doesn't have a delivery note associated with it."
									});
								}
							}
						}
					})
    		    }
    		)
	    }
	},

	fetch_shipping_rates: function (frm) {
		if (!frm.doc.shipment_id) {
			frappe.call({
				method: "erpnext_shipping.erpnext_shipping.shipping.fetch_shipping_rates",
				freeze: true,
				freeze_message: __("Fetching Shipping Rates"),
				args: {
					pickup_from_type: frm.doc.pickup_from_type,
					delivery_to_type: frm.doc.delivery_to_type,
					pickup_address_name: frm.doc.pickup_address_name,
					delivery_address_name: frm.doc.delivery_address_name,
					parcels: frm.doc.shipment_parcel,
					description_of_content: frm.doc.description_of_content,
					pickup_date: frm.doc.pickup_date,
					pickup_contact_name:
						frm.doc.pickup_from_type === "Company"
							? frm.doc.pickup_contact_person
							: frm.doc.pickup_contact_name,
					delivery_contact_name: frm.doc.delivery_contact_name,
					value_of_goods: frm.doc.value_of_goods,
				},
				callback: function (r) {
					if (r.message && r.message.length) {
						select_from_available_services(frm, r.message);
					} else {
						frappe.msgprint({
							message: __("No Shipment Services available"),
							title: __("Note"),
						});
					}
				},
			});
		} else {
			frappe.throw(__("Shipment already created"));
		}
	},

	print_shipping_label: function (frm) {
		frappe.call({
			method: "erpnext_shipping.erpnext_shipping.shipping.print_shipping_label",
			freeze: true,
			freeze_message: __("Printing Shipping Label"),
			args: {
				shipment: frm.doc.name,
			},
			callback: function (r) {
				if (r.message) {
					if (frm.doc.service_provider == "LetMeShip") {
						var array = JSON.parse(r.message);
						// Uint8Array for unsigned bytes
						array = new Uint8Array(array);
						const file = new Blob([array], { type: "application/pdf" });
						const file_url = URL.createObjectURL(file);
						window.open(file_url);
					} else {
						if (Array.isArray(r.message)) {
							r.message.forEach((url) => window.open(url));
						} else {
							window.open(r.message);
						}
					}
				}
			},
		});
	},

	update_tracking: function (frm, service_provider, shipment_id) {
		let delivery_notes = [];
		(frm.doc.shipment_delivery_note || []).forEach((d) => {
			delivery_notes.push(d.delivery_note);
		});
		frappe.call({
			method: "erpnext_shipping.erpnext_shipping.shipping.update_tracking",
			freeze: true,
			freeze_message: __("Updating Tracking"),
			args: {
				shipment: frm.doc.name,
				shipment_id: shipment_id,
				service_provider: service_provider,
				delivery_notes: delivery_notes,
			},
			callback: function (r) {
				if (!r.exc) {
					frm.reload_doc();
					$('div[data-fieldname="shipment_information_section"]')[0].scrollIntoView();
				}
			},
		});
	},

	before_submit: async (frm) => {
	    if (frm.doc.shipment_parcel.length > 1) {
            let prompt = new Promise((resolve, reject) => {
                frappe.confirm(
                    `The shipment has <b>${frm.doc.shipment_parcel.length}</b> parcels.\n EasyPost will not appear in the rates table as it does not support multiple parcels. Continue anyways?</b>`,
                    () => resolve(),
                    () => reject()
                );
            });
            await prompt.then(
                () => frappe.show_alert("Shipment successfully submitted.", 5), 
                () => {
                    frappe.validated = false;
                    frappe.show_alert("Shipment submission cancelled.", 5)
                }
            );
	    }
	}
});

function select_from_available_services(frm, available_services) {
	console.log(available_services)
	const arranged_services = available_services.reduce(
		(prev, curr) => {
			if (curr.is_preferred) {
				prev.preferred_services.push(curr);
			} else {
				prev.other_services.push(curr);
			}
			return prev;
		},
		{ preferred_services: [], other_services: [] }
	);

	const dialog = new frappe.ui.Dialog({
		title: __("Select Service to Create Shipment"),
		size: "extra-large",
		fields: [
			{
				fieldtype: "HTML",
				fieldname: "available_services",
				label: __("Available Services"),
			},
		],
	});

	let delivery_notes = [];
	(frm.doc.shipment_delivery_note || []).forEach((d) => {
		delivery_notes.push(d.delivery_note);
	});

	dialog.fields_dict.available_services.$wrapper.html(
		frappe.render_template("shipment_service_selector", {
			header_columns: [__("Platform"), __("Carrier"), __("Parcel Service"), __("Price"), ""],
			data: arranged_services,
		})
	);

	dialog.$body.on("click", ".btn", function () {
		let service_type = $(this).attr("data-type");
		let service_index = cint($(this).attr("id").split("-")[2]);
		let service_data = arranged_services[service_type][service_index];
		frm.select_row(service_data);
	});

	frm.select_row = function (service_data) {
		console.log(service_data)
		frappe.call({
			method: "erpnext_shipping.erpnext_shipping.shipping.create_shipment",
			freeze: true,
			freeze_message: __("Creating Shipment"),
			args: {
				shipment: frm.doc.name,
				pickup_from_type: frm.doc.pickup_from_type,
				delivery_to_type: frm.doc.delivery_to_type,
				pickup_address_name: frm.doc.pickup_address_name,
				delivery_address_name: frm.doc.delivery_address_name,
				shipment_parcel: frm.doc.shipment_parcel,
				description_of_content: frm.doc.description_of_content,
				pickup_date: frm.doc.pickup_date,
				pickup_contact_name:
					frm.doc.pickup_from_type === "Company"
						? frm.doc.pickup_contact_person
						: frm.doc.pickup_contact_name,
				delivery_contact_name: frm.doc.delivery_contact_name,
				value_of_goods: frm.doc.value_of_goods,
				service_data: service_data,
				delivery_notes: delivery_notes,
			},
			callback: function (r) {
				if (!r.exc) {
					frm.reload_doc();
					frappe.msgprint({
						message: __("Shipment {1} has been created with {0}.", [
							r.message.service_provider,
							r.message.shipment_id.bold(),
						]),
						title: __("Shipment Created"),
						indicator: "green",
					});
					frm.events.update_tracking(
						frm,
						r.message.service_provider,
						r.message.shipment_id
					);
				}
			},
		});
		dialog.hide();
	};
	dialog.show();
}
