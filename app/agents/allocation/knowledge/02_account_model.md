# Account Model

One **CPID** (carrier) → many **accounts** (distinct contracts: speed, weight class, pickup zone, pricing). Example: Delhivery → `Delhivery_Maratahalli`, `Delhivery_Bangalore_air`, …

| Identifier | Meaning | Example |
|------------|---------|---------|
| CPID | Carrier ID | `4` |
| `cp_name` | Carrier name | `Delhivery` |
| Account Code | Brand contract name | `Delhivery_Maratahalli` |
| Account ID | Internal account ID | `1005979` |

Log / serviceable-string format: `AccountCode(Carrier(account_id))` e.g. `Delhivery_Maratahalli(Delhivery(1005979))`.

| Layer | ID | Used for |
|-------|----|----------|
| External | CPID + Account Code | Order Creation input; `preference_array` output |
| Internal | Account ID | Credentials, serviceability, pricing, SLA, every rule `account_list` |

Filter/OL `account_list` is always Account IDs, never CPIDs. Rules, serviceability, blacklists, and allocation run at **Account** level — "use Delhivery" is never enough. **CPID-scoped exceptions:** ODA and Attribute Layer.

Per-account config: drop serviceability (per order type), thresholds/cutoffs, ODA (stored per CPID), pricing, SLA. Serviceability must be uploaded per account/order type; missing upload = pre-filter drop.

Dashboard base: `https://dashboard.clickpost.ai/settings/courier/integrations/<cpid>/account/<account_id>/`
- Drop serviceability: `…/serviceability/drop?orderType=PREPAID`
- Pickup (cross-product): `…/serviceability/pickup`
- Thresholds: `…/serviceability/threshold-creation`
- ODA: `https://dashboard.clickpost.ai/recommendation/allocation-rule/oda`
- Attribute rules: `Dashboard → Filters → Carrier attribute rules → select CP_ID`
