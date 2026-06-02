# Pre-Filter Checks

## What Are Pre-Filter Checks?
Before any Filter Rule or Ordered List is evaluated, Clickpost runs a series of system-level checks to narrow down the universe of available carriers. These checks happen automatically based on account configuration, serviceability data, and historical scan events.

The output of these checks is the set of accounts that are passed into the Filter layer.

## Position in the Pipeline
```
Incoming Order
      ↓
[1] Check Active Accounts          ← pre-filter check
      ↓
[2] Check Serviceable Accounts     ← pre-filter check
      ↓
[3] Remove Out-of-Threshold        ← pre-filter check
      ↓
[4] Remove ODA Pinched Accounts    ← pre-filter check
      ↓
[5] Remove Blacklisted Pincode Accounts  ← pre-filter check
      ↓
Filter Rules
      ↓
Ordered List
      ↓
Final Allocation
```

---

## Step 1 — Check Active Accounts

### What it does
Identifies all carrier accounts that are currently active and available for processing shipments.

### Example
For a given enterprise, only accounts that are active under their carrier partner integrations are considered.

### System Location
```
Dashboard → Settings → Courier → Integrations → [Enterprise]
https://dashboard.clickpost.ai/settings/courier/integrations/<enterprise_id>
```

### Key Point
Inactive accounts are excluded from allocation entirely, regardless of any filter or OL configuration.

---

## Step 2 — Check Serviceable Accounts

### What it does
From the active accounts, removes those that cannot service the given pickup-drop pincode combination or order type.

### Example
For a shipment from pincode `560037` to `302015`, only courier partners that have serviceability configured for this route are kept.

### System Location
Serviceability is configured per account:
```
Dashboard → Settings → Courier → Integrations → [Enterprise] → Account → Serviceability → Drop
https://dashboard.clickpost.ai/settings/courier/integrations/<enterprise_id>/account/<account_id>/serviceability/drop?orderType=PREPAID
```

### Key Point
- Serviceability must be **uploaded** into the system per account
- Can be configured per `orderType` (PREPAID, COD, etc.)
- Accounts without serviceability for the given pincode/order type are dropped before filters run

---

## Step 3 — Remove Out-of-Threshold Accounts

### What it does
Removes accounts that do not meet predefined threshold criteria set by the enterprise.

### Example
An enterprise can configure a time-range threshold for a carrier — e.g. a carrier is only available between certain hours. Orders placed outside that window will have that carrier removed.

### System Location
Threshold configuration is per account in the serviceability section:
```
Dashboard → Settings → Courier → Integrations → [Enterprise] → Account → Serviceability → Threshold Creation
https://dashboard.clickpost.ai/settings/courier/integrations/<enterprise_id>/account/<account_id>/serviceability/threshold-creation
```

### Key Point
- Thresholds are time/range based conditions
- Set per account by the enterprise
- Accounts failing threshold checks are removed before filters run

---

## Step 4 — Remove ODA Pinched Accounts

### What it does
Removes accounts for which the drop pincode is classified as **Out of Delivery Area (ODA)**. ODA pincodes are locations where delivery is difficult or restricted for a specific carrier.

### How Clickpost Identifies ODA
- ODA must be **enabled** in the allocation rule settings:
  ```
  https://dashboard.clickpost.ai/recommendation/allocation-rule/oda
  ```
- Once enabled, Clickpost monitors shipment tracking scans
- If a tracking scan for a shipment contains an ODA event → the system **marks that pincode as ODA** for that carrier account

### How a Pincode is Removed from ODA
- **As of today: manually** — requires raising a support ticket
- No automated ODA removal flow currently exists

### Key Point
- ODA marking is **scan-driven** — it happens automatically from tracking events once ODA is enabled
- ODA is **per carrier account + pincode** combination — one carrier may mark a pincode ODA while another doesn't
- Accounts with ODA pincodes matching the drop pincode are removed before filters run

---

---

## Step 5 — Remove Blacklisted Pincode Accounts

### What it does
Removes accounts for which the drop pincode in the order payload is explicitly blacklisted by the brand against that carrier account.

### How it works
- Brands configure a list of pincodes as **blacklisted** against specific carrier partner accounts
- When an order's drop pincode matches a blacklisted pincode for an account → that account is removed before filters run

### Key Point
- Blacklisting is **brand-configured** — manual setup, not scan-driven (unlike ODA)
- Blacklist is **per carrier account + pincode** combination
- Applied at the drop pincode level

---

## Summary Table

| Step | What Gets Removed | Configured Where |
|------|-------------------|-----------------|
| 1. Active Accounts | Inactive carrier accounts | Carrier integrations page |
| 2. Serviceable Accounts | Accounts without serviceability for the route/order type | Account serviceability → Drop pincode |
| 3. Out-of-Threshold | Accounts outside configured time/range thresholds | Account serviceability → Threshold creation |
| 4. ODA Pinched | Accounts where drop pincode is marked ODA | Allocation rule → ODA settings + tracking scans |
| 5. Blacklisted Pincode | Accounts where drop pincode is blacklisted by brand | Brand-configured blacklist per carrier account |

---

## Key Notes

1. All 5 checks happen **before** any Filter Rule is evaluated
2. The output of these 5 checks = the universe of accounts that enter the Filter layer
3. Even if an account is in a Filter's `account_list`, it will not be allocated if it was removed in pre-filter checks
4. ODA removal requires a support ticket today — no self-serve removal
5. Serviceability must be proactively uploaded per account — missing serviceability = account dropped
