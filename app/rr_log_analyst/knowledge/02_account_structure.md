# Clickpost Account Structure

## Hierarchy Overview

```
CPID (Carrier Partner)
├── Carrier Name        ← human-readable name stored against the CPID
└── Accounts
    ├── Account 1       ← e.g. Delhivery_Maratahalli
    ├── Account 2       ← e.g. Delhivery_Bangalore_air
    ├── Account 3       ← e.g. Delhivery_Bangalore_Surface
    └── Account 4       ← e.g. Delhivery_BLRH
```

---

## Identifiers

| Identifier | Type | Description | Example |
|------------|------|-------------|---------|
| **CPID** | Integer | Clickpost's internal ID for a carrier | `4` |
| **Carrier Name** | String | Human-readable name stored against the CPID | `Delhivery` |
| **Account Code** | String | Brand's specific contract/account with that carrier | `Delhivery_Maratahalli` |
| **Account ID** | Integer | Clickpost's internal ID for that specific account | `1005979` |

### How they appear together
Format seen in serviceable carrier responses:
```
{Account Code}({Carrier Name}({Account ID}))

e.g. Delhivery_Maratahalli(Delhivery(1005979))
```

---

## Key Principles

### Two layers of account identity

| Layer | Identifier | Where it's used |
|-------|------------|-----------------|
| **External (API-facing)** | CPID + Account Code | Order Creation API, Recommendation `preference_array` — these are the identifiers brands pass and receive |
| **Internal (Clickpost system)** | Account ID | All settings storage — credentials, serviceability, pricing, SLA, filter/OL rules — are keyed on Account ID |

The Account Code is the human-readable contract name (e.g. `Delhivery_Maratahalli`). The Account ID is Clickpost's internal integer key for that same account. Externally you reference an account by its code; internally everything is resolved to its ID.

### Clickpost operates at Account level, not CPID level
- CPID is the carrier grouping; rules, serviceability, blacklists, and allocation decisions are all at the **Account** level
- **Exception:** ODA is stored per CPID — an ODA pincode removes all accounts under that carrier
- Saying "use Delhivery" is not enough — a specific account (CPID + account code) must be resolved

### One CPID → Multiple Accounts
A carrier (CPID) can offer multiple services, each represented as a separate account:
```
CPID: Delhivery
├── Delhivery_Maratahalli     (Account ID: 1005979) ← local dark store
├── Delhivery_Bangalore_air   (Account ID: XXXXXX)  ← air express
├── Delhivery_Bangalore_Surface (Account ID: XXXXXX) ← surface
└── Delhivery_BLRH            (Account ID: XXXXXX)  ← heavy shipments
```

Each account represents a different service — different speed, weight class, pickup zone, or pricing contract.

---

## Per-Account Configuration

Everything is configured independently per account:

```
Account (Account ID)
├── Serviceability
│   ├── Drop Pincodes      ← which pincodes it can serve, per order type
│   ├── Thresholds         ← time/range windows the account is available
│   └── ODA                ← pincodes marked as Out of Delivery Area
├── Pricing                ← rate card for that specific service/contract
└── SLA                    ← promised delivery timelines
```

### Serviceability
- Configured per account per order type (PREPAID, COD)
- Must be proactively uploaded — missing serviceability = account dropped in pre-filter checks
- Dashboard:
  ```
  Settings → Courier → Integrations → [Enterprise] → Account → Serviceability → Drop
  https://dashboard.clickpost.ai/settings/courier/integrations/<cpid>/account/<account_id>/serviceability/drop?orderType=PREPAID
  ```

#### Thresholds (sub-config of Serviceability)
- Time/range windows during which the account is available
- Orders outside the threshold window have this account removed in pre-filter checks
- Dashboard:
  ```
  Settings → Courier → Integrations → [Enterprise] → Account → Serviceability → Threshold Creation
  https://dashboard.clickpost.ai/settings/courier/integrations/<cpid>/account/<account_id>/serviceability/threshold-creation
  ```

#### ODA — Out of Delivery Area
- Pincodes where delivery is difficult/restricted for a carrier
- ODA is scan-driven — system marks a pincode ODA when a tracking scan contains an ODA event
- **Stored per courier partner (CPID) + enterprise + pincode** (`OdaPincodes` model has `courier_partner_id`, not account_id). When a drop pincode is ODA for a carrier, **all accounts of that carrier** are removed — this is the one engine check that operates at CPID level
- ODA removal in the engine only runs if `oda_filter` is enabled in the enterprise's recommendation flow config (default off); enable via `https://dashboard.clickpost.ai/recommendation/allocation-rule/oda`
- Removal of a pincode from ODA: manual via support ticket (no self-serve flow today)

### Pricing
- Rate card specific to the brand's contract with this account
- Independent per account — two accounts under same CPID can have different pricing

### SLA
- Promised delivery timelines for this account
- Independent per account

---

## How Identifiers Are Used Across APIs

### Filter and Ordered List rules (internal)
The `account_list` in Filter and Ordered List rules uses **Account IDs** (integers):

```json
{
  "account_list": [1006980, 817, 187852]
}
```

These are Account IDs, not CPIDs. Each integer maps to one specific carrier account.

### Recommendation API — output
The `preference_array` returned by RecsV2 includes both **CPID and account code** for each ranked carrier:

```json
{
  "cp_id": 4,
  "account_code": "Delhivery_Maratahalli",
  "account_id": 1005979,
  ...
}
```

The brand uses `cp_id` + `account_code` from this response directly in the Order Creation API.

### Order Creation API — input
Takes **CPID + account code** to identify the carrier account. Clickpost resolves the account internally (by account ID) to fetch credentials and hits the carrier's API with those credentials.

**Important:** Order Creation has no integration with the serviceability module. Passing any valid CPID + account code will attempt order creation with that carrier regardless of whether the drop pincode is serviceable for that account. Serviceability filtering is the responsibility of the Recommendation flow.

---

## Dashboard URL Pattern

```
https://dashboard.clickpost.ai/settings/courier/integrations/<cpid>/account/<account_id>/<feature>/
```

- `<cpid>` = Carrier Partner ID (e.g. `4`)
- `<account_id>` = Account ID (e.g. `300774`)

---

## Summary

| Level | Identifier | Used Where |
|-------|------------|------------|
| Carrier | CPID | Carrier grouping; ODA marking; API input alongside account code |
| Account (external) | Account Code | Order Creation API input; Recommendation `preference_array` output |
| Account (internal) | Account ID | Credentials, serviceability, pricing, SLA storage; Filter/OL `account_list` |
