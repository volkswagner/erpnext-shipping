# Copyright (c) 2024, Frappe and contributors
# For license information, please see license.txt

import json

import frappe
import requests
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt
from frappe.utils.data import get_link_to_form
from requests.exceptions import HTTPError
from urllib.request import urlopen

from erpnext_shipping.erpnext_shipping.utils import show_error_alert

EASYPOST_PROVIDER = "EasyPost"

class EasyPost(Document):
	pass

class EasyPostUtils():
	def __init__(self):
		settings = frappe.get_single("EasyPost")
		# toggle between the test and production keys
		# self.selected_env = settings.test_key if settings.use_test_environment else settings.production_key
		# self.api_key = settings.get_password(self.selected_env)
		self.api_key = settings.get_password("test_key")
		self.enabled = settings.enabled
		self.label_format = settings.label_format
		self.currency = frappe.db.get_single_value("Shipping Settings", "rates_currency")

		if not self.enabled: # show dialog to prompt user to enable the service provicer
			link = get_link_to_form("EasyPost", "EasyPost", _("EasyPost Settings"))
			frappe.throw(_("Please enable EasyPost Integration in {0}").format(link))

	def get_available_services(self,
		shipment_doc,
		delivery_address,
		delivery_contact,
		shipment_parcel,
		pickup_address,
		pickup_contact,
		value_of_goods
	):
		# Retrieve rates at EasyPost from specification stated.
		if not self.enabled or not self.api_key:
			return []
		
		shipment = frappe.get_doc('Shipment', shipment_doc)
		origin_country_name = frappe.db.get_single_value("Shipping Settings", "country_of_origin")
		origin_country_code = frappe.db.get_value("Country", origin_country_name, "code") if origin_country_name else pickup_address.country

		# convert the measurements from metric to English
		parcel = self.convert_parcel_measurements(
			{
				'length': shipment_parcel[0]['length'],
				'width': shipment_parcel[0]['width'],
				'height': shipment_parcel[0]['height'],
				'weight': shipment_parcel[0]['weight']
			}
		)

		# create a shipment object
		ep_shipment = {
			'to_address': {
				'name': "{} {}".format(delivery_contact.first_name, delivery_contact.last_name),
				'street1': delivery_address.address_line1,
				'street2': delivery_address.address_line2,
				'city': delivery_address.city,
				'state': delivery_address.state,
				'zip': delivery_address.pincode,
				'country': delivery_address.country,
				'phone': delivery_contact.phone
			},
			'from_address': {
				'name': "{} {}".format(pickup_contact.first_name, pickup_contact.last_name),
				'street1': pickup_address.address_line1,
				'street2': pickup_address.address_line2,
				'city': pickup_address.city,
				'state': pickup_address.state,
				'zip': pickup_address.pincode,
				'country': pickup_address.country,
				'phone': pickup_contact.phone
			},
			'parcel': parcel,
			'options': {
				'currency': self.currency
			}
		}

		if delivery_contact.email_id is not None:
			ep_shipment['to_address']['email'] = delivery_contact.email_id

		if pickup_contact.email_id is not None:
			ep_shipment['from_address']['email'] = pickup_contact.email_id

		if delivery_address.country_code.lower() != pickup_address.country_code.lower():
			customs_items_list = []
			for item in shipment.customs_items:
				customs_items_list.append({
					'description': item.description,
					'quantity': item.qty,
					'weight': item.weight * 35.27396195,
					'value': item.value,
					'hs_tariff_number': item.hs_tariff_number,
					'origin_country': origin_country_code
				})

			ep_shipment['customs_info'] = {
				'contents_type': shipment.contents_type,
				'restriction_type': shipment.restriction_type,
				'customs_certify': 'true',
				'customs_signer': shipment.customs_signer_name,
				'eel_pfc': shipment.eel_pfc,
				'customs_items': customs_items_list
			}

		try:
			response = requests.post(
				"https://api.easypost.com/v2/shipments",
				json={
					"shipment": ep_shipment,
				},
				auth=(self.api_key, "")
			)
			response_dict = response.json()

			if "error" in response_dict:
				error_message = response_dict["error"]["message"]
				frappe.throw(error_message, title=_("EasyPost"))

			available_services = []
			# store the rates to display to user
			for service in response_dict.get("rates", []):
				available_service = self.get_service_dict(service, flt(shipment_parcel[0]['count']), response_dict.get("id")) # standardize the format of the rate details
				available_services.append(available_service)

			return available_services
		except Exception:
			show_error_alert("fetching EasyPost prices")

	def create_shipment(self, service_info, delivery_address):
		# Create a transaction at EasyPost

		rate = {
			"rate": {
				"id": service_info['service_id']
			}
		}

		if not self.enabled or not self.api_key:
			return []
		try:
			response = requests.post('https://api.easypost.com/v2/shipments/{id}/buy'.format(id=service_info['shipment_id']), auth=(self.api_key, ""), json=rate)

			response_data = response.json()
			if 'failed_parcels' in response_data:
				error = response_data['failed_parcels'][0]['errors']
				frappe.msgprint(_('Error occurred while creating Shipment: {0}').format(error),
					indicator='orange', alert=True)
			else:
				return {
					'service_provider': 'EasyPost',
					'shipment_id': service_info['shipment_id'],
					'carrier': self.get_carrier(service_info['carrier'], post_or_get="post"),
					'carrier_service': service_info['service_name'],
					'shipment_amount': service_info['total_price'],
					'awb_number': response_data['tracker']['tracking_code']
				}

		except Exception:
			show_error_alert("creating EasyPost Shipment")

	def get_label(self, shipment_id):
		# Retrieve shipment label from EasyPost
		label_url = ""
		key_format = "" if self.label_format == "png" else self.label_format + "_" # for the corresponding field key of the label object: empty string for png, append '_' to other formats

		# request for label first
		try:
			shipment_label_response = requests.get('https://api.easypost.com/v2/shipments/{id}/label?file_format={format}'.format(id=shipment_id, format=self.label_format), auth=(self.api_key, ""))
			shipment_label = shipment_label_response.json()
			label_url = shipment_label['postage_label']['label_{format}url'.format(format=key_format)]

			if label_url:
				return label_url

			else:
				message = _("Please make sure Shipment (ID: {0}), exists and is a complete Shipment on EasyPost.").format(shipment_id)
				frappe.msgprint(msg=_(message), title=_("Label Not Found"))
		except Exception:
			show_error_alert("printing EasyPost Label")

	def convert_parcel_measurements(self, parcel):
		parcel_in_english = {}

		parcel_in_english['length'] = parcel['length'] / 2.54
		parcel_in_english['width'] = parcel['width'] / 2.54
		parcel_in_english['height'] = parcel['height'] / 2.54
		parcel_in_english['weight'] = parcel['weight'] * 35.27396195

		return parcel_in_english

	def get_tracking_data(self, shipment_id):
		# return EasyPost tracking data
		try:
			tracking_data_response = requests.get('https://api.easypost.com/v2/shipments/{id}'.format(id=shipment_id),auth=(self.api_key, ""))
			tracking_data = json.loads(tracking_data_response.text)
			tracking_data_parcel = tracking_data['tracker']

			return {
				'awb_number': tracking_data_parcel['tracking_code'],
				'tracking_status': tracking_data_parcel['status'],
				'tracking_status_info': tracking_data_parcel['status_detail'],
				'tracking_url': tracking_data_parcel['public_url']
			}
		except Exception:
			show_error_alert("updating EasyPost Shipment")

	def get_service_dict(self, service, parcel_count, shipment_id):
		"""Returns a dictionary with service info."""
		available_service = frappe._dict()
		available_service.service_provider = 'EasyPost'
		available_service.carrier = self.get_carrier(service['carrier'], post_or_get="get")
		available_service.service_name = service['service']
		#eric changing line below, removing "+ shipment_value" as this shipment value should not be added to the shipping rate
		available_service.total_price = (flt(service['rate']) * parcel_count)
		available_service.service_id = service['id']
		available_service.shipment_id = shipment_id
		return available_service

	def get_carrier(self, carrier_name, post_or_get=None):
		# make 'easypost' => 'EasyPost' while displaying rates
		# reverse the same while creating shipment
		if carrier_name in ("easypost", "EasyPost"):
			return "EasyPost" if post_or_get=="get" else "easypost"
		else:
			return carrier_name.upper() if post_or_get=="get" else carrier_name.lower()
	
	def test(self):
		frappe.msg("gello")