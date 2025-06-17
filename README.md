# ERPNext Shipping Integration

A USA-focused shipping integration for ERPNext using EasyPost (with UPS, USPS & FedEx support).  

> **Prerequisites**  
> - An EasyPost API key  
> - A UPS Developer account with a “Rating” & “Shipping” app (https://developer.ups.com)  

---

## 🚀 Features

- **Rate Comparison**  
  Fetch and compare live shipping rates from multiple carriers (UPS, USPS, FedEx) via EasyPost.  
- **Preferred Services**  
  Mark your most‐used carrier-service combinations and surface them at the top.  
- **One-Click Shipment Creation**  
  Create an ERPNext Shipment record and an EasyPost shipment in one step.  
- **Label Printing**  
  • **Local**: Opens the PDF in a new tab for your browser’s print dialog  
  • **Network**: Send labels directly to a CUPS printer on your network  
- **Dimension Templates**  
  Save and reuse common box or pallet dimensions.  
- **Tracking & Status**  
  Automatically pull tracking numbers into the Shipment record.  

---

## 🔌 Integrations

- **Fully Tested**  
  - EasyPost (https://www.easypost.com)  
    - Supports Standard UPS, FedEx & USPS shipping, FedEx third-party billing via EasyPost API & UPS 3rd Party Billing through user's UPS account
    - **Note:** UPS third-party billing is _not_ supported through EasyPost. UPS labels must be created with direct UPS API calls.
- **Self-hosted Connector**  
  - `ups_direct.py`  
    - Direct UPS integration  
    - **Add your UPS credentials** (Access Key, User ID, UPS account number) into the (top) of `ups_direct.py` config section
- **Planned / Untested**  
  - LetMeShip (https://www.letmeship.com)  
  - SendCloud (https://www.sendcloud.com)  

---

## 📦 Installation

### 1. Frappe Cloud  
Install directly from the [Frappe Cloud Marketplace](https://frappecloud.com/marketplace/apps/shipping).

### 2. Self-Hosted Bench  
```bash
# From your bench directory
bench get-app https://github.com/your-org/erpnext_shipping.git
bench --site [your-site] install-app erpnext_shipping
bench build
bench restart

⚙️ Configuration
Shipping Settings

Navigate to Home > Settings > Shipping Settings

Enter your EasyPost API key, default network printer, and any preferred services.

UPS Direct Credentials

Edit erpnext_shipping/erpnext_shipping/integrations/ups_direct.py

Populate your UPS Access Key, Username, and Password in the top-of-file configuration section.

Carrier Billing

FedEx (via EasyPost)

Can use FedEx third-party billing: configure your FedEx account details under the EasyPost credentials.

UPS

Third-party billing not available via EasyPost—must use the UPS direct connector above.

User Contact

Ensure the Pickup Contact Person on each Shipment has a first name, last name, email, and phone.

📑 Usage
Fetch Shipping Rates
Open a Shipment (submitted, but not booked).

Click Fetch Shipping Rates.

Compare rates in the dialog (preferred services appear first).

Click Buy to book that service and create the shipment.

Print Shipping Label
Print Shipping Label: Opens the carrier-provided PDF or PNG in a new tab for your local printer.
Network Print: Sends the PDF or PNG to your configured CUPS printer automatically.

🛠️ Development
Templates live under erpnext_shipping/public/templates/

Client script: erpnext_shipping/public/js/shipment.js

EasyPost integration: erpnext_shipping/erpnext_shipping/integrations/easypost.py

UPS direct integration: erpnext_shipping/erpnext_shipping/integrations/ups_direct.py

Run bench build && bench restart after making changes.

📄 License
MIT
