# Copyright (c) 2020, Frappe Technologies and contributors
# For license information, please see license.txt
import json
import os
import requests
import frappe
from erpnext.stock.doctype.shipment.shipment import get_company_contact
from frappe import _

from erpnext_shipping.erpnext_shipping.doctype.letmeship.letmeship import (
	LETMESHIP_PROVIDER,
	get_letmeship_utils,
)
from erpnext_shipping.erpnext_shipping.doctype.sendcloud.sendcloud import SENDCLOUD_PROVIDER, SendCloudUtils
from erpnext_shipping.erpnext_shipping.utils import (
	get_address,
	get_contact,
	match_parcel_service_type_carrier,
)

from erpnext_shipping.erpnext_shipping.doctype.easypost.easypost import EASYPOST_PROVIDER, EasyPostUtils


@frappe.whitelist()
def fetch_shipping_rates(
	shipment_doc,
	pickup_from_type,
	delivery_to_type,
	pickup_address_name,
	delivery_address_name,
	parcels,
	description_of_content,
	pickup_date,
	value_of_goods,
	pickup_contact_name=None,
	delivery_contact_name=None,
):
	# Return Shipping Rates for the various Shipping Providers
	shipment_prices = []
	letmeship_enabled = frappe.db.get_single_value("LetMeShip", "enabled")
	sendcloud_enabled = frappe.db.get_single_value("SendCloud", "enabled")
	easypost_enabled = frappe.db.get_single_value("EasyPost", "enabled")
	pickup_address = get_address(pickup_address_name)
	delivery_address = get_address(delivery_address_name)
	parcels = json.loads(parcels)

	if letmeship_enabled:
		pickup_contact = None
		delivery_contact = None
		if pickup_from_type != "Company":
			pickup_contact = get_contact(pickup_contact_name)
		else:
			pickup_contact = get_company_contact(user=pickup_contact_name)
			pickup_contact.email_id = pickup_contact.pop("email", None)

		delivery_contact = get_contact(delivery_contact_name)

		letmeship = get_letmeship_utils()
		letmeship_prices = (
			letmeship.get_available_services(
				delivery_to_type=delivery_to_type,
				pickup_address=pickup_address,
				delivery_address=delivery_address,
				parcels=parcels,
				description_of_content=description_of_content,
				pickup_date=pickup_date,
				value_of_goods=value_of_goods,
				pickup_contact=pickup_contact,
				delivery_contact=delivery_contact,
			)
			or []
		)
		letmeship_prices = match_parcel_service_type_carrier(letmeship_prices, "carrier", "service_name")
		shipment_prices += letmeship_prices

	if sendcloud_enabled:
		sendcloud = SendCloudUtils()
		sendcloud_prices = (
			sendcloud.get_available_services(
				delivery_address=delivery_address, pickup_address=pickup_address, parcels=parcels
			)
			or []
		)
		sendcloud_prices = match_parcel_service_type_carrier(sendcloud_prices, "carrier", "service_name")
		shipment_prices += sendcloud_prices

	if easypost_enabled and len(parcels) == 1:
		pickup_contact = None
		delivery_contact = None
		if pickup_from_type != "Company":
			pickup_contact = get_contact(pickup_contact_name)
		else:
			pickup_contact = get_company_contact(user=pickup_contact_name)
			pickup_contact.email_id = pickup_contact.pop("email", None)

		# if delivery_to_type != "Company":
		# 	delivery_contact = get_contact(delivery_contact_name)
		# else:
		# 	delivery_contact = get_company_contact(user=pickup_contact_name)
		# 	delivery_contact.email_id = delivery_contact.pop("email", None)

		delivery_contact = get_contact(delivery_contact_name)

		easypost = EasyPostUtils()
		easypost_prices = (
			easypost.get_available_services(
				shipment_doc,
				delivery_address=delivery_address,
				delivery_contact=delivery_contact,
				shipment_parcel=parcels,
				pickup_address=pickup_address,
				pickup_contact=pickup_contact,
				value_of_goods=value_of_goods
			)
			or []
		)
#eric added the following line but does not seem to make a difference
		easypost_prices = match_parcel_service_type_carrier(easypost_prices, "carrier", "service_name")
		shipment_prices += easypost_prices

	shipment_prices = sorted(shipment_prices, key=lambda k: k["total_price"])
	return shipment_prices


@frappe.whitelist()
def create_shipment(
	shipment,
	pickup_from_type,
	delivery_to_type,
	pickup_address_name,
	delivery_address_name,
	shipment_parcel,
	description_of_content,
	pickup_date,
	value_of_goods,
	service_data,
	shipment_notific_email=None,
	tracking_notific_email=None,
	pickup_contact_name=None,
	delivery_contact_name=None,
	delivery_notes=None,
):
	if isinstance(delivery_notes, str):
		delivery_notes = json.loads(delivery_notes)

	if delivery_notes is None:
		delivery_notes = []

	service_info = json.loads(service_data)
	shipment_info, pickup_contact, delivery_contact = None, None, None
	pickup_address = get_address(pickup_address_name)
	delivery_address = get_address(delivery_address_name)
	delivery_company_name = get_delivery_company_name(shipment)

	if pickup_from_type != "Company":
		pickup_contact = get_contact(pickup_contact_name)

	else:
		pickup_contact = get_company_contact(user=pickup_contact_name)
		pickup_contact.email_id = pickup_contact.pop("email", None)

	delivery_contact = get_contact(delivery_contact_name)

	if service_info["service_provider"] == LETMESHIP_PROVIDER:
		letmeship = get_letmeship_utils()
		shipment_info = letmeship.create_shipment(
			pickup_address=pickup_address,
			delivery_company_name=delivery_company_name,
			delivery_address=delivery_address,
			shipment_parcel=shipment_parcel,
			description_of_content=description_of_content,
			pickup_date=pickup_date,
			value_of_goods=value_of_goods,
			pickup_contact=pickup_contact,
			delivery_contact=delivery_contact,
			service_info=service_info,
		)

	if service_info["service_provider"] == SENDCLOUD_PROVIDER:
		sendcloud = SendCloudUtils()
		shipment_info = sendcloud.create_shipment(
			shipment=shipment,
			delivery_address=delivery_address,
			pickup_address=pickup_address,
			pickup_contact=pickup_contact,
			shipment_parcel=shipment_parcel,
			delivery_contact=delivery_contact,
			service_info=service_info,
		)

	if service_info["service_provider"] == EASYPOST_PROVIDER:
		easypost = EasyPostUtils()
		shipment_info = easypost.create_shipment(
			service_info=service_info,
			delivery_address=delivery_address
		)

	if shipment_info:
		shipment = frappe.get_doc("Shipment", shipment)
		shipment.db_set(
			{
				"service_provider": shipment_info.get("service_provider"),
				"carrier": shipment_info.get("carrier"),
				"carrier_service": shipment_info.get("carrier_service"),
				"shipment_id": shipment_info.get("shipment_id"),
				"shipment_amount": shipment_info.get("shipment_amount"),
				"awb_number": shipment_info.get("awb_number"),
				"status": "Booked",
			}
		)

		if delivery_notes:
			update_delivery_note(delivery_notes=delivery_notes, shipment_info=shipment_info)

	return shipment_info


def get_delivery_company_name(shipment: str) -> str | None:
	shipment_doc = frappe.get_doc("Shipment", shipment)
	if shipment_doc.delivery_customer:
		return frappe.db.get_value("Customer", shipment_doc.delivery_customer, "customer_name")
	if shipment_doc.delivery_supplier:
		return frappe.db.get_value("Supplier", shipment_doc.delivery_supplier, "supplier_name")
	if shipment_doc.delivery_company:
		return frappe.db.get_value("Company", shipment_doc.delivery_company, "company_name")

	return None


#standard label printing
@frappe.whitelist()
def print_shipping_label(shipment: str):
	shipment_doc = frappe.get_doc("Shipment", shipment)
	service_provider = shipment_doc.service_provider
	shipment_id = shipment_doc.shipment_id

	if service_provider == LETMESHIP_PROVIDER:
		letmeship = get_letmeship_utils()
		shipping_label = letmeship.get_label(shipment_id)
	elif service_provider == SENDCLOUD_PROVIDER:
		sendcloud = SendCloudUtils()
		shipping_label = []
		_labels = sendcloud.get_label(shipment_id)
		for i, label_url in enumerate(_labels, start=1):
			content = sendcloud.download_label(label_url)
			file_url = save_label_as_attachment(shipment, content, i)
			shipping_label.append(file_url)
	elif service_provider == EASYPOST_PROVIDER:
		easypost = EasyPostUtils()
		shipping_label = easypost.get_label(shipment_id)

	return shipping_label


#send label to network printer
@frappe.whitelist()
def net_print_shipping_label(shipment: str, printer_setting: str):
	shipment_doc = frappe.get_doc("Shipment", shipment)
	service_provider = shipment_doc.service_provider
	shipment_id = shipment_doc.shipment_id
	shipping_label_url = None
	is_byte_data = False # for SendCloud — when calling the print_label_from_url it gives a distinction between labels that are in byte array or URL string

	# Fetch the shipping label URL
	if service_provider == LETMESHIP_PROVIDER:
		letmeship = get_letmeship_utils()
		shipping_label_url = letmeship.get_label(shipment_id)
	elif service_provider == SENDCLOUD_PROVIDER:
		is_byte_data = True # if the provider is SendCloud tell print_label_from_url that the label is already in an array of bytes
		sendcloud = SendCloudUtils()
		_labels = sendcloud.get_label(shipment_id)
		if _labels:
			shipping_label_url = sendcloud.download_label(_labels[0])
	elif service_provider == EASYPOST_PROVIDER:
		easypost = EasyPostUtils()
		shipping_label_url = easypost.get_label(shipment_id)

	if not shipping_label_url:
		frappe.throw(_("No shipping label found for shipment ID: {0}").format(shipment_id))

	# Print the label
	try:
		print_label_from_url(shipping_label_url, printer_setting, is_byte_data, shipment)

	except Exception as e:
		frappe.throw(_("Failed to print the shipping label: {0}").format(str(e)))

	return _("Shipping label sent to printer successfully.")

def print_label_from_url(label: str, printer_setting: str, is_byte_data: bool, shipment: str):
# Downloads a file from a URL and sends it to the configured network printer.
	import cups

	# Fetch EasyPost settings
	easypost_settings = frappe.get_single("Shipping Settings")
	default_printer = easypost_settings.default_network_printer

	# Fetch printer settings
	print_settings = frappe.get_doc("Network Printer Settings", printer_setting)

	# Use the default printer if specified; otherwise, fallback to provided printer_setting
	selected_printer = default_printer if default_printer else printer_setting

	label_content = b''
	label_filename = ""

	if not selected_printer:
		frappe.throw(_("No printer selected or configured."))

	# if the label is in bytes, skip downloading from URL, store the label_content and set the filename that are in the arguments
	if is_byte_data:
		label_content = label
		label_filename = f"label_{shipment}.pdf"

	# otherwise, proceed with downloading first
	else:
		try:
			# Download the label from the URL
			response = requests.get(label)
			response.raise_for_status()

			# store the content and set the filename
			label_content = response.content
			label_filename = label

		except requests.exceptions.RequestException as e:
			frappe.throw(_("Failed to download the label: {0}").format(e))
		except cups.IPPError as e:
			frappe.throw(_("Printing failed due to CUPS error: {0}").format(e))
		except Exception as e:
			frappe.throw(_("An error occurred while printing: {0}").format(e))

	# Save the label locally
	file_name = os.path.basename(label_filename)
	local_path = os.path.join("/tmp", file_name)
	with open(local_path, "wb") as f:
		f.write(label_content)

	# Set up CUPS connection
	cups.setServer(print_settings.server_ip)
	cups.setPort(print_settings.port)
	conn = cups.Connection()

	# Print the file
	conn.printFile(print_settings.printer_name, local_path, "Shipping Label", {})

	# Clean up the temporary file
	os.remove(local_path)

def save_label_as_attachment(shipment: str, content: bytes) -> str:
#Store label as attachment to Shipment and return the URL.
	attachment = frappe.new_doc("File")
	if index is not None:
		attachment.file_name = f"label_{shipment}_{index}.pdf"
	else:
		attachment.file_name = f"label_{shipment}.pdf"
	attachment.content = content
	attachment.folder = "Home/Attachments"
	attachment.attached_to_doctype = "Shipment"
	attachment.attached_to_name = shipment
	attachment.is_private = 1
	attachment.save()
	return attachment.file_url


@frappe.whitelist()
def update_tracking(shipment, service_provider, shipment_id, delivery_notes=None):
	if isinstance(delivery_notes, str):
		delivery_notes = json.loads(delivery_notes)

	if delivery_notes is None:
		delivery_notes = []

	# Update Tracking info in Shipment
	tracking_data = None
	if service_provider == LETMESHIP_PROVIDER:
		letmeship = get_letmeship_utils()
		tracking_data = letmeship.get_tracking_data(shipment_id)
	elif service_provider == SENDCLOUD_PROVIDER:
		sendcloud = SendCloudUtils()
		tracking_data = sendcloud.get_tracking_data(shipment_id)
	elif service_provider == EASYPOST_PROVIDER:
		easypost = EasyPostUtils()
		tracking_data = easypost.get_tracking_data(shipment_id)

	if not tracking_data:
		return

	shipment = frappe.get_doc("Shipment", shipment)
	shipment.db_set(
		{
			"awb_number": tracking_data.get("awb_number"),
			"tracking_status": tracking_data.get("tracking_status"),
			"tracking_status_info": tracking_data.get("tracking_status_info"),
			"tracking_url": tracking_data.get("tracking_url"),
		}
	)

	if delivery_notes:
		update_delivery_note(delivery_notes=delivery_notes, tracking_info=tracking_data)


def update_delivery_note(delivery_notes, shipment_info=None, tracking_info=None):
	# Update Shipment Info in Delivery Note
	# Using db_set since some services might not exist
	delivery_notes = list(set(delivery_notes))

	for delivery_note in delivery_notes:
		dl_doc = frappe.get_doc("Delivery Note", delivery_note)
		if shipment_info:
			dl_doc.db_set("delivery_type", "Parcel Service")
			dl_doc.db_set("parcel_service", shipment_info.get("carrier"))
			dl_doc.db_set("parcel_service_type", shipment_info.get("carrier_service"))
		if tracking_info:
			dl_doc.db_set("tracking_number", tracking_info.get("awb_number"))
			dl_doc.db_set("tracking_url", tracking_info.get("tracking_url"))
			dl_doc.db_set("tracking_status", tracking_info.get("tracking_status"))
			dl_doc.db_set("tracking_status_info", tracking_info.get("tracking_status_info"))
