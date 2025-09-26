# Copyright (c) 2024, Frappe and contributors
# For license information, please see license.txt
#frappe.log_error(title="Bill_3p 2", message=f"bill_3p: {bill_3p}, clean_acct: {acct_3p}")
#/apps/erpnext_shipping/erpnext_shipping/erpnext_shipping/doctype/easypost
import json
import base64
import io
import os
import re                # strips non‑alphanumerics from account numbers
import uuid
import time
from typing import List, Dict, Any

import frappe
import requests
from PIL import Image
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, get_link_to_form, get_url, get_files_path
from requests.exceptions import HTTPError

from erpnext_shipping.erpnext_shipping.utils import show_error_alert

# UPS-Direct imports -----------------------------------------------------------
from .ups_direct import UPSDirect

EASYPOST_PROVIDER = "EasyPost"

# ─────────────────────────────────────────────
# Helper: turn each row into N identical parcel dicts
# ─────────────────────────────────────────────
def build_parcel_list(rows):
    """
    Return a flat list with ONE element per physical box.
    Each element is a dict shaped exactly like EasyPost's Parcel.
    """
    parcels = []
    for row in rows:
        qty = int(row.get("count") or 1)
        parcel = {
            "length":  row["length"],
            "width":   row["width"],
            "height":  row["height"],
            "weight":  row["weight"] * 16.0,  # EasyPost wants ounces
        }
        parcels.extend([parcel] * qty)
    return parcels


class EasyPost(Document):
    pass


class EasyPostUtils:
    def __init__(self):
        settings = frappe.get_single("EasyPost")
        self.api_key = settings.get_password("test_key")
        self.enabled = settings.enabled
        self.label_format = settings.label_format
        self.currency = frappe.db.get_single_value("Shipping Settings", "rates_currency")

        if not self.enabled:
            link = get_link_to_form("EasyPost", "EasyPost", _("EasyPost Settings"))
            frappe.throw(_("Please enable EasyPost Integration in {0}").format(link))

    # ──────────────────────────────────────────────────────────────────────
    # Human‑friendly labels for the popup (does NOT affect the API)
    # ──────────────────────────────────────────────────────────────────────
    _DISPLAY_MAP = {
        # ---- Carrier aliases the rest of the code expects ----
        "FEDEXDEFAULT": "FedEx (Easy Post)",
        "FEDEX":        "FedEx (Case Club)",
        "UPSDAP":       "UPS",
        "USPS":         "USPS",
        # ---- Service renames (add / edit as you like) ----
        "FEDEX_2_DAY":          "2‑Day",
        "FEDEX_EXPRESS_SAVER":  "Express Saver",
        "FEDEX_GROUND":         "Ground",
        "PRIORITY_OVERNIGHT":   "Priority Overnight",
        "STANDARD_OVERNIGHT":   "Standard Overnight",
        "GroundAdvantage":      "Ground Advantage",
        "3DaySelect":           "3‑Day",
        "SMART_POST":           "Smart Post",
        "2ndDayAir":            "2‑Day",
        "NextDayAirSaver":      "Next Day Air Saver",
        "NextDayAir":           "Next Day Air",
        "FEDEX_2_DAY_AM":       "2‑Day AM",
        "NextDayAirEarlyAM":    "Next Day Air AM",
        "2ndDayAirAM":          "2‑Day AM",
        "UPSGroundsaverGreaterThan1lb": "Ground Saver",
        "FIRST_OVERNIGHT": "Next Day Air AM",   
    }

    def _pretty(self, raw: str) -> str:
        """Return nicer label if we have one."""
        return self._DISPLAY_MAP.get(raw, raw)

    def _rate_in_all_shipments(self, rate_obj, shipments) -> bool:
        """True iff every shipment has a rate with the same carrier+service."""
        c = rate_obj["carrier"]
        s = rate_obj["service"]
        for sh in shipments:
            if not any(r["carrier"] == c and r["service"] == s for r in sh.get("rates", [])):
                return False
        return True

    # =========================================================================
    # MAIN RATE‑SHOPPING ENDPOINT
    # =========================================================================
    def get_available_services(self, delivery_address, delivery_contact, shipment_parcel: List[Dict[str, Any]], pickup_address, pickup_contact, value_of_goods, ) -> List[Dict[str, Any]]:
        if not self.enabled or not self.api_key:
            return []

        parcels = build_parcel_list(shipment_parcel)
        if not parcels:
            frappe.throw(_("No parcel data supplied to EasyPost."))
        parcel_count = len(parcels)
        first_parcel = parcels[0]
        mps = parcel_count > 1        # multi-piece shipment?

        parcel_block = ({"parcel": first_parcel} # 1 box only
        if parcel_count == 1 else {"parcels": parcels}) # 2+ boxes

        # ------------------------------------------------------------------
        # Third‑party billing extraction for UPS
        # ------------------------------------------------------------------
        third_party_block = {}

        ship_doc = frappe.get_doc("Shipment", shipment_parcel[0]["parent"])
        if not ship_doc:
            frappe.throw(_("Could not locate parent Shipment for delivery address"))

        bill_3p = (
            ship_doc.get("custom_ship_on_third_party") in (1, "1", True, "Yes")
            and bool(ship_doc.get("custom_third_party_account"))
        )
        acct_3p     = ship_doc.custom_third_party_account          # may be None
        clean_acct  = re.sub(r"[^A-Za-z0-9]", "", acct_3p or "")
        zip_3p   = ship_doc.custom_third_party_postal
        
        if bill_3p and len(clean_acct) == 6:          
            if not (acct_3p and zip_3p):
                frappe.throw(_("Please fill Customer Account # and Billing ZIP for third‑party billing."))

            if len(clean_acct) == 0:
                frappe.throw(_("Account number must contain at least one letter/number."))

            third_party_block = {
                "payment": {
                    "type":        "THIRD_PARTY",
                    "account":     clean_acct,
                    "postal_code": zip_3p.strip(),
                    "country":     "US",
                }
            }

        # ------------------------------------------------------------------
        # Compose EasyPost shipment payload
        # ------------------------------------------------------------------
        shipment: Dict[str, Any] = {
            "to_address": {
                "name":    f"{delivery_contact.first_name} {delivery_contact.last_name}",
                "street1": delivery_address.address_line1,
                "street2": delivery_address.address_line2,
                "city":    delivery_address.city,
                "state":   delivery_address.state,
                "zip":     delivery_address.pincode,
                "country": delivery_address.country,
                "phone":   self._phone(delivery_contact, delivery_address),
            },
            "from_address": {
                "name":    f"{pickup_contact.first_name} {pickup_contact.last_name}",
                "street1": pickup_address.address_line1,
                "street2": pickup_address.address_line2,
                "city":    pickup_address.city,
                "state":   pickup_address.state,
                "zip":     pickup_address.pincode,
                "country": pickup_address.country,
                "phone":   self._phone(pickup_contact, pickup_address),
            },
            **parcel_block,           # ← single or multi‑piece
            "options": {
                "currency": self.currency,
                **third_party_block,
            },
        }

        if delivery_contact.email_id:
            shipment["to_address"]["email"] = delivery_contact.email_id
        if pickup_contact.email_id:
            shipment["from_address"]["email"] = pickup_contact.email_id

        try:
            if not mps:
                ep_url = "https://api.easypost.com/v2/shipments"
                payload = {"shipment": shipment}
            else:
                # ── build an Order with one Shipment per parcel ──
                ep_url = "https://api.easypost.com/v2/orders"
                payload = {
                    "order": {
                        "to_address":   shipment["to_address"],
                        "from_address": shipment["from_address"],
                        "shipments": [
                            {"parcel": p, "options": shipment["options"]} for p in parcels
                        ],
                    }
                }
            #frappe.log_error(title="ep_url", message=f"{ep_url}")
            response = requests.post(ep_url, auth=(self.api_key, ""), json=payload)
            response_dict = response.json()

            # ---- Handle EasyPost API errors --------------------------------
            if "error" in response_dict:
                frappe.throw(response_dict["error"]["message"], title=_("EasyPost"))

            available_services: List[Dict[str, Any]] = []
            for service in response_dict.get("rates", []):
                available_service = self.get_service_dict(
                    service,
                    1 if mps else parcel_count,    # Order returns full price already
                    response_dict.get("id"),
                    is_order=mps
                )
                available_services.append(available_service)
            
            #frappe.log_error(title="available_services from easypost", message=f"{available_services}")

            # ─────────────────────────────────────────────────────────────
            # UPSDirect integration for third‑party UPS billing (single‑parcel)
            # ─────────────────────────────────────────────────────────────
            if bill_3p:
                #frappe.log_error(title="Bill_3p single", message=f"bill_3p: {bill_3p}, clean_acct: {acct_3p}")
                ups = UPSDirect()
                try:
                    ups_rates = ups.rate(
                        ups.ups_shipper_num,
                        clean_acct,
                        zip_3p.strip(),
                        shipment["to_address"],
                        shipment["from_address"],
                        parcels
                    )

                    rated_shipments = ups_rates.get("RateResponse", {}).get("RatedShipment", [])
                    if isinstance(rated_shipments, dict):
                        rated_shipments = [rated_shipments]

                    for rated in rated_shipments:
                        svc = rated["Service"]
                        code = svc.get("Code") or ""
                        nice_name = (
                            svc.get("Description")
                            or UPSDirect.SERVICE_MAP.get(code)
                            or code
                        )

                        available_services.append(
                            frappe._dict({
                                "service_provider": "UPS",
                                "carrier":          "UPS",
                                "service_name":     nice_name,
                                "total_price":      flt(rated["TotalCharges"]["MonetaryValue"]),
                                "delivery_days":    rated.get("GuaranteedDaysToDelivery"),
                                "service_id":       code,
                                "shipment_id":      None,
                                "ups_shipper_number": ups.ups_shipper_num,
                                "ups_account":        clean_acct,
                                "ups_postal_code":    zip_3p.strip(),
                                "to_address":         shipment["to_address"],
                                "from_address":       shipment["from_address"],
                                "parcels":             parcels,
                            })
                        )

                except HTTPError as e:
                    print("UPS HTTPError:", getattr(e.response, "text", str(e)), flush=True)
                    raise  # propagate the HTTP 4xx/5xx back to Frappe
                except Exception as e:
                    frappe.throw(_(f"Unexpected error creating UPS label: {str(e)}"))

            # ---- Filter by carrier for 3‑P billing -------------------------
            if bill_3p:
                if len(clean_acct) == 6:      # UPS (6‑digit acct)
                    available_services = [s for s in available_services if s.get("service_provider") == "UPS"]
                elif len(clean_acct) == 9:    # FedEx (9‑digit acct)
                    available_services = [s for s in available_services if s.get("carrier", "").lower() == "fedex"]

            # FedEx-selector by parcel-count (easypost fedex account does not support multiple parcels)
            if parcel_count > 1:
                # multi-parcel → keep *our* FedEx account (“FEDEX”), drop EasyPost’s pooled account (“FEDEXDEFAULT”)
                available_services = [
                    s for s in available_services
                    if (s.get("carrier_code") or "").upper() != "FEDEXDEFAULT"
                ]
            else:
                # single-parcel → keep EasyPost’s “FEDEXDEFAULT”, drop *our* “FEDEX”
                available_services = [
                    s for s in available_services
                    if (s.get("carrier_code") or "").upper() != "FEDEX"
                ]
    
            return available_services

        except Exception:
            show_error_alert("fetching EasyPost prices")

    # =========================================================================
    # PURCHASE LABELS (EasyPost or UPSDirect)
    # =========================================================================
    def create_shipment(self, service_info, delivery_address):
        # ─────────────────────────────────────────────────────────────────
        # Custom UPS purchase flow
        # ─────────────────────────────────────────────────────────────────
        if isinstance(service_info, dict):
            service_info = frappe._dict(service_info)
            
        # ───────────────────────────
        # EasyPost ORDER (multi-parcel) - Requires the "orders" api endpoint
        # ───────────────────────────
        if service_info.get("is_order"):
            buy_body = {
                "carrier": service_info.carrier_code,   # "FedExDefault", "UPSDAP", etc.
                "service": service_info.service_code    # "FEDEX_GROUND", "Ground", ...
            }
            
            rate_check = requests.get(
                f"https://api.easypost.com/v2/orders/{service_info['order_id']}",
                auth=(self.api_key, ""),
            ).json()
            #frappe.log_error("EZP Order detail", json.dumps(rate_check)[:200000])
            
            resp = requests.post(
                f"https://api.easypost.com/v2/orders/{service_info['order_id']}/buy",
                auth=(self.api_key, ""),
                json=buy_body,
            ).json()

            # Handle explicit EasyPost error objects
            if "error" in resp:
                frappe.throw(_("EasyPost Error: {0}").format(resp["error"].get("message")))

            # Different API revisions: 'shipments' is sometimes nested
            shipments = (
                resp.get("shipments") or
                resp.get("order", {}).get("shipments") or
                []
            )

            if not shipments:
                frappe.throw(_("EasyPost returned no shipments for this order."))

            # collect tracking & labels
            labels   = [s["postage_label"]["label_url"] for s in shipments]
            tracknos = [s["tracking_code"]              for s in shipments]

            first_label = labels[0]
            merged_pdf_url = self._pngs_to_pdf(labels)
            postage_stub = {"label_url": first_label, "label_png_url": first_label, "label_pdf_url": first_label.replace(".png", ".pdf")}

            return {
                "service_provider": "EasyPost",
                "order_id":  service_info["order_id"],
                "shipment_id":     service_info["order_id"],
                "carrier":   self.get_carrier(service_info["carrier"], post_or_get="post"),
                "carrier_service": service_info["service_name"],
                "shipment_amount": service_info["total_price"],
                "awb_number": ", ".join(tracknos),
                "label_bundle": labels,   # raw PNGs if you need them
                "shipping_label": merged_pdf_url,
                "postage_label":  postage_stub,
                "awb_number":      ", ".join(tracknos),
            }


        # ───────────────────────────
        # UPS 3rd Party Billing ORDER (multiple & single parcel)
        # ───────────────────────────
        if (service_info.get("service_provider") or "").upper() == "UPS":
            ups = UPSDirect()
            try:
                resp = ups.ship(
                    service_info.ups_shipper_number,
                    service_info.ups_account,
                    service_info.ups_postal_code,
                    service_info.to_address,
                    service_info.from_address,
                    service_info.parcels,
                    service_info.service_id,
                )

                results = resp["ShipmentResponse"]["ShipmentResults"]

                # ---------- collect every package's tracking + label ----------
                pkg_results = results.get("PackageResults", [])
                if isinstance(pkg_results, dict):          # UPS returns dict when just 1 pkg
                    pkg_results = [pkg_results]

                tracking_numbers = []
                png_urls = []

                for pr in pkg_results:
                    tracking_numbers.append(pr.get("TrackingNumber"))

                    lbl = pr.get("LabelImage") or pr.get("ShippingLabel", {})
                    graphic = lbl.get("GraphicImage")
                    fmt = (lbl.get("ImageFormat") or {}).get("Code", "").lower()

                    if graphic and fmt:
                        data_uri = f"data:image/{fmt};base64,{graphic}"
                        png_urls.append(self._save_base64_png(data_uri))

                if not png_urls:
                    frappe.throw(_("UPS did not return any label images."))

                # ---------- convert every PNG ➜ single PDF ----------
                pdf_url = (
                    self._png_to_pdf(png_urls[0])          # 1 parcel
                    if len(png_urls) == 1
                    else self._pngs_to_pdf(png_urls)       # 2+ parcels
                )

                return {
                    "service_provider": "UPS",
                    "shipment_id":      tracking_numbers[0],
                    "carrier":          "UPS",
                    "carrier_service":  service_info.service_name,
                    "shipment_amount":  service_info.total_price,
                    "awb_number":       ", ".join(tracking_numbers),
                    "label_bundle":     png_urls,      # raw PNGs if you need them
                    "shipping_label":   pdf_url,       # one multi-page PDF
                    "postage_label": {
                        "label_url":     pdf_url,
                        "label_png_url": png_urls[0],  # first page preview
                        "label_pdf_url": pdf_url,
                    },
                }
            except HTTPError as e:
                # pull the JSON explanation if UPS sent one
                try:
                    err_json = e.response.json()
                    detail = (
                        err_json.get("response", {})
                                .get("errors", [{}])[0]
                                .get("message")
                        or err_json
                    )
                except Exception:
                    detail = e.response.text or str(e)

                frappe.throw(_("UPS API Error: {0}").format(detail))


        # ───────────────────────────
        # EasyPost ORDER (single-parcel) - Requires the "shipments" api endpoint
        # ───────────────────────────
        rate = {"rate": {"id": service_info["service_id"]}}

        if not self.enabled or not self.api_key:
            return []

        try:
            response = requests.post(
                f'https://api.easypost.com/v2/shipments/{service_info["shipment_id"]}/buy',
                auth=(self.api_key, ""),
                json=rate,
            )

            response_data = response.json()
            if "error" in response_data:
                frappe.throw(_("EasyPost Error: {0}").format(response_data["error"].get("message", "Unknown error")))

            if "failed_parcels" in response_data:
                error = response_data["failed_parcels"][0]["errors"]
                frappe.msgprint(_(f"Error occurred while creating Shipment: {error}"), indicator="orange", alert=True)
            else:
                                # ------------------------------------------------------------------
                # 1 We have a PAID EasyPost Shipment – grab the PNG label
                # ------------------------------------------------------------------
                lbl_resp = requests.get(
                    f"https://api.easypost.com/v2/shipments/{service_info['shipment_id']}/label",
                    auth=(self.api_key, ""),
                    params={"file_format": "png"},
                ).json()

                png_url = lbl_resp["postage_label"]["label_url"]

                # 2 Convert that single PNG → single-page PDF, save to File
                pdf_url = self._pngs_to_pdf([png_url]) 

                # 3 Normalise the postage-label stub (helpful for API users)
                postage_stub = {
                    "label_url":     pdf_url,      # the PDF we just stored
                    "label_png_url": png_url,      # raw PNG (preview)
                    "label_pdf_url": pdf_url,
                }

                # 4 Return exactly like the multi-parcel path
                return {
                    "service_provider": "EasyPost",
                    "shipment_id": service_info["shipment_id"],
                    "carrier": self.get_carrier(service_info["carrier"], post_or_get="post"),
                    "carrier_service": service_info["service_name"],
                    "shipment_amount": service_info["total_price"],
                    "awb_number": response_data["tracker"]["tracking_code"],
                    "label_bundle":     [png_url],      # keep parity with Orders
                    "shipping_label":   pdf_url,        # what ERPNext will open
                    "postage_label":    postage_stub,
                }
        except Exception:
            show_error_alert("creating EasyPost Shipment")

    def get_label(self, shipment_id):
        """
        Return a printable label URL.

        • Multi-parcel Order: ask EasyPost for ONE PNG that contains a page per
          parcel. Poll up to ~6 s; fall back to the first parcel’s PNG.
        • Single-parcel Shipment: unchanged.
        """
        try:
            # ── Multi-parcel Order ───────────────────────────────────────────
            if shipment_id.startswith("order_"):

                #requests.post(
                #    f"https://api.easypost.com/v2/orders/{shipment_id}/label",
                #    auth=(self.api_key, ""),
                #    json={"file_format": "PNG"},
                #    timeout=20,
                #)

                # always fetch the fresh Order object
                order = requests.get(
                    f"https://api.easypost.com/v2/orders/{shipment_id}",
                    auth=(self.api_key, ""),
                    timeout=20,
                ).json()

                # collect every individual PNG
                label_urls = [
                    s.get("postage_label", {}).get("label_url")
                    for s in order.get("shipments", [])
                    if s.get("postage_label")
                ]
                label_urls = [u for u in label_urls if u]

                if not label_urls:
                    frappe.throw(_("EasyPost did not return any label URLs for this Order."))

                # single box? just return it
                if len(label_urls) == 1:
                    return label_urls[0]

                # 2+ boxes → stitch them
                return self._pngs_to_pdf(label_urls)

                frappe.throw(_("EasyPost did not return any label URLs for this Order."))

            # ── Single-parcel Shipment → always return a local PDF ─────────

            # 1️⃣  Ask EasyPost for the PNG (never mind self.label_format)
            shp = requests.get(
                f"https://api.easypost.com/v2/shipments/{shipment_id}/label",
                auth=(self.api_key, ""),
                params={"file_format": "png"},
            ).json()

            png_url = shp["postage_label"]["label_url"]

            # 2️⃣  Convert PNG → PDF  (re-runs instantly if we’ve done it before)
            pdf_url = self._png_to_pdf(png_url)

            # 3️⃣  OPTIONAL: write back to the Shipment doctype so future
            #     clicks skip the network hop completely.
            try:
                doc = frappe.get_doc("Shipment", {"easy_post_shipment_id": shipment_id})
                if doc and (doc.get("shipping_label") != pdf_url):
                    doc.db_set("shipping_label", pdf_url, update_modified=False)
            except Exception:
                # ignore if mapping field doesn’t exist or any other issue
                pass

            return pdf_url


        except Exception:
            show_error_alert("printing EasyPost Label")



    def get_tracking_data(self, ep_id: str):
        """Return tracking data for a single-parcel **or** multi-parcel shipment."""
        try:
            if ep_id.startswith("order_"):                         # <-- NEW
                r = requests.get(f"https://api.easypost.com/v2/orders/{ep_id}",
                                  auth=(self.api_key, "")).json()
                
                chosen_carrier = (r.get("selected_rate") or {}).get("carrier", "").upper()
                msgs = [
                    m["message"]
                    for m in r.get("messages", [])
                    if (m.get("carrier", "") or "").upper() == chosen_carrier
                ]
                             
                # collect trackers from every parcel in the order
                tracking_codes = [
                    (sh.get("tracker") or {}).get("tracking_code") or sh.get("tracking_code")
                    for sh in r.get("shipments", [])
                ]

                return {
                    "awb_number": ", ".join(filter(None, tracking_codes)),
                    "tracking_status": r.get("status"),
                    "tracking_status_info": " / ".join(msgs) if msgs else None,
                    "tracking_url": f"https://track.easypost.com/{tracking_codes[0]}"
                                     if tracking_codes else None
                }

            # ---------- existing single-parcel logic ----------
            r = requests.get(f"https://api.easypost.com/v2/shipments/{ep_id}",
                             auth=(self.api_key, "")).json()
            t = r["tracker"]
            return {
                "awb_number": t["tracking_code"],
                "tracking_status": t["status"],
                "tracking_status_info": t["status_detail"],
                "tracking_url": t["public_url"],
            }
        except Exception:
            show_error_alert("updating EasyPost Shipment")


    def get_service_dict(self, service, multiplier, shipment_or_order_id, is_order=False):
        available_service = frappe._dict()
        available_service.service_provider = "EasyPost"
        raw_carrier  = service["carrier"]            # ← add
        raw_service  = service["service"]            # ← add
        raw_carrier_api = self.get_carrier(service["carrier"], post_or_get="get")
        available_service.carrier = self._pretty(raw_carrier_api)
        available_service.service_name = self._pretty(raw_service)
        available_service.total_price = flt(service["rate"]) * multiplier
        available_service.delivery_days = service.get("delivery_days")
        available_service.service_id = service["id"]
        available_service.carrier_code = raw_carrier   # ← add
        available_service.service_code = raw_service   # ← add
        available_service.shipment_id = None if is_order else shipment_or_order_id
        available_service.order_id    = shipment_or_order_id if is_order else None
        available_service.is_order    = is_order
        return available_service

    def get_carrier(self, carrier_name, post_or_get=None):
        if carrier_name in ("easypost", "EasyPost"):
            return "EasyPost" if post_or_get == "get" else "easypost"
        return carrier_name.upper() if post_or_get == "get" else carrier_name.lower()

    # ------------------------------------------------------------------
    # Private helper to store a base‑64 label image and return public URL
    # ------------------------------------------------------------------
    def _save_base64_png(self, data_uri: str) -> str:
        if not data_uri or not data_uri.startswith("data:image"):
            return data_uri

        header, b64 = data_uri.split(",", 1)
        ext = header.split("/")[1].split(";")[0]

        raw_bytes = base64.b64decode(b64)
        img = Image.open(io.BytesIO(raw_bytes))
        img = img.rotate(-90, expand=True)  # 90° clockwise

        fname = f"{uuid.uuid4()}.{ext}"
        file_path = os.path.join(get_files_path(is_private=True), fname)
        img.save(file_path, format=ext.upper())

        file_doc = frappe.get_doc({
            "doctype":   "File",
            "file_name": fname,
            "file_url":  f"/private/files/{fname}",
            "is_private": 1,
        })
        file_doc.insert(ignore_permissions=True)

        return f"{get_url()}{file_doc.file_url}"

    # ----------------------------------------------------------------------
    # Combine N remote PNGs into one multi-page PDF and return the File URL
    # ----------------------------------------------------------------------
    def _png_to_pdf(self, url: str) -> str:
        """Wrap single-item call so we don’t keep writing [url]."""
        return self._pngs_to_pdf([url])


    def _pngs_to_pdf(self, urls: list[str]) -> str:
        imgs: list[Image.Image] = []

        for url in urls:
            img = self._open_label_image(url)
            # im = im.rotate(-90, expand=True)
            imgs.append(img)

        if not imgs:
            frappe.throw(_("No PNGs were supplied for PDF merge."))

        fname = f"{uuid.uuid4()}.pdf"
        file_path = os.path.join(get_files_path(is_private=True), fname)

        # write a multi-page PDF – Pillow does this if save_all=True
        imgs[0].save(
            file_path,
            "PDF",
            save_all=True,
            append_images=imgs[1:]
        )

        file_doc = frappe.get_doc({
            "doctype":   "File",
            "file_name": fname,
            "file_url":  f"/private/files/{fname}",
            "is_private": 1,
        }).insert(ignore_permissions=True)

        return f"{get_url()}{file_doc.file_url}"

    # ----------------------------------------------------------------------
    # Open a label image whether it lives on S3 or in /private/files
    # ----------------------------------------------------------------------
    def _open_label_image(self, url: str) -> "Image":
        """
        • For remote (S3) links   → GET over HTTP.
        • For local /private/…    → read directly from the filesystem
          so we don’t need authentication cookies.
        """
        # Is it one of *our* private files?
        base = get_url().rstrip("/")            # e.g. https://erp.caseclub.com
        if url.startswith(f"{base}/private/files/"):
            fname = url.split("/private/files/")[1]
            file_path = os.path.join(get_files_path(is_private=True), fname)
            return Image.open(file_path).convert("RGB")

        # Otherwise download
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        return Image.open(io.BytesIO(r.content)).convert("RGB")
    
    # ─────────────────────────────────────────────
    # Helper: always return a non-blank phone string
    # ─────────────────────────────────────────────
    def _phone(self, contact, address) -> str:
        """
        FedEx's REST API will error if `phone` is missing or null.
        Priority:
          1) contact.phone
          2) address.phone  (Address DocType has an optional phone field)
          3) Company default phone (Settings → Company)
          4) hard-coded fallback so test mode never blows up
        """
        return (
            (getattr(contact, "phone", "") or "").strip()
            or (getattr(address, "phone", "") or "").strip()
            or frappe.db.get_single_value("Company", "phone_no")  # may be None
            or "714-555-0000"     # ← safe dummy; change to whatever you like
        )



