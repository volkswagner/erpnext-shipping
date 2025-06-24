# apps/erpnext_shipping/erpnext_shipping/utils/ups_direct.py
import requests, json, datetime, uuid
import frappe
import re
from requests.exceptions import HTTPError

# --- HARD-CODED creds for POC ---
UPS_CLIENT_ID     = "UPS_CLIENT_ID"
UPS_CLIENT_SECRET = "UPS_CLIENT_SECRET"
UPS_SHIPPER_NUM   = "UPS_SHIPPER_NUM"          # your own 6-char account

# Endpoints (test vs prod)
UPS_BASE_URL   = "https://wwwcie.ups.com"          # ← add this constant
UPS_OAUTH_URL  = f"{UPS_BASE_URL}/security/v1/oauth/token"
UPS_RATE_URL = f"{UPS_BASE_URL}/api/rating/v2205/Shop"   # <— path param
UPS_SHIP_URL   = f"{UPS_BASE_URL}/api/shipments/v1/ship"

def build_parcel_list(rows):
    parcels = []
    for p in rows:
        parcels.append(p.copy())      # rows are already dicts here
    return parcels

class UPSDirect:
    def __init__(self):
        self.token = self._oauth()
        self._base_url = UPS_BASE_URL
    
    SERVICE_MAP = {
        # Domestic
        "01": "Next Day Air",
        "02": "2nd Day Air",
        "03": "Ground",
        "12": "3-Day Select",
        "13": "Next Day Air Saver",
        "14": "Next Day Air Early A.M.",
        "59": "2nd Day Air A.M.",

        # International
        "07": "Worldwide Express",
        "08": "Worldwide Expedited",
        "11": "Standard",
        "54": "Worldwide Express Plus",
        "65": "Worldwide Saver",
        "96": "Worldwide Express Freight",

        # SurePost
        "92": "SurePost Less than 1 lb",
        "93": "SurePost 1 lb or Greater",
        "94": "SurePost BPM",
        "95": "SurePost Media Mail",

        # Access Point
        "70": "Access Point Economy",

        # Today (same-day) services
        "82": "Today Standard",
        "83": "Today Dedicated Courier",
        "84": "Today Intercity",
        "85": "Today Express",
        "86": "Today Express Saver",
    }

    
    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.token}",      # the OAuth token
            "x-merchant-id": UPS_CLIENT_ID,               # ← back to client-id
            "Accept": "application/json",
            "Content-Type": "application/json",
            "transId": str(uuid.uuid4()),
            "transactionSrc": "ERPNext",
        }

    # ---------- auth ----------
    def _oauth(self):
        # UPS wants Basic auth + x-merchant-id on the token request
        import base64

        # construct Basic header
        creds = f"{UPS_CLIENT_ID}:{UPS_CLIENT_SECRET}".encode("utf-8")
        basic_token = base64.b64encode(creds).decode("utf-8")

        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "x-merchant-id": UPS_CLIENT_ID,
            "Authorization": f"Basic {basic_token}",
        }
        payload = "grant_type=client_credentials"

        r = requests.post(
            UPS_OAUTH_URL,
            headers=headers,
            data=payload
        )
        r.raise_for_status()
        return r.json()["access_token"]

    # ---------- build helpers ----------
    def _address(self, d):
        return {
            "Address": {
                "AddressLine": [d["street1"]],
                "City": d["city"],
                "StateProvinceCode": d["state"],
                "PostalCode": d["zip"],
                "CountryCode": "US",
            }
        }

    def _phone(self, raw: str | None) -> dict | None:
        """
        Return {"Phone": {"Number": "7147798794"}}  or  None.
        UPS wants 10–15 digits, no punctuation.
        """
        if not raw:
            return None
        num = re.sub(r"\D", "", raw)[:15]
        if len(num) < 10:        # UPS rejects shorter numbers
            return None
        return {"Phone": {"Number": num}}

    def _package(self, parcel):
        """
        Build a Package block that satisfies both the /Shop (rating)
        and /ship (buy label) endpoints.
        """
        dims = {
            "UnitOfMeasurement": {"Code": "IN"},
            "Length": str(parcel["length"]),
            "Width":  str(parcel["width"]),
            "Height": str(parcel["height"]),
        }
        weight = {
            "UnitOfMeasurement": {"Code": "LBS"},
            "Weight": str(parcel["weight"] / 16.0)   # oz ➜ lb
        }

        return {
            # NEW field name for the Ship API
            "Packaging":      {"Code": "02"},        # 02 = Your Package
            # Legacy field retained so the Rate API keeps working
            "PackagingType":  {"Code": "02"},
            "Dimensions":     dims,
            "PackageWeight":  weight,
        }


    # ---------- rate ----------
    def rate(self, shipper_num, bill_acct, bill_zip, to_addr, from_addr, parcels):
        body = {
            "RateRequest": {
                 "Request": {
                     "SubVersion": "2205"
                 },
                "PickupType": {                  # ← Pascal-case
                    "Code": "03"                 # 03 = Customer Counter
                },
                "CustomerClassification": {
                    "Code": "04"                 # 04 = Occasional Shipper
                },
                "Shipment": {
                    "Shipper": {
                        "Name": "Case Club",
                        "ShipperNumber": shipper_num,
                        **self._address(from_addr)        # returns {"Address":{…}}
                    },
                    "ShipFrom": self._address(from_addr),
                    "ShipTo":   self._address(to_addr),
                    "Package": [self._package(p) for p in parcels]
                }
            }
        }

        r = requests.post(UPS_RATE_URL, json=body, headers=self._headers(), timeout=30)

        if r.status_code >= 400:
            body_text = r.text.strip() or "(empty)"
            frappe.log_error(f"UPS {r.status_code} response", body_text)
            # try to pull JSON errors, but fall back to raw text
            try:
                err = r.json()
            except ValueError:
                err = body_text
            raise HTTPError(f"UPS error {r.status_code}: {err}", response=r)

        
        # Print the raw ups rate response
        #print("RAW UPS RATE RESPONSE:", json.dumps(r.json(), indent=2), flush=True)

        r.raise_for_status()
        return r.json()



    # ---------- ship / buy ----------
    def ship(self, shipper_num, bill_acct, bill_zip, to_addr, from_addr, parcels, service_code):
        today = datetime.date.today().strftime("%Y%m%d")      
        
        if bill_acct and bill_acct.strip() != shipper_num:
            # third-party billing
            payer_block = {
                "Type": "01",                 # 01 = transportation charges
                "BillThirdParty": {
                    "AccountNumber": bill_acct.strip(),
                    "Address": {
                        "PostalCode": bill_zip,
                        "CountryCode": "US"
                    }
                }
            }
        else:
            # shipper pays
            payer_block = {
                "Type": "01",
                "BillShipper": {
                    "AccountNumber": shipper_num
                }
            }

        body = {
            "ShipmentRequest": {
                "Request": {"SubVersion": "2205"},
                "Shipment": {
                    "Shipper":  self._shipper("Case Club", shipper_num, from_addr),
                    "ShipFrom": self._party(from_addr.get("name"),  from_addr),
                    "ShipTo":   self._party(to_addr.get("name"),    to_addr),
                    "Service": {"Code": service_code},
                    "PaymentInformation": {
                        "ShipmentCharge": [payer_block]
                    },
                    "Package": [self._package(p) for p in parcels],
                    "ShipmentDate": today,
                },
                "LabelSpecification": {
                    "LabelImageFormat": {"Code": "PNG"},
                    "LabelDelivery":    {"LabelLinkIndicator": "true"}
                }
            }
        }
        #print(f"Ship Payload: {body}", flush=True)
        frappe.log_error("DEBUG UPS ship URL", UPS_SHIP_URL)
        r = requests.post(UPS_SHIP_URL, json=body, headers=self._headers())
        if r.status_code >= 400:
            frappe.log_error(
                title=f"UPS {r.status_code} body",
                message=r.text[:2000] or "(empty)"
            )
            r.raise_for_status()
        
        #print("RAW UPS Ship RESPONSE:", json.dumps(r.json(), indent=2), flush=True)
        
        r.raise_for_status()
        return r.json()
    

    # --- new helpers -----------------------------------------------------------
    def _shipper(self, nickname: str, acct_no: str, addr: dict) -> dict:
        """Block for the mandatory Shipper object."""
        block = {
            "Name": nickname,               # required
            "ShipperNumber": acct_no,       # required
            "Address": self._address(addr)["Address"],
        }
        # label looks nicer with a contact name too
        if addr.get("name"):
            block["AttentionName"] = addr["name"]
            
        phone_block = self._phone(addr.get("phone"))
        if phone_block:
            block.update(phone_block)
        
        return block


    def _party(self, person_or_co: str, addr: dict) -> dict:
        # Base block
        if addr.get("company"):  # commercial address
            block = {
                "CompanyName":   person_or_co,
                "AttentionName": person_or_co,
                "Address":       self._address(addr)["Address"],
            }
        else:  # residential / individual – UPS wants Name
            block = {
                "Name":    person_or_co,
                "AttentionName": person_or_co,
                "Address": self._address(addr)["Address"],
            }

        # Inject phone when valid
        phone_block = self._phone(addr.get("phone"))
        if phone_block:
            block.update(phone_block)

        return block

