# Pre-Filter Checks

## What Are Pre-Filter Checks?
Before any Filter Rule or Ordered List is evaluated, Clickpost runs a series of system-level checks to narrow down the universe of available carriers. These checks happen automatically based on account configuration, serviceability data, and historical scan events.

The output of these checks (`serviceable_account_ids`) is the set of accounts that are passed into the Filter layer.

## Position in the Pipeline (actual engine order, with `filters_ran` keys)
```
Incoming Order
      ↓
[1] Active Accounts                  → 1_active_accounts_filter
      ↓
Serviceability engine call — produces three things at once:
[1] Out-of-Threshold/Cutoff removal  → 1_accounts_out_of_threshold
[1] Blacklisted Pincode removal      → 1_blacklisted_accounts
[2] Serviceable Accounts             → 2_serviceable_accounts_filter
      ↓
[2.1] ODA removal                    → 2_1_serviceable_oda_accounts_filter
[2.2] Overall LD cap (if enabled)    → 2_2_serviceable_accounts_without_exhausted_overall_cap
[2.4] Global blacklisting service    → 2_4_global_blacklisting_serviceability_filter
      ↓
Filter Rules → Ordered List / PBA → Final Allocation
```

Note: each `filters_ran` key shows the accounts **remaining after** that check (except `1_accounts_out_of_threshold` and `1_blacklisted_accounts`, which list the **removed** accounts). Some enterprise-specific keys also appear, e.g. `1_accounts_missing_cod_or_prepaid_support`, `1_accounts_exceeding_invoice_value_limit`, `2_3_serviceable_accounts_after_enterprise_custom_key_filter` (TataCliq), `2_removed_accs_for_ndd_filter`, `2_account_removed_missing_sla` / `_transport_mode` / `_tat`.

---

## Step 1 — Check Active Accounts

### What it does
Identifies all carrier accounts that are currently active (`active=True` on `EnterpriseCourierPartnerAccountType`) for the enterprise (and matching forward/RVP type).

### System Location
```
Dashboard → Settings → Courier → Integrations → [Enterprise]
```

### Key Point
Inactive accounts are excluded from allocation entirely, regardless of any filter or OL configuration.

---

## Step 2 — Check Serviceable Accounts (includes Threshold and Blacklist)

### What it does
A single serviceability engine call evaluates the pickup-drop pincode pair and order type, and returns:
- the serviceable accounts (`2_serviceable_accounts_filter`)
- accounts removed because they are outside their configured threshold/cutoff window (`1_accounts_out_of_threshold`)
- accounts removed because the drop pincode is blacklisted for them for this order type (`1_blacklisted_accounts`)

### System Location
Serviceability is configured per account:
```
Dashboard → Settings → Courier → Integrations → [Enterprise] → Account → Serviceability → Drop
```
Thresholds:
```
Dashboard → Settings → Courier → Integrations → [Enterprise] → Account → Serviceability → Threshold Creation
```

### Key Points
- Serviceability must be **uploaded** into the system per account
- Configured per `orderType` (PREPAID, COD, etc.)
- Accounts without serviceability for the given pincode/order type are dropped before filters run
- To verify serviceability live, use `check_route_serviceability` (pickup + drop intersection) or `check_pincode_serviceability` (one side only) — see `14_serviceability_tools.md`
- Blacklisting is **brand-configured** per carrier account + pincode + order type — manual setup, not scan-driven (unlike ODA)
- A handful of enterprises skip serviceability entirely (hard-coded in `recsv2.py`)

---

## Step 2.1 — Remove ODA Accounts

### What it does
Removes accounts for which the drop pincode is classified as **Out of Delivery Area (ODA)**.

### How it works
- Gated by the `oda_filter` flag in `EnterpriseRecommendationFlowConfig` (default **off**)
- ODA pincodes are stored in `OdaPincodes` **per courier partner (CPID) + enterprise + pincode** — NOT per account
- If the drop pincode is ODA for a carrier, **all serviceable accounts of that carrier** are removed
- ODA marking is **scan-driven** — system marks a pincode ODA when a tracking scan contains an ODA event

### How a Pincode is Removed from ODA
- **As of today: manually** — requires raising a support ticket
- No automated ODA removal flow currently exists

---

## Step 2.2 — Overall Load Distribution Cap (optional)

If `load_distribution_overall` is enabled, accounts whose overall allocation cap is exhausted are removed (`2_2_serviceable_accounts_without_exhausted_overall_cap`).

---

## Step 2.4 — Global Serviceability Blacklisting (optional)

If the enterprise has the GLOBAL_SERVICEABILITY_BLACKLISTING service enabled, accounts with `global_serviceability_blacklisting_enabled=True` are checked against a global (Clickpost-wide) blacklist of drop pincode + order type per carrier and removed if blacklisted (`2_4_global_blacklisting_serviceability_filter`).

---

## Summary Table

| Check | What Gets Removed | `filters_ran` key | Configured Where |
|------|-------------------|-------------------|-----------------|
| Active Accounts | Inactive carrier accounts | `1_active_accounts_filter` (remaining) | Carrier integrations page |
| Out-of-Threshold | Accounts outside configured time/cutoff thresholds | `1_accounts_out_of_threshold` (removed) | Account serviceability → Threshold creation |
| Blacklisted Pincode | Accounts where drop pincode is blacklisted by brand for the order type | `1_blacklisted_accounts` (removed) | Brand-configured blacklist per account |
| Serviceable Accounts | Accounts without serviceability for the route/order type | `2_serviceable_accounts_filter` (remaining) | Account serviceability → Drop pincode |
| ODA | All accounts of carriers where drop pincode is marked ODA | `2_1_serviceable_oda_accounts_filter` (remaining) | `oda_filter` flow flag + tracking scans |
| Overall LD cap | Accounts with exhausted overall allocation cap | `2_2_...` (remaining) | Load distribution config |
| Global blacklisting | Accounts blacklisted in global serviceability data | `2_4_...` (remaining) | Clickpost global service + account config |

---

## Key Notes

1. All these checks happen **before** any Filter Rule is evaluated
2. Their output = the universe of accounts that enter the Filter layer (`serviceable_account_ids`)
3. Even if an account is in a Filter's `account_list`, it will not be allocated if it was removed in pre-filter checks
4. ODA is **per carrier (CPID)**, not per account — and only runs when `oda_filter` is enabled
5. Serviceability must be proactively uploaded per account — missing serviceability = account dropped
