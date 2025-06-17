# Copyright (c) 2024, Frappe and contributors
# For license information, please see license.txt

import json

import frappe
import requests
import re  # strips non-alphanumerics from account numbers
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt
from frappe.utils.data import get_link_to_form
from requests.exceptions import HTTPError
from urllib.request import urlopen

from erpnext_shipping.erpnext_shipping.utils import show_error_alert
# UPS added imports
from .ups_direct import UPSDirect, UPS_SHIPPER_NUM
from requests.exceptions import HTTPError
import base64, os, uuid
from frappe.utils import get_files_path
import io
from PIL import Image

EASYPOST_PROVIDER = "EasyPost"

class EasyPost(Document):
    pass

class EasyPostUtils():
    def __init__(self):
        settings = frappe.get_single("EasyPost")
        self.api_key = settings.get_password("test_key")
        self.enabled = settings.enabled
        self.label_format = settings.label_format
        self.currency = frappe.db.get_single_value("Shipping Settings", "rates_currency")

        if not self.enabled:
            link = get_link_to_form("EasyPost", "EasyPost", _("EasyPost Settings"))
            frappe.throw(_("Please enable EasyPost Integration in {0}").format(link))

    # ─────────────────────────────────────────────────────────────
    # Human-friendly labels for the popup (does NOT affect API)
    # ─────────────────────────────────────────────────────────────
    _DISPLAY_MAP = {
        # ---- Carrier aliases the rest of the code expects ----
        "FEDEXDEFAULT": "FedEx",
        "UPSDAP":       "UPS",
        "USPS":         "USPS",
        # Service renames (add / edit as you like)
        "FEDEX_2_DAY":          "2-Day",
        "FEDEX_EXPRESS_SAVER":  "Express Saver",
        "FEDEX_GROUND":         "Ground",
        "PRIORITY_OVERNIGHT":   "Priority Overnight",
        "STANDARD_OVERNIGHT":   "Standard Overnight",
        "GroundAdvantage":      "Ground Advantage",
        "3DaySelect":           "3-Day",
        "SMART_POST":           "Smart Post",
        "2ndDayAir":            "2-Day",
        "NextDayAirSaver":      "Next Day Air Saver",
        "NextDayAir":           "Next Day Air",
        "FEDEX_2_DAY_AM":       "2-Day AM",
        "NextDayAirEarlyAM":    "Next Day Air AM",
        "2ndDayAirAM":          "2-Day AM",
    }

    def _pretty(self, raw: str) -> str:
        """Return nicer label if we have one."""
        return self._DISPLAY_MAP.get(raw, raw)

    def get_available_services(
        self,
        delivery_address,
        delivery_contact,
        shipment_parcel,
        pickup_address,
        pickup_contact,
        value_of_goods
    ):
        if not self.enabled or not self.api_key:
            return []

        parcel = {
            "length":  shipment_parcel[0]["length"],
            "width":   shipment_parcel[0]["width"],
            "height":  shipment_parcel[0]["height"],
            "weight":  shipment_parcel[0]["weight"] * 16.0   # lb → oz
        }

        # ------------------------------------------------------------------
        #  DEBUG + third-party extraction
        # ------------------------------------------------------------------
        third_party_block = {}

        ship_doc = frappe.get_doc("Shipment", shipment_parcel[0]["parent"])
        if not ship_doc:
            frappe.throw(_("Could not locate parent Shipment for delivery address"))

        bill_3p  = ship_doc.custom_ship_on_third_party
        acct_3p  = ship_doc.custom_third_party_account
        zip_3p   = ship_doc.custom_third_party_postal

        clean_acct = None
        if bill_3p:
            if not (acct_3p and zip_3p):
                frappe.throw(_("Please fill Customer Account # and Billing ZIP for third-party billing."))

            clean_acct = re.sub(r"[^A-Za-z0-9]", "", acct_3p)
            if len(clean_acct) == 0:
                frappe.throw(_("Account number must contain at least one letter/number."))

            third_party_block = {
                "payment": {
                    "type":        "THIRD_PARTY",
                    "account":     clean_acct,
                    "postal_code": zip_3p.strip(),
                    "country":     "US"
                }
            }

        shipment = {
            'to_address': {
                'name':    f"{delivery_contact.first_name} {delivery_contact.last_name}",
                'street1': delivery_address.address_line1,
                'street2': delivery_address.address_line2,
                'city':    delivery_address.city,
                'state':   delivery_address.state,
                'zip':     delivery_address.pincode,
                'country': delivery_address.country,
                'phone':   delivery_contact.phone
            },
            'from_address': {
                'name':    f"{pickup_contact.first_name} {pickup_contact.last_name}",
                'street1': pickup_address.address_line1,
                'street2': pickup_address.address_line2,
                'city':    pickup_address.city,
                'state':   pickup_address.state,
                'zip':     pickup_address.pincode,
                'country': pickup_address.country,
                'phone':   pickup_contact.phone
            },
            'parcel': parcel,
            'options': {
                'currency': self.currency,
                **third_party_block
            }
        }
        #print(f"Shipment: {shipment}", flush=True)

        if delivery_contact.email_id:
            shipment['to_address']['email'] = delivery_contact.email_id
        if pickup_contact.email_id:
            shipment['from_address']['email'] = pickup_contact.email_id

        try:
            response = requests.post(
                "https://api.easypost.com/v2/shipments",
                json={"shipment": shipment},
                auth=(self.api_key, "")
            )
            response_dict = response.json()

            if "error" in response_dict:
                error_message = response_dict["error"]["message"]
                frappe.throw(error_message, title=_("EasyPost"))

            available_services = []
            for service in response_dict.get("rates", []):
                available_service = self.get_service_dict(
                    service,
                    flt(shipment_parcel[0]['count']),
                    response_dict.get("id")
                )
                available_services.append(available_service)

            # ─────────────────────────────────────────────────────────────
            # UPSDirect integration for third-party UPS billing
            # ─────────────────────────────────────────────────────────────
            if bill_3p:
                ups = UPSDirect()
                try:
                    ups_rates = ups.rate(
                        UPS_SHIPPER_NUM,
                        clean_acct,
                        zip_3p.strip(),
                        shipment['to_address'],
                        shipment['from_address'],
                        parcel
                    )
                    
                    rated_shipments = ups_rates.get('RateResponse', {}).get('RatedShipment', [])
                   
                    if isinstance(rated_shipments, dict):
                        rated_shipments = [rated_shipments]
                        
                    for rated in rated_shipments:                    
                        svc = rated["Service"]
                        code = svc.get("Code") or ""
                        # Prefer UPS-returned Description, but fall back to SERVICE_MAP or the raw code
                        nice_name = svc.get("Description") or UPSDirect.SERVICE_MAP.get(code) or code

                        available_services.append(frappe._dict({
                            "service_provider": "UPS",
                            "carrier":          "UPS",
                            "service_name":      nice_name,  # ← friendly name now set
                            "total_price":       flt(rated["TotalCharges"]["MonetaryValue"]),
                            "delivery_days":     rated.get("GuaranteedDaysToDelivery"),
                            "service_id":        code,
                            "shipment_id":       None,
                            "ups_shipper_number":UPS_SHIPPER_NUM,
                            "ups_account":       clean_acct,
                            "ups_postal_code":   zip_3p.strip(),
                            "to_address":        shipment["to_address"],
                            "from_address":      shipment["from_address"],
                            "parcel":            parcel,
                        }))

                except HTTPError as e:
                    print("UPS HTTPError:", getattr(e.response, "text", str(e)), flush=True)
                    raise      # propagate the HTTP 4xx/5xx back to Frappe


                except Exception as e:
                    frappe.throw(_(f"Unexpected error creating UPS label: {str(e)}"))

                    
            # ─────────────────────────────────────────────────────────────
            # If third-party billing, filter out all but UPS (6-digit acct)
            # or FedEx (9-digit acct).
            # ─────────────────────────────────────────────────────────────
            if bill_3p:
                if len(clean_acct) == 6:
                    # only UPS rows
                    available_services = [
                        s for s in available_services
                        if s.get("service_provider") == "UPS"
                    ]
                elif len(clean_acct) == 9:
                    # only FedEx rows
                    available_services = [
                        s for s in available_services
                        if s.get("carrier", "").lower() == "fedex"
                    ]                    

            return available_services

        except Exception:
            show_error_alert("fetching EasyPost prices")

    def create_shipment(self, service_info, delivery_address):       
        # ─────────────────────────────────────────────────────────────
        # Custom UPS purchase flow
        # ─────────────────────────────────────────────────────────────        
        if isinstance(service_info, dict):
            service_info = frappe._dict(service_info)
         
        if (service_info.get("service_provider") or "").upper() == "UPS":
            ups = UPSDirect()
            try:
                resp = ups.ship(
                    service_info.ups_shipper_number,
                    service_info.ups_account,
                    service_info.ups_postal_code,
                    service_info.to_address,
                    service_info.from_address,
                    service_info.parcel,
                    service_info.service_id
                )

                results = resp["ShipmentResponse"]["ShipmentResults"]

                # ── normalise PackageResults to always be a list ────────────────
                pkg_results = results.get("PackageResults", [])
                if isinstance(pkg_results, dict):
                    pkg_results = [pkg_results]

                # tracking #
                tracking = (
                    results.get("ShipmentIdentificationNumber")
                    or pkg_results[0].get("TrackingNumber")
                )

                # label block (may be missing if "LabelDelivery" = LabelLinkIndicator)
                label_info = {}
                if pkg_results:
                    label_info = (
                        pkg_results[0].get("LabelImage", {})          # newer docs
                        or pkg_results[0].get("ShippingLabel", {})     # older docs
                    )

                graphic = label_info.get("GraphicImage")
                fmt     = label_info.get("ImageFormat", {}).get("Code", "").lower()
                label_url = f"data:image/{fmt};base64,{graphic}" if (graphic and fmt) else None


                # ── save label data-URI → file so ERPNext can stream it
                public_label_url = self._save_base64_png(label_url)
                shipping_label   = public_label_url
                #print(f"SHIPPING LABEL VARIABLE: {shipping_label}, flush=True)

                return {
                    "service_provider": "UPS",
                    "shipment_id":      tracking,
                    "carrier":          "UPS",
                    "carrier_service":  service_info.service_name,
                    "shipment_amount":  service_info.total_price,
                    "awb_number":       tracking,

                    # For ERPNext code paths that still expect this field
                    "shipping_label":   shipping_label,

                    # EasyPost-compatible block
                    "postage_label": {
                        "label_url":     public_label_url,
                        "label_png_url": public_label_url,
                        "label_pdf_url": public_label_url.replace(".png", ".pdf"),
                    }
                }            
            except HTTPError as e:
                print("UPS HTTPError:", getattr(e.response, "text", str(e)), flush=True)
                raise      # propagate the HTTP 4xx/5xx back to Frappe

        # ─────────────────────────────────────────────────────────────
        # EasyPost purchase flow (unchanged)
        # ─────────────────────────────────────────────────────────────
        rate = {"rate": {"id": service_info['service_id']}}

        if not self.enabled or not self.api_key:
            return []

        try:
            response = requests.post(
                f'https://api.easypost.com/v2/shipments/{service_info["shipment_id"]}/buy',
                auth=(self.api_key, ""),
                json=rate
            )

            response_data = response.json()
            #print(response_data, flush=True)

            if "error" in response_data:
                frappe.throw(_("EasyPost Error: {0}").format(
                    response_data["error"].get("message", "Unknown error")
                ))

            if 'failed_parcels' in response_data:
                error = response_data['failed_parcels'][0]['errors']
                frappe.msgprint(
                    _('Error occurred while creating Shipment: {0}').format(error),
                    indicator='orange', alert=True
                )
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
        label_url = ""
        key_format = "" if self.label_format == "png" else self.label_format + "_"

        try:
            shipment_label_response = requests.get(
                f'https://api.easypost.com/v2/shipments/{shipment_id}/label?file_format={self.label_format}',
                auth=(self.api_key, "")
            )
            shipment_label = shipment_label_response.json()
            label_url = shipment_label['postage_label'][f'label_{key_format}url']

            if label_url:
                return label_url
            else:
                message = _(f"Please make sure Shipment (ID: {shipment_id}), exists and is a complete Shipment on EasyPost.")
                frappe.msgprint(msg=_(message), title=_("Label Not Found"))
        except Exception:
            show_error_alert("printing EasyPost Label")

    def get_tracking_data(self, shipment_id):
        try:
            tracking_data_response = requests.get(
                f'https://api.easypost.com/v2/shipments/{shipment_id}',
                auth=(self.api_key, "")
            )
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
        available_service = frappe._dict()
        available_service.service_provider = 'EasyPost'
        raw_carrier = self.get_carrier(service['carrier'], post_or_get="get")
        available_service.carrier = self._pretty(raw_carrier)
        raw_service = service['service']
        available_service.service_name = self._pretty(raw_service)
        available_service.total_price = flt(service['rate']) * parcel_count
        available_service.delivery_days = service.get('delivery_days')
        available_service.service_id = service['id']
        available_service.shipment_id = shipment_id
        return available_service

    def get_carrier(self, carrier_name, post_or_get=None):
        if carrier_name in ("easypost", "EasyPost"):
            return "EasyPost" if post_or_get == "get" else "easypost"
        else:
            return carrier_name.upper() if post_or_get == "get" else carrier_name.lower()
    
    def _save_base64_png(self, data_uri: str) -> str:
        """
        Decode a data-URI, rotate it 90° clockwise, save as a *private* File,
        create the matching File Doc, and return the absolute URL.
        """
        if not data_uri or not data_uri.startswith("data:image"):
            return data_uri                     # already a URL – nothing to do

        header, b64 = data_uri.split(",", 1)
        ext   = header.split("/")[1].split(";")[0]          # "png", "gif", …

        # ── decode base-64 → bytes ───────────────────────────────────────
        raw_bytes = base64.b64decode(b64)

        # ▲ ROTATE the image upright with Pillow
        img = Image.open(io.BytesIO(raw_bytes))
        img = img.rotate(-90, expand=True)                  # -90 = 90° clockwise

        # save the rotated image to disk
        fname = f"{uuid.uuid4()}.{ext}"
        file_path = os.path.join(get_files_path(is_private=True), fname)
        img.save(file_path, format=ext.upper())

        # ── register a File doc so /private/files/* isn’t “Forbidden” ────
        file_doc = frappe.get_doc({
            "doctype":   "File",
            "file_name": fname,
            "file_url":  f"/private/files/{fname}",
            "is_private": 1,
        })
        file_doc.insert(ignore_permissions=True)

        return f"{frappe.utils.get_url()}{file_doc.file_url}"


    def test(self):
        frappe.msg("gello")

