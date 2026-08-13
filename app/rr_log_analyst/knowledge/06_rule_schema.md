# Rule Schema (Filter + Ordered List)

Same DB object (`RecommendationInclusionFilters`) with different `rule_type`. Fields, conditions, operators, priority identical; behaviour of `account_list` differs by type.

## Top-level fields

| Field | Notes |
|-------|-------|
| `rule_type` | `"FILTER"` or `"ORDERED_LIST"`; legacy filter rows may be `"CLICKPOST_DEFINED"`/`"ENTERPRISE_CUSTOM"` (treated as filters) |
| `name`, `priority`, `enabled`, `id`, `updated_at` | Lower priority first; `enabled=false` skipped |
| `account_list` | Account IDs only (not CPIDs). Filter = eligible pool (order irrelevant); OL = priority order (index 0 = P1) |
| `allocation_type` | `PERCENTAGE` or `ABSOLUTE_CAPPING` (LD) |
| `load_distribution_filter_enabled` | false unless LD configured |
| `rule` | Condition objects, AND |

## Condition object (`rule[]`)

All conditions must pass. Missing/None key on the order → condition fails.

| Field | Notes |
|-------|-------|
| `rule_type` | `CLICKPOST_DEFINED`, `ENTERPRISE_CUSTOM`, or rare `CLUSTER_FIELDS` |
| `key`, `op`, `value` | `value` is **always a string**, even for numbers |
| `value_type` | `string` / `float` / `integer` / `timeHHMM` |
| `field_name` / `field_type` / `field_value` | Legacy — always `null` / `""` / `null` |

## CLICKPOST_DEFINED keys

Native order fields. Special behaviour only:
- `volumetric_weight` — L×B×H ÷ per-account coefficient (default 5); evaluated **per account**, prunes `account_list`
- `zone` — matched **per account** against serviceability zone; prunes `account_list`
- `shipment_created_datetime_utc` — `timeHHMM`, usually `BETWEEN "HH:MM,HH:MM"` (overnight OK)
- `higher_of_dry_weight_and_volumetric_weight` — computed

Others (self-explanatory): `item`, `reference_number`, `order_type`, `delivery_type`, `invoice_value`, `pickup_pincode`, `drop_pincode`, `weight`, `pickup_city`, `drop_city`, `drop_postal_code`, `pickup_postal_code`, `pickup_country_code`, `drop_country_code`, `length`, `height`, `breadth`, `pickup_city_postal_code`, `drop_city_postal_code`, `is_restricted`, `fareye_store_id`, `enterprise_store_id`, `pickup_address`, `drop_address`, `pickup_name`, `item_count`, `pickup_location_code`, `drop_location_code`, `pickup_state`, `drop_state`, `box_code`.

## ENTERPRISE_CUSTOM

Brand fields from `additional.custom_fields` (`key`/`value`). Same condition shape; `rule_type: ENTERPRISE_CUSTOM`.

## Operators

| Op | Meaning |
|----|---------|
| `EQ` | Exact (strings case-insensitive) |
| `NEG` | None of the actual value(s) in the comma-separated list (there is **no** `NOT IN`) |
| `IN` | Value in comma-separated list |
| `CONTAINS` | Rule value is substring of actual |
| `GTE`/`GT`/`LTE`/`LT` | Numeric compare |
| `BETWEEN` | Inclusive `"low,high"` or `"HH:MM,HH:MM"` |

`IN`/`NEG`: comma-separated rule values; actual may also be comma-separated — match if **any** element overlaps. All string compares lowercased.

## Priority

Unique per type per customer by convention (DB doesn't enforce). Lower first. First full match wins unless rule iteration applies.
