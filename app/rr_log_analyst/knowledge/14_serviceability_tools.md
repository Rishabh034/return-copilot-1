## Serviceability Tools

Two tools query the Clickpost pincode serviceability API (`/api/v1/pincode_search_overall`). They answer whether carrier accounts can serve a pincode or a pickup→drop route — the same data that feeds pre-filter step `2_serviceable_accounts_filter`.

**Do not use `run_recommendation` for serviceability questions.** If the user asks for serviceable accounts on a pickup→drop pair (or whether a pincode/account is serviceable), call `check_route_serviceability` or `check_pincode_serviceability` directly. Recommendation simulation is for full allocation walkthroughs only.

### Tools

| Tool | When to use | API calls |
|------|-------------|-----------|
| `check_pincode_serviceability` | One pincode only — pickup **or** drop | 1 |
| `check_route_serviceability` | Full route — account must be serviceable at **both** ends | 2 (pickup + drop), intersected client-side |

### Inputs (both tools)

| Param | Required | Notes |
|-------|----------|-------|
| `pincode` + `is_pickup_pincode` | Single-pincode tool only | `is_pickup_pincode=true` → pickup list; `false` → drop list |
| `pickup_pincode` + `drop_pincode` | Route tool only | Account must be on pickup; keep all drop `(account_id, order_type)` rows for that account |
| `is_rvp` | No (default `false`) | `true` for reverse (RVP) orders |
| `order_type` | No | Filter to PREPAID or COD — match the order under investigation |
| `account_type_id` | No | **Preferred** account filter — use the numeric ID from RR logs / `get_account_info` |
| `account_name` | No | Substring match; avoid alone when names are generic (`"1"`, `"test"`) |
| `courier_partner` | No | Substring match on carrier brand name |

### Output shape

Both tools return shaped rows with `account_id`, `account_name`, `courier_partner`, `order_type`. The API uses different field names per side — drop rows use `pincode_serviceability__order_type`, pickup rows use `pickup_pincode_serviceability__order_type`. The tools normalize both to uppercase `order_type`.

Route tool also returns `pickup_count`, `drop_count`. When filters are applied, `filters_applied` and `serviceable` (boolean) are included.

**Intersection rule (cross-product):** pickup is an **account-id set** (pickup often only returns PREPAID). For every account present on pickup **and** drop, keep **all drop order_types**. Example: pickup has `1938:PREPAID`, drop has `1938:PREPAID` + `1938:COD` → both PREPAID and COD appear in the result.

---

## Question → Tool Mapping

| User question | Tool | Typical args |
|---------------|------|--------------|
| "Is Delhivery serviceable for 560001 → 110001?" | `check_route_serviceability` | `pickup_pincode`, `drop_pincode`, `courier_partner=Delhivery` |
| "Why isn't account 193087 in the serviceable list?" | `check_route_serviceability` | pincodes from RR log `user_request`, `account_type_id=193087`, `order_type` from order |
| "Is 110001 a valid drop for this account?" | `check_pincode_serviceability` | `pincode=110001`, `is_pickup_pincode=false`, `account_type_id` |
| "Is pickup pincode 560001 configured?" | `check_pincode_serviceability` | `pincode=560001`, `is_pickup_pincode=true` |
| "Which accounts can serve this route?" / "all serviceable accounts for pickup X drop Y" | `check_route_serviceability` | `pickup_pincode`, `drop_pincode` — **not** `run_recommendation` |
| "Account dropped at serviceability — is data missing?" | `check_route_serviceability` | Filter to the dropped `account_id`; if `serviceable: false`, serviceability upload is missing or pincode not covered |

---

## Reconciling with RR Logs

When explaining why an account was removed at `2_serviceable_accounts_filter`:

1. **Fetch the RR log** (`fetch_rr_log`) — read `user_request` for `pickup_pincode`, `drop_pincode`, `order_type`, `delivery_type`.
2. **Resolve account ID** — from `filters_ran` account strings like `Delhivery_Maratahalli(Delhivery(1005979))` or via `get_account_info`.
3. **Call `check_route_serviceability`** with those pincodes, `is_rvp=(delivery_type == "RVP")`, `order_type`, and `account_type_id`.
4. **Compare:**
   - `serviceable: true` but account absent from `2_serviceable_accounts_filter` → look at threshold (`1_accounts_out_of_threshold`) or blacklist (`1_blacklisted_accounts`) in the same RR log — serviceability passed but a sibling pre-filter removed it.
   - `serviceable: false` → account lacks uploaded serviceability for that pincode/order type, or only one side of the route is covered (use single-pincode tool on each end to isolate pickup vs drop gap).
   - Account in tool result but not in RR log serviceable list → enterprise may skip serviceability (hard-coded exceptions) or the log is from a different order type.

---

## Dashboard location

Per-account serviceability is configured at:

```
Dashboard → Settings → Courier → Integrations → [CPID] → Account → Serviceability → Drop
```

Pickup pincode lists (cross-product mode):

```
Dashboard → Settings → Courier → Integrations → [CPID] → Account → Serviceability → Pickup
```

When recommending a fix, cite the specific account ID and whether pickup, drop, or both need an upload.
