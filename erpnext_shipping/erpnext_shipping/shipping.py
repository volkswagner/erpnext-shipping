# Copyright (c) 2020, Frappe Technologies and contributors
# For license information, please see license.txt
#/apps/erpnext_shipping/erpnext_shipping/erpnext_shipping
import base64
import uuid
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

from frappe.utils.file_manager import get_files_path

@frappe.whitelist()
def fetch_shipping_rates(
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

    if sendcloud_enabled and pickup_from_type == "Company":
        sendcloud = SendCloudUtils()
        sendcloud_prices = (
            sendcloud.get_available_services(delivery_address=delivery_address, parcels=parcels) or []
        )
        sendcloud_prices = match_parcel_service_type_carrier(sendcloud_prices, "carrier", "service_name")
        shipment_prices += sendcloud_prices

    if easypost_enabled:
        pickup_contact = None
        delivery_contact = None
        if pickup_from_type != "Company":
            pickup_contact = get_contact(pickup_contact_name)
        else:
            pickup_contact = get_company_contact(user=pickup_contact_name)
            pickup_contact.email_id = pickup_contact.pop("email", None)

        # if delivery_to_type != "Company":
        #     delivery_contact = get_contact(delivery_contact_name)
        # else:
        #     delivery_contact = get_company_contact(user=pickup_contact_name)
        #     delivery_contact.email_id = delivery_contact.pop("email", None)

        delivery_contact = get_contact(delivery_contact_name)

        easypost = EasyPostUtils()
        easypost_prices = (
            easypost.get_available_services(
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

def ensure_label_file(shipment_doc, label_value: str) -> str:
    """
    Accepts:
      • data-URI
      • https:// remote URL
      • /private/files/…  *or*  https://<this-site>/private/files/…
    Ensures a File in /private/files **and** attaches it to the Shipment.
    Returns the absolute URL you can store / print.
    """
    if not label_value:
        return ""

    # ─────────────────────────────────────────────────────────────────
    # Case 1 – already an internal file (relative OR absolute URL)
    # -----------------------------------------------------------------
    site_url = frappe.utils.get_url().rstrip("/")             # e.g. "https://erp.caseclub.com"
    internal_prefix = f"{site_url}/private/files/"

    if label_value.startswith("/private/files/"):
        file_url = label_value                                  # already relative

    elif label_value.startswith(internal_prefix):
        file_url = label_value[len(site_url):]                  # strip site → relative

    # ─────────────────────────────────────────────────────────────────
    # Case 2 – data-URI  (unchanged)
    # -----------------------------------------------------------------
    elif label_value.startswith("data:image"):
        header, b64 = label_value.split(",", 1)
        ext   = header.split("/")[1].split(";")[0]
        fname = f"{uuid.uuid4()}.{ext}"
        file_path = os.path.join(get_files_path(is_private=True), fname)
        with open(file_path, "wb") as f:
            f.write(base64.b64decode(b64))
        file_url = f"/private/files/{fname}"

    # ─────────────────────────────────────────────────────────────────
    # Case 3 – genuine remote URL  (unchanged)
    # -----------------------------------------------------------------
    else:
        resp = requests.get(label_value, timeout=15)
        resp.raise_for_status()
        ext   = label_value.rsplit(".", 1)[-1].split("?")[0] or "png"
        fname = f"{uuid.uuid4()}.{ext}"
        file_path = os.path.join(get_files_path(is_private=True), fname)
        with open(file_path, "wb") as f:
            f.write(resp.content)
        file_url = f"/private/files/{fname}"

    # ────────────────────────────────────────────────────────────
    # Attach (or re-attach) to the Shipment
    if not frappe.db.exists(
        "File",
        {"file_url": file_url, "attached_to_doctype": "Shipment", "attached_to_name": shipment_doc.name}
    ):
        file_doc = frappe.get_doc({
            "doctype":             "File",
            "file_name":           os.path.basename(file_url),
            "file_url":            file_url,
            "is_private":          1,
            "attached_to_doctype": "Shipment",
            "attached_to_name":    shipment_doc.name,
        })
        file_doc.insert(ignore_permissions=True)

    return f"{frappe.utils.get_url()}{file_url}"

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
    # Create Shipment for the selected provider
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
            delivery_company_name=delivery_company_name,
            delivery_address=delivery_address,
            shipment_parcel=shipment_parcel,
            description_of_content=description_of_content,
            value_of_goods=value_of_goods,
            delivery_contact=delivery_contact,
            service_info=service_info,
        )

    # Handle both EasyPost and direct-UPS purchases here
    if service_info.get("service_provider") in (EASYPOST_PROVIDER, "UPS"):
        # convert the incoming dict to frappe._dict so attribute access works
        if isinstance(service_info, dict):
            service_info = frappe._dict(service_info)

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
        # ------------------------------------------------------------------
        # Save custom UPS label info so the UI buttons have something to open
        # ------------------------------------------------------------------
        if shipment_info.get("shipping_label"):
            try:
                file_url = ensure_label_file(shipment, shipment_info["shipping_label"])
                shipment.db_set("custom_shipping_label", file_url)
            except frappe.DoesNotExistError:
                pass                          # site hasn't got the custom field yet

        if shipment_info.get("postage_label"):
            try:
                shipment.db_set("custom_postage_label", json.dumps(shipment_info["postage_label"]))
            except frappe.DoesNotExistError:
                pass

        # FETCH delivery_notes from Shipment child table if not provided/empty
        if not delivery_notes:
            delivery_notes = [row.delivery_note for row in shipment_doc.get("shipment_delivery_note") or [] if row.delivery_note]

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
        for label_url in _labels:
            content = sendcloud.download_label(label_url)
            file_url = save_label_as_attachment(shipment, content)
            shipping_label.append(file_url)
    elif service_provider == EASYPOST_PROVIDER:
        easypost = EasyPostUtils()
        shipping_label = easypost.get_label(shipment_id)
    elif service_provider == "UPS":
        # 1) try direct URL saved earlier
        shipping_label = shipment_doc.get("custom_shipping_label")

        # 2) fall back to extracting from the JSON blob (in case the first save failed)
        if (not shipping_label) and shipment_doc.get("custom_postage_label"):
            try:
                pl = json.loads(shipment_doc.custom_postage_label)
                shipping_label = (
                    pl.get("label_url") or
                    pl.get("label_png_url")
                )
            except Exception:
                pass


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
    elif service_provider == "UPS":
        # Use the URL we saved on create_shipment
        shipping_label_url = shipment_doc.get("custom_shipping_label")
        is_byte_data = False


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

    shipment_doc = frappe.get_doc("Shipment", shipment)  # Renamed for clarity
    shipment_doc.db_set(
        {
            "awb_number": tracking_data.get("awb_number"),
            "tracking_status": tracking_data.get("tracking_status"),
            "tracking_status_info": tracking_data.get("tracking_status_info"),
            "tracking_url": tracking_data.get("tracking_url"),
        }
    )

    # FETCH delivery_notes from Shipment child table if not provided/empty
    if not delivery_notes:
        delivery_notes = [row.delivery_note for row in shipment_doc.get("shipment_delivery_note") or [] if row.delivery_note]

    # CONSTRUCT minimal shipment_info from Shipment doc (fallback)
    shipment_info = None
    if delivery_notes:  # Only build if we'll use it
        shipment_info = {
            "carrier": shipment_doc.carrier,
            "carrier_service": shipment_doc.carrier_service,
        }

    if delivery_notes:
        update_delivery_note(delivery_notes=delivery_notes, shipment_info=shipment_info, tracking_info=tracking_data)


def update_delivery_note(delivery_notes, shipment_info=None, tracking_info=None):
    # Update Shipment Info in Delivery Note
    # Using db_set since some services might not exist
    if isinstance(delivery_notes, str):
        delivery_notes = json.loads(delivery_notes)

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
