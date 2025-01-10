# Copyright (c) 2025, Frappe and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from erpnext.stock.doctype.delivery_note.delivery_note import make_sales_invoice

class ShippingSettings(Document):
	pass


@frappe.whitelist()
def check_settings_if_complete():
	shipping_settings = frappe.get_doc("Shipping Settings")
	shipment_cost_target = shipping_settings.shipment_cost_target
	form_link = frappe.utils.get_url_to_form("Shipping Settings", "")

	if shipment_cost_target:
		if shipment_cost_target == "Items List":
			if not shipping_settings.item_code:
				frappe.throw('The item code for Shipping and Handling has not been set. Click <a href="{form_link}">here</a> to add the item code.'.format(form_link=form_link))
			else:
				return "Complete"
		if shipment_cost_target == "Taxes and Charges List":
			if not (shipping_settings.shipping_description and shipping_settings.shipping_account):
				frappe.throw('The account head and/or description for the Shipping Charges has not been set. Click <a href="{form_link}">here</a> to add them.'.format(form_link=form_link))
			else:
				return "Complete"
	else:
		frappe.throw('The location for Sales Invoice Shipping Cost has not been set. Click <a href="{form_link}">here</a> to change the location.'.format(form_link=form_link))

@frappe.whitelist() 
def make_sales_invoice_from_shipment(shipment):
	delivery_note = frappe.flags.args.delivery_note
	shipping_total = frappe.flags.args.shipping_total

	shipping_settings = frappe.get_doc("Shipping Settings")
	shipment_cost_target = shipping_settings.shipment_cost_target

	si_doc = make_sales_invoice(delivery_note)
	form_link = frappe.utils.get_url_to_form("Shipping Settings", "")

	if shipment_cost_target == "Items List":
		item_code = shipping_settings.item_code
		if item_code:
			si_doc.append("items", {
				'item_code': item_code,
				'description': frappe.db.get_value("Item", item_code, "description"),
				'qty': 1,
				'uom': frappe.db.get_value("Item", item_code, "stock_uom"),
				'rate': float(shipping_total),
				'price_list_rate': float(shipping_total)
			})
		else:
			frappe.throw('The item code for Shipping and Handling has not been set. Click <a href="{form_link}">here</a> to add the item code.'.format(form_link=form_link))

	if shipment_cost_target == "Taxes and Charges List":
		si_doc_dict = si_doc.as_dict() # make the si_doc a dict
		si_doc_shipping_entry_index = -1 # set the index of the shipping tax entry to null

		# iterate throught the taxes of the si_doc
		for index, tax_entry in enumerate(si_doc_dict.taxes): 
			if tax_entry['account_head'] == shipping_settings.shipping_account:
				# if a tax matches with the account head set in settings, return the index
				si_doc_shipping_entry_index = index
				break

		# check if settings are set
		if shipping_settings.shipping_description and shipping_settings.shipping_account:
			# check if shipping tax exists in the si doc (value 0 or more)
			if si_doc_shipping_entry_index >= 0:
				#if so modify the tax amount
				si_doc.taxes[si_doc_shipping_entry_index].tax_amount = float(shipping_total)
			# if not, manually add 
			else:
				si_doc.append("taxes", {
					'charge_type': "Actual",
					'description': shipping_settings.shipping_description,
					'account_head': shipping_settings.shipping_account,
					'rate': 0.00,
					'tax_amount': float(shipping_total)
				})
		else:
			frappe.throw('The account head and/or description for the Shipping Charges has not been set. Click <a href="{form_link}">here</a> to add them.'.format(form_link=form_link))

	si_doc.shipment = shipment

	return si_doc
