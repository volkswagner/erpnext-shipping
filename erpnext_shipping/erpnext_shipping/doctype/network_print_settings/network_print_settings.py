# Copyright (c) 2025, Frappe and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class NetworkPrintSettings(Document):
	pass

def check_shipment_information(shipment, method=None):
	if shipment.shipment_id and shipment.tracking_url:
		shipment.auto_netprint_status = "To Print"