# Serviceability

Same data as engine step `2_serviceable_accounts_filter`. **Never use `run_recommendation` for serviceability-only questions** — that runs full allocation.

Tool: `check_route_serviceability` — pickup only, drop only, or both (route = client-side intersection). Response `mode`: `pickup_only` | `drop_only` | `route`.

| Param | Notes |
|-------|-------|
| `pickup_pincode` and/or `drop_pincode` | At least one |
| `is_rvp` | default false; true for reverse |
| `order_type` | PREPAID / COD — match the order |
| `account_type_id` | Preferred account filter |
| `account_name` / `courier_partner` | Substring; avoid generic names alone |

Output rows: `account_id`, `account_name`, `courier_partner`, `order_type` (normalised). Route mode adds `pickup_count`/`drop_count`; with filters, `filters_applied` + `serviceable`.

**Route intersection:** pickup is an account-id set (often PREPAID only). For accounts on both sides, keep **all drop order_types**. Example: pickup `1938:PREPAID`, drop PREPAID+COD → both in result.

### Reconcile with allocation log (`2_serviceable_accounts_filter`)
1. From log `user_request`: pincodes, `order_type`, `delivery_type`.
2. Resolve account ID; call `check_route_serviceability` with `is_rvp=(delivery_type=="RVP")`.
3. Compare: `serviceable:true` but absent from log → check threshold/blacklist siblings; `serviceable:false` → missing upload or one side of route (retry pickup-only / drop-only); in tool but not log → enterprise may skip serviceability, or wrong order type.

Cite account ID and whether pickup, drop, or both need upload when recommending a fix.
