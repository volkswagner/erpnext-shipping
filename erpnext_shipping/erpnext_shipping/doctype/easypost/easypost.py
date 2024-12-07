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

from erpnext_shipping.erpnext_shipping.utils import show_error_alert

EASYPOST_PROVIDER = "EasyPost"

class EasyPost(Document):
	pass


class EasyPostUtils():
	def __init__(self):
		settings = frappe.get_single("EasyPost")
		self.api_key = settings.test_key if settings.use_test_environment else settings.production_key # toggle between the test and production keys
		self.enabled = settings.enabled

		if not self.enabled: # show dialog to prompt user to enable the service provicer
			link = get_link_to_form("EasyPost", "EasyPost", _("EasyPost Settings"))
			frappe.throw(_("Please enable EasyPost Integration in {0}").format(link))

	def get_available_services(self, delivery_address, delivery_contact, shipment_parcel, pickup_address, pickup_contact):
		# Retrieve rates at EasyPost from specification stated.
		if not self.enabled or not self.api_key:
			return []
		
		# create a shipment object
		shipment = {
			'to_address': {
				'name': "{} {}".format(delivery_contact.first_name, delivery_contact.last_name),
				'street1': delivery_address.address_line1,
				'street2': delivery_address.address_line2,
				'city': delivery_address.city,
				'state': delivery_address.state,
				'zip': delivery_address.pincode,
				'country': 'US',
				'phone': delivery_contact.phone
			},
			'from_address': {
				'name': "{} {}".format(pickup_contact.first_name, pickup_contact.last_name),
				'street1': pickup_address.address_line1,
				'street2': pickup_address.address_line2,
				'city': pickup_address.city,
				'state': pickup_address.state,
				'zip': pickup_address.pincode,
				'country': 'US',
				'phone': pickup_contact.phone
			},
			'parcel': {
				'length': shipment_parcel[0]['length'],
				'width': shipment_parcel[0]['width'],
				'height': shipment_parcel[0]['height'],
				'weight': shipment_parcel[0]['weight']
			},
		}

		if delivery_contact.email_id is not None:
   			shipment['to_address']['email'] = delivery_contact.email_id

		if pickup_contact.email_id is not None:
   			shipment['to_address']['email'] = pickup_contact.email_id

		try:
			response = requests.post(
				"https://api.easypost.com/v2/shipments",
				json={
					"shipment": shipment,
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
				available_service = self.get_service_dict(service) # standardize the format of the rate details
				available_services.append(available_service)

			return available_services
		except Exception:
			show_error_alert("fetching EasyPost prices")

	def create_shipment(self, shipment):
		# Create a transaction at EasyPost
		if not self.enabled or not self.api_key:
			return []
		try:
			response = requests.post('https://api.easypost.com/v2/shipments/{id}/buy'.format(id=shipment), auth=(self.api_key, ""))

			response_data = response.json()
			if 'failed_parcels' in response_data:
				error = response_data['failed_parcels'][0]['errors']
				frappe.msgprint(_('Error occurred while creating Shipment: {0}').format(error),
					indicator='orange', alert=True)
			else:
				return {
					'service_provider': 'EasyPost',
					'shipment_id': response_data['id'],
					'carrier': self.get_carrier(service_info['carrier'], post_or_get="post"),
					'carrier_service': service_info['service_name'],
					'shipment_amount': service_info['total_price'],
					'awb_number': response_data['tracking_code']
				}
		except Exception:
			show_error_alert("creating EasyPost Shipment")

	def get_label(self, shipment_id):
		# Retrieve shipment label from EasyPost
		label_url = ""

		try:
			shipment_label_response = requests.get('https://api.easypost.com/v2/shipments/{id}'.format(id=shipment_id), auth=(self.api_key, ""))
			shipment_label = json.loads(shipment_label_response.text)
			label_url = shipment_label['postage_label']['label_url']

			if label_url:
				return label_url
			else:
				message = _("Please make sure Shipment (ID: {0}), exists and is a complete Shipment on SendCloud.").format(shipment_id)
				frappe.msgprint(msg=_(message), title=_("Label Not Found"))
		except Exception:
			show_error_alert("printing SendCloud Label")

	def get_tracking_data(self, shipment_id):
		# return EasyPost tracking data
		try:
			tracking_data_response = requests.get('https://api.easypost.com/v2/shipments/{id}'.format(id=shipment_id),auth=(self.api_key, ""))
			tracking_data = json.loads(tracking_data_response.text)
			tracking_data_parcel = tracking_data['tracker']

			return {
				'awb_number': tracking_data_parcel['code'],
				'tracking_status': tracking_data_parcel['status'],
				'tracking_status_info': tracking_data_parcel['status_detail'],
				'tracking_url': tracking_data_parcel['public_url']
			}
		except Exception:
			show_error_alert("updating EasyPost Shipment")

	def get_service_dict(self, service):
		"""Returns a dictionary with service info."""
		available_service = frappe._dict()
		available_service.service_provider = 'EasyPost'
		available_service.carrier = self.get_carrier(service['carrier'], post_or_get="get")
		available_service.service_name = service['service']
		available_service.total_price = service['rate']
		available_service.service_id = service['id']
		return available_service

	def get_carrier(self, carrier_name, post_or_get=None):
		# make 'easypost' => 'EasyPost' while displaying rates
		# reverse the same while creating shipment
		if carrier_name in ("easypost", "EasyPost"):
			return "EasyPost" if post_or_get=="get" else "easypost"
		else:
			return carrier_name.upper() if post_or_get=="get" else carrier_name.lower()
	
	def verify_address(self, address, contact):
		# get the address values
		address= {
			'street1': address.address_line1,
			'street2': address.address_line2,
			'city': address.city,
			'state': address.state,
			'zip': address.pincode,
			'country': 'US',
			'phone': contact.phone,
			'email': ontact.email
		}

		# create an address object and then verify
		try:
			response = requests.post(
				"https://api.easypost.com/v2/addresses/create_and_verify",
				json={
				"address": address,
				},
				auth=(self.api_key, "")
			)
			response_dict = response.json()

			if "error" in response_dict:
				error_message = response_dict["error"]["message"]
				frappe.throw(error_message, title=_("EasyPost"))
			else:
				return True
		except Exception:
			show_error_alert("verifying EasyPost Address")

	def convert_label(self, shipment_id, file_format):
		# convert label
		label_url = ""

		try:
			shipment_label_response = requests.get('https://api.easypost.com/v2/shipments/{id}/label?{format}'.format(id=shipment_id, format=file_format), auth=(self.api_key, ""))
			shipment_label = json.loads(shipment_label_response.text)
			label_url = shipment_label['postage_label']['label_url']

			if label_url:
				return label_url
			else:
				message = _("Please make sure Shipment (ID: {0}), exists and is a complete Shipment on SendCloud.").format(shipment_id)
				frappe.msgprint(msg=_(message), title=_("Label Not Found"))
		except Exception:
			show_error_alert("printing SendCloud Label")
