# Filter Rules

## What is a Filter Rule?
A Filter Rule is the first rule-evaluation layer in Clickpost's carrier allocation engine. It acts as an eligibility gate — it evaluates conditions on an incoming order and if ALL conditions pass, it outputs a pool of eligible carriers (its `account_list` intersected with the serviceable accounts) to the next layer.

Stored in `RecommendationInclusionFilters` (selector app). The engine loads rows with `rule_type IN ('CLICKPOST_DEFINED', 'ENTERPRISE_CUSTOM', 'FILTER')` and `enabled=True`, ordered by `priority`. Runs only if the `filters` flag is enabled in the enterprise's recommendation flow config.
A Filter Rule is the first rule-evaluation layer in Clickpost's carrier allocation engine. It acts as an eligibility gate — it evaluates conditions on an incoming order and if ALL conditions pass, it outputs a pool of eligible carriers (its `account_list` intersected with the serviceable accounts) to the next layer.

Stored in `RecommendationInclusionFilters` (selector app). The engine loads rows with `rule_type IN ('CLICKPOST_DEFINED', 'ENTERPRISE_CUSTOM', 'FILTER')` and `enabled=True`, ordered by `priority`. Runs only if the `filters` flag is enabled in the enterprise's recommendation flow config.

## Role in the Pipeline
```
Pre-filter checks (serviceable accounts)
Pre-filter checks (serviceable accounts)
        ↓
  Filter Rules       ← THIS LAYER
        ↓
  Ordered List  /  PBA
  Ordered List  /  PBA
        ↓
  Final Allocation
```

## Key Behaviours
test_data['order_shipped_date'].min(),test_data['order_shipped_date'].max())
- All conditions within a rule are evaluated with **AND logic** — every condition must pass
- Rules are evaluated in **priority order** — lowest number first
- **First matching rule wins** — evaluation stops, remaining rules are skipped (unless rule iteration is enabled and the matched rule has no serviceable accounts — see `07_rule_iteration.md`)
- The matched filter's pool = `account_list` ∩ serviceable accounts (order of `account_list` preserved)
- If a filter matches but no OL matches → the filter's pool goes to **PBA**, which re-ranks it by performance scores (see `06_pba.md`). The `account_list` order is NOT the final order in that case.
- If **no filter matches** → the upstream pool defaults to **all serviceable accounts**, which then go to OL/PBA. (Exception: a few enterprises — e.g. GlobalBees, Savana, Urbanic — are hard-coded to return an empty preference array when neither filter nor OL matched.)
- If a filter matches but its pool has no serviceable accounts (`has_serviceable_accounts: false` in `3_clickpost_filters_rules_list`) → `priority_list_account_names` is **empty** and the upstream pool passed to OL is `[]`. See `08_fl_ol_intersection_fallback.md`.

---

## Top-Level Fields

| Field | Type | Create | Update | Description |
|-------|------|--------|--------|-------------|
| `rule_type` | string | `"FILTER"` | `"FILTER"` | `"FILTER"` for new rules; legacy rows may have `"CLICKPOST_DEFINED"` or `"ENTERPRISE_CUSTOM"` at top level — the engine treats all three as filter rules |
| `rule_type` | string | `"FILTER"` | `"FILTER"` | `"FILTER"` for new rules; legacy rows may have `"CLICKPOST_DEFINED"` or `"ENTERPRISE_CUSTOM"` at top level — the engine treats all three as filter rules |
| `name` | string | required | required | Human-readable rule name |
| `priority` | integer | required | required | Evaluation order. Lower = first. Keep unique (dashboard convention; not DB-enforced) |
| `priority` | integer | required | required | Evaluation order. Lower = first. Keep unique (dashboard convention; not DB-enforced) |
| `enabled` | boolean | required | required | `true` = active, `false` = skipped |
| `id` | integer / null | `null` | `<rule_id>` | null for create, existing ID for update |
| `updated_at` | string | `""` | `"<ISO 8601>"` | empty for create, timestamp for update |
| `account_list` | integer[] | required | required | Carrier account IDs eligible if rule matches |
| `allocation_type` | string | `"PERCENTAGE"` | `"PERCENTAGE"` | `"PERCENTAGE"` or `"ABSOLUTE_CAPPING"` (load distribution); `"PERCENTAGE"` is the norm |
| `account_list` | integer[] | required | required | Carrier account IDs eligible if rule matches |
| `allocation_type` | string | `"PERCENTAGE"` | `"PERCENTAGE"` | `"PERCENTAGE"` or `"ABSOLUTE_CAPPING"` (load distribution); `"PERCENTAGE"` is the norm |
| `load_distribution_filter_enabled` | boolean | `false` | `false` | false unless load distribution configured |
| `rule` | object[] | required | required | Array of condition objects (AND logic) |

---

## Condition Object (`rule[]`)

Each object is one condition. All must be true (AND) for the rule to fire.

| Field | Type | Description |
|-------|------|-------------|
| `rule_type` | string | `"CLICKPOST_DEFINED"`, `"ENTERPRISE_CUSTOM"`, or `"CLUSTER_FIELDS"` (rare — matches against precomputed cluster data) |
| `rule_type` | string | `"CLICKPOST_DEFINED"`, `"ENTERPRISE_CUSTOM"`, or `"CLUSTER_FIELDS"` (rare — matches against precomputed cluster data) |
| `key` | string | The field being evaluated |
| `op` | string | The operator |
| `value` | string | Value to compare against — always a string even for numeric types |
| `value_type` | string | `"string"`, `"float"`, `"integer"`, or `"timeHHMM"` |
| `value_type` | string | `"string"`, `"float"`, `"integer"`, or `"timeHHMM"` |
| `field_name` | null | Legacy field — always `null` |
| `field_type` | string | Legacy field — always `""` |
| `field_value` | null | Legacy field — always `null` |

---

## Condition Rule Types

### `CLICKPOST_DEFINED`
Fields that Clickpost extracts and understands natively from every order API call. No custom setup needed. Source of truth: `selector/utils/clickpost_defined_filter_enum.py` (`ClickpostDefinedFilterFieldEnum`). 35 fields total.
Fields that Clickpost extracts and understands natively from every order API call. No custom setup needed. Source of truth: `selector/utils/clickpost_defined_filter_enum.py` (`ClickpostDefinedFilterFieldEnum`). 35 fields total.

| API Key | Display Name | Value Type | Computed? |
|---------|--------------|-----------|-----------|
| `item` | Items | string | |
| `reference_number` | ReferenceNumber | string | |
| `order_type` | Payment Mode | string | |
| `delivery_type` | Delivery Type | string | |
| `invoice_value` | Invoice Value | float | |
| `pickup_pincode` | Pickup Pincode | string | |
| `drop_pincode` | Drop Pincode | string | |
| `weight` | Dry Weight | float | |
| `volumetric_weight` | Volumetric Weight | float | yes — computed from L×B×H ÷ per-account volumetric coefficient (default 5); evaluated per account, pruning the rule's `account_list` |
| `higher_of_dry_weight_and_volumetric_weight` | Higher Of Dry Weight And Volumetric Weight | float | yes |
| `pickup_city` | Pickup City | string | |
| `drop_city` | Drop City | string | |
| `drop_postal_code` | Drop Postal Code | string | |
| `pickup_postal_code` | Pickup Postal Code | string | |
| `pickup_country_code` | Pickup Country Code | string | |
| `drop_country_code` | Drop Country Code | string | |
| `length` | Length | float | |
| `height` | Height | float | |
| `breadth` | Breadth | float | |
| `pickup_city_postal_code` | Pickup City Postal Code | string | |
| `drop_city_postal_code` | Drop City Postal Code | string | |
| `is_restricted` | Is Restricted | string | |
| `fareye_store_id` | Farye Store ID | string | |
| `enterprise_store_id` | Enterprise Store ID | string | |
| `pickup_address` | Pickup Address | string | |
| `drop_address` | Drop Address | string | |
| `zone` | Zone | string | special — matched per account against the serviceability zone of each account, pruning the rule's `account_list` |
| `pickup_name` | Pickup Name | string | |
| `item_count` | Item Count | integer | |
| `pickup_location_code` | Pickup Location Code | string | |
| `drop_location_code` | Drop Location Code | string | |
| `pickup_state` | Pickup State | string | |
| `drop_state` | Drop State | string | |
| `box_code` | Box Code | string | |
| `shipment_created_datetime_utc` | Shipment Time (UTC) | timeHHMM | typically used with `BETWEEN`, value `"HH:MM,HH:MM"` (supports overnight ranges) |
| API Key | Display Name | Value Type | Computed? |
|---------|--------------|-----------|-----------|
| `item` | Items | string | |
| `reference_number` | ReferenceNumber | string | |
| `order_type` | Payment Mode | string | |
| `delivery_type` | Delivery Type | string | |
| `invoice_value` | Invoice Value | float | |
| `pickup_pincode` | Pickup Pincode | string | |
| `drop_pincode` | Drop Pincode | string | |
| `weight` | Dry Weight | float | |
| `volumetric_weight` | Volumetric Weight | float | yes — computed from L×B×H ÷ per-account volumetric coefficient (default 5); evaluated per account, pruning the rule's `account_list` |
| `higher_of_dry_weight_and_volumetric_weight` | Higher Of Dry Weight And Volumetric Weight | float | yes |
| `pickup_city` | Pickup City | string | |
| `drop_city` | Drop City | string | |
| `drop_postal_code` | Drop Postal Code | string | |
| `pickup_postal_code` | Pickup Postal Code | string | |
| `pickup_country_code` | Pickup Country Code | string | |
| `drop_country_code` | Drop Country Code | string | |
| `length` | Length | float | |
| `height` | Height | float | |
| `breadth` | Breadth | float | |
| `pickup_city_postal_code` | Pickup City Postal Code | string | |
| `drop_city_postal_code` | Drop City Postal Code | string | |
| `is_restricted` | Is Restricted | string | |
| `fareye_store_id` | Farye Store ID | string | |
| `enterprise_store_id` | Enterprise Store ID | string | |
| `pickup_address` | Pickup Address | string | |
| `drop_address` | Drop Address | string | |
| `zone` | Zone | string | special — matched per account against the serviceability zone of each account, pruning the rule's `account_list` |
| `pickup_name` | Pickup Name | string | |
| `item_count` | Item Count | integer | |
| `pickup_location_code` | Pickup Location Code | string | |
| `drop_location_code` | Drop Location Code | string | |
| `pickup_state` | Pickup State | string | |
| `drop_state` | Drop State | string | |
| `box_code` | Box Code | string | |
| `shipment_created_datetime_utc` | Shipment Time (UTC) | timeHHMM | typically used with `BETWEEN`, value `"HH:MM,HH:MM"` (supports overnight ranges) |

### `ENTERPRISE_CUSTOM`
Custom fields defined by the brand, passed in the order payload under `additional.custom_fields`.

Order payload example:
```json
{
  "additional": {
    "custom_fields": [
      {"key": "wh_name", "value": "DKS_Blr_Marathahalli_002"}
    ]
  }
}
```

Corresponding condition:
```json
{
  "rule_type": "ENTERPRISE_CUSTOM",
  "key": "wh_name",
  "op": "EQ",
  "value": "DKS_Blr_Marathahalli_002",
  "value_type": "string",
  "field_name": null,
  "field_type": "",
  "field_value": null
}
```

---

## Operators

Source of truth: `RecsV2.evaluate_operator2` in `selector/service/recsv2.py`.

Source of truth: `RecsV2.evaluate_operator2` in `selector/service/recsv2.py`.

| Operator | Meaning | Applies To |
|----------|---------|------------|
| `EQ` | Exact match (case-insensitive for strings) | string, float, integer |
| `NEG` | Not in — none of the actual value(s) may appear in the rule's comma-separated list | string |
| `EQ` | Exact match (case-insensitive for strings) | string, float, integer |
| `NEG` | Not in — none of the actual value(s) may appear in the rule's comma-separated list | string |
| `IN` | Value exists in comma-separated list | string |
| `CONTAINS` | Partial string match (rule value is substring of actual value) | string |
| `CONTAINS` | Partial string match (rule value is substring of actual value) | string |
| `GTE` | Greater than or equal | float, integer |
| `GT` | Strictly greater than | float, integer |
| `LTE` | Less than or equal | float, integer |
| `LT` | Strictly less than | float, integer |
| `BETWEEN` | Inclusive range — value is `"low,high"` (or `"HH:MM,HH:MM"` for timeHHMM, supports overnight ranges) | float, integer, timeHHMM |
| `BETWEEN` | Inclusive range — value is `"low,high"` (or `"HH:MM,HH:MM"` for timeHHMM, supports overnight ranges) | float, integer, timeHHMM |

> There is **no** `NOT IN` operator — the exclusion operator is `NEG`.
> All string comparisons are **case-insensitive** (both sides lowercased).
> `IN`/`NEG` accept comma-separated values as a single string: `"value": "110001,110002,110003"`. For `IN`/`NEG`, the actual order value can itself be comma-separated and is treated as a list (match if ANY element overlaps).
> There is **no** `NOT IN` operator — the exclusion operator is `NEG`.
> All string comparisons are **case-insensitive** (both sides lowercased).
> `IN`/`NEG` accept comma-separated values as a single string: `"value": "110001,110002,110003"`. For `IN`/`NEG`, the actual order value can itself be comma-separated and is treated as a list (match if ANY element overlaps).

---

## Priority

- Should be **unique** across all filter rules for a customer (dashboard convention — the DB does not enforce it)
- Should be **unique** across all filter rules for a customer (dashboard convention — the DB does not enforce it)
- Evaluation is strictly sequential: Priority 1 → 2 → 3 → ...
- **Lower number = evaluated first**
- First rule where ALL conditions pass wins; rest are not evaluated (unless rule iteration kicks in)
- First rule where ALL conditions pass wins; rest are not evaluated (unless rule iteration kicks in)
- Dashboard shows all priorities currently in use to avoid conflicts

---

## Important Notes

1. `value` is always sent as a **string** in the API regardless of `value_type`
2. `field_name`, `field_type`, `field_value` are legacy — always send `null`, `""`, `null`
3. A condition only matches if the order data actually contains the key (missing/None values fail the condition)
4. When no OL matches, the filter pool's final order is decided by **PBA scoring**, not by `account_list` order
5. The matched rule's name appears in the RR log as `filters_ran.clickpost_defined_and_custom_rule_matched`; all matched rules (with `sequence`, `rule_name`, `rule_priority`, `has_serviceable_accounts`) appear in `3_clickpost_filters_rules_list`
3. A condition only matches if the order data actually contains the key (missing/None values fail the condition)
4. When no OL matches, the filter pool's final order is decided by **PBA scoring**, not by `account_list` order
5. The matched rule's name appears in the RR log as `filters_ran.clickpost_defined_and_custom_rule_matched`; all matched rules (with `sequence`, `rule_name`, `rule_priority`, `has_serviceable_accounts`) appear in `3_clickpost_filters_rules_list`
