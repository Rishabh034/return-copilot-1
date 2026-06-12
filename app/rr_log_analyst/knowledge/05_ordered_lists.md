# Ordered List Rules

## What is an Ordered List Rule?
An Ordered List (OL) rule is the second rule-evaluation layer in Clickpost's carrier allocation engine. It defines the **priority order** of carriers, typically for a specific route. The final carrier allocation is the intersection of the OL's carrier list with the upstream eligible pool, preserved in OL's priority order.

Stored in `RecommendationInclusionFilters` with `rule_type='ORDERED_LIST'`. Runs only if the `ordered_list` flag is enabled in the enterprise's recommendation flow config.

## Role in the Pipeline
```
Pre-filter checks (serviceable accounts)
        ↓
  Filter Rules
        ↓
  upstream pool (matched filter's pool, or all serviceable accounts if no filter matched)
        ↓
  Ordered List       ← THIS LAYER
        ↓
  Final Allocation (or PBA if OL produced nothing)
```

## Key Behaviours
- Conditions within a rule are evaluated with **AND logic** — every condition must pass
- Rules are evaluated in **priority order** — lowest number first
- **First matching rule wins** — evaluation stops, remaining rules are skipped (unless rule iteration is enabled and the matched rule has no serviceable accounts)
- `account_list` is an **ordered priority list** — P1 (index 0) is highest priority
- Final allocation = OL carriers filtered to only those present in the upstream pool, in OL's order
- If a carrier is in OL but NOT in the upstream pool → dropped from final allocation
- **OL does not require a filter match.** If no filter matched, the upstream pool is all serviceable accounts, and the OL intersects with that.
- If OL matches but the intersection is **empty** → the engine falls through to **PBA** over the upstream pool (when `return_serviceable_accounts` is enabled, which is the default). It is NOT a guaranteed "no allocation". (Enterprise exceptions exist, e.g. TataCliq returns empty in that case.)
- If **no OL matches** → upstream pool goes to PBA (`4_ordered_list_account_names_filters_applied: false` in the RR log)

## Intersection Logic
```python
final_carriers = [c for c in OL.account_list if c in upstream_pool]
```
OL's priority order is preserved. The upstream pool (filter result ∩ serviceable accounts) acts as the eligibility gate.

---

## Top-Level Fields

| Field | Type | Create | Update | Description |
|-------|------|--------|--------|-------------|
| `rule_type` | string | `"ORDERED_LIST"` | `"ORDERED_LIST"` | Always `"ORDERED_LIST"` |
| `name` | string | required | required | Human-readable rule name |
| `priority` | integer | required | required | Evaluation order. Lower = first. Keep unique (dashboard convention; not DB-enforced) |
| `enabled` | boolean | required | required | `true` = active, `false` = skipped |
| `id` | integer / null | `null` | `<rule_id>` | null for create, existing ID for update |
| `updated_at` | string | `""` | `"<ISO 8601>"` | empty for create, timestamp for update |
| `account_list` | integer[] | required | required | Carriers in **priority order** — P1 first, P2 second... |
| `allocation_type` | string | `"PERCENTAGE"` | `"PERCENTAGE"` | `"PERCENTAGE"` or `"ABSOLUTE_CAPPING"`; `"PERCENTAGE"` is the norm |
| `load_distribution_filter_enabled` | boolean | `false` | `false` | false unless load distribution configured |
| `rule` | object[] | required | required | Array of condition objects (AND logic) |

---

## Condition Object (`rule[]`)

Identical structure, field list, and operators to Filter condition objects — see `04_filter_rules.md` for:
- the full 35-field `CLICKPOST_DEFINED` table (source: `ClickpostDefinedFilterFieldEnum`)
- `ENTERPRISE_CUSTOM` and `CLUSTER_FIELDS` condition rule types
- the operator table (`EQ`, `NEG`, `IN`, `CONTAINS`, `GTE`, `GT`, `LTE`, `LT`, `BETWEEN`; no `NOT IN`; strings case-insensitive)

| Field | Type | Description |
|-------|------|-------------|
| `rule_type` | string | `"CLICKPOST_DEFINED"`, `"ENTERPRISE_CUSTOM"`, or `"CLUSTER_FIELDS"` |
| `key` | string | The field being evaluated |
| `op` | string | The operator |
| `value` | string | Value to compare against — always a string even for numeric types |
| `value_type` | string | `"string"`, `"float"`, `"integer"`, or `"timeHHMM"` |
| `field_name` | null | Legacy field — always `null` |
| `field_type` | string | Legacy field — always `""` |
| `field_value` | null | Legacy field — always `null` |

---

## Priority

- Should be **unique** across all OL rules for a customer (dashboard convention — the DB does not enforce it)
- Evaluation is strictly sequential: Priority 1 → 2 → 3 → ...
- **Lower number = evaluated first**
- First rule where ALL conditions pass wins; rest are not evaluated

---

## Difference from Filter `account_list`

| | Filter `account_list` | OL `account_list` |
|-|----------------------|-------------------|
| Meaning | Eligible carrier pool | Priority order (P1→PN) |
| Order matters? | No — when no OL matches, PBA re-ranks the pool by scores | Always — defines P1, P2, P3... |
| Used for | Gating which carriers can be used | Ordering the eligible carriers |

---

## Important Notes

1. OL **can** fire without a filter match — it then intersects with all serviceable accounts
2. Carriers in OL not present in the upstream pool are **silently dropped**
3. If the intersection is empty → falls through to PBA over the upstream pool (default behavior); not necessarily "no allocation"
4. `value` is always sent as a **string** regardless of `value_type`
5. `field_name`, `field_type`, `field_value` are legacy — always send `null`, `""`, `null`
6. Most common OL conditions are `pickup_pincode IN` + `drop_pincode IN` for route-based routing
7. RR log keys: `order_list_matched_rule` (matched rule name), `4_ordered_list_rules_list` (matched rules with `sequence`/`has_serviceable_accounts`), `4_ordered_list_account_names_filters_applied` (whether an OL matched), `ordered_list_filter_accounts` (final intersection)
