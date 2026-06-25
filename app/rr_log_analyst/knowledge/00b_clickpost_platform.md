# Clickpost Platform — Where It Fits in the Supply Chain

Clickpost is a multi-carrier logistics intelligence platform with direct tech integrations with 300+ carriers. Brands integrate Clickpost at multiple stages of the supply chain to replace individual carrier integrations with a single unified layer.

---

## Carrier Account Management

Before any Clickpost API can be used, carrier accounts must be onboarded and configured. This is a one-time setup per account, done via the Clickpost dashboard.

### 1. Carrier Account Onboarding / Activation

Each carrier account is created and activated under the brand's enterprise on the Clickpost dashboard.

```
Dashboard → Settings → Courier → Integrations → [Enterprise] → Add Account
```

- A carrier (CPID) can have multiple accounts — each represents a distinct service contract (e.g. surface, air, express, heavy, dark store)
- Each account gets a unique **account code** (e.g. `Delhivery_Marathahalli`) and is assigned an internal **account ID**
- Account must be set to `active = true` to be considered by the allocation engine — inactive accounts are excluded entirely from all API flows

---

### 2. Credentials Configuration

Each account stores the API credentials for that specific carrier contract. Clickpost uses these credentials when calling the carrier's API for order creation.

- Credentials are stored per account (account ID) — two accounts under the same carrier can have different credentials
- Configured via dashboard under the account settings
- The brand never exposes carrier credentials in their own API calls — they only pass CPID + account code to Clickpost, and Clickpost resolves and uses the credentials internally

---

### 3. Serviceability Upload (Required for Recommendation)

Serviceability defines which drop pincodes an account can serve, and for which order types (PREPAID, COD, etc.). This is **only relevant when using the Recommendation API** — Order Creation has no serviceability check and will attempt the order regardless.

- Must be proactively uploaded per account per order type — missing serviceability means the account is dropped in pre-filter checks and will never appear in the recommendation output
- Configured per account via dashboard:

```
Dashboard → Settings → Courier → Integrations → [Enterprise] → Account → Serviceability → Drop
```

**Two upload modes** (depends on how the brand's account is configured):

| Mode | How it works |
|------|--------------|
| **Cross-product** | Pickup pincode list uploaded separately + drop pincodes uploaded per order type — system resolves serviceability as the cross product |
| **Lane-to-lane** | Flat (pickup pincode, drop pincode) tuples uploaded per order type — explicit lane-level control |

Without serviceability data uploaded, an account will never be recommended — even if credentials are configured and the account is active.

---

## Platform Map

```
Serviceability Check  →  Serviceability APIs
EDD Display           →  EDD API
Carrier Selection     →  Recommendation API (RecsV2)
Shipment Creation     →  Order Creation API
Status Updates        →  Tracking (Registration + Webhooks / Polling)
```

---

## 1. Serviceability APIs

Checks whether a pickup–drop pincode pair is serviceable across the brand's active carrier network.

**Four API types:**

| API | Use Case |
|-----|----------|
| Pincode Carrier Serviceability | Returns all serviceable carriers for a pickup + drop pincode pair, along with shipping charges and committed SLAs |
| Pincode Serviceability | Simple check — is this drop pincode covered by any carrier? |
| Bulk Serviceability | Fetch all serviceable pickup and drop pincodes across the carrier network (by carrier + account code) |
| Fetch Drop Pincodes | Retrieve full drop pincode list for all combined carrier partners |

**Key outputs per carrier:** serviceability flag, shipping charges, committed SLA.

---

## 2. EDD API

Returns the estimated delivery date for a pickup–drop pincode pair using predictive ML algorithms.

**Required inputs:** `pickup_pincode`, `drop_pincode`
**Optional:** `cp_id` (specific carrier), `awb` (for an existing shipment)

**Key outputs:**

| Field | Description |
|-------|-------------|
| `predicted_sla_min` / `predicted_sla_max` | Min and max delivery days |
| `all_map` | SLA range per available carrier |
| `min_sla_cp_id` | Carrier with the fastest delivery |
| `pickup_date` | Scheduled pickup date |

**Formula:** Estimated delivery date = Pickup date + SLA days

**Coverage:** India, North America, Europe, Southeast Asia (both SPS and MPS).

---

## 3. Recommendation API — RecsV2

AI-driven carrier selection engine. Evaluates the order against carrier performance, business rules, and historical data to return a ranked list of carriers.

**Two versions:**

| Version | Geography |
|---------|-----------|
| V1 | India domestic |
| V2 | International — GCC, Southeast Asia, North America, Europe |

**Factors evaluated:**
- Drop pincode and serviceability
- Shipment weight
- Carrier performance history
- RTO (Return to Origin) rates and reliability
- Brand-configured rules (Filter Rules, Ordered Lists)
- Load distribution targets

**Output:** Ranked `preference_array` of carrier accounts. Each entry includes **CPID and account code** (alongside shipping charges, predicted SLA, scoring details) — these can be passed directly to the Order Creation API to create the shipment with the top-ranked carrier.

See `01_pipeline_overview.md` for the full internal pipeline (pre-filter checks → filter rules → ordered lists → PBA).

---

## 4. Order Creation API

Creates a shipment with the selected carrier partner.

**How it works:**
Brand passes **CPID + account code** in the request. Clickpost resolves the account internally, fetches that account's credentials, and calls the carrier's API to create the order.

**Important:** Order Creation has no integration with the serviceability module. The API will attempt to create the shipment with whatever CPID + account code is passed, regardless of whether the drop pincode is serviceable for that account. Serviceability filtering is handled upstream by the Recommendation flow — Order Creation trusts that the caller has already resolved a valid carrier.

**Two shipment types:**

| Type | API |
|------|-----|
| SPS (Single Piece Shipment) | Individual packages |
| MPS (Multi Piece Shipment) | Batched / multi-box shipments |

**Also supports:** Reverse orders (with QC), B2B MPS, International (V4).

**Key output:** AWB (Airway Bill) number — used for all downstream tracking and operations.

---

## 5. Tracking

### AWB Registration
AWBs must be registered with Clickpost before tracking begins:
- V2: India
- V3: Global (international + cross-border)

### Two tracking approaches

| Approach | How it works |
|----------|--------------|
| **Webhook (all-status webhook)** | Clickpost pushes status updates to the brand's endpoint in real-time as scan events arrive |
| **Polling** | Brand calls Clickpost's tracking API periodically to fetch updated order statuses |

**Best practice:** Webhooks are preferred for real-time updates; polling is a fallback or used for bulk reconciliation.

**Key data per update:** AWB, carrier, `clickpost_status_code`, status bucket, scan location, timestamp, EDD.
