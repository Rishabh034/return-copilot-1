# Filter Rules

## What is a Filter Rule?
A Filter Rule is the first evaluation layer in Clickpost's carrier allocation engine. It acts as an eligibility gate — it evaluates conditions on an incoming order and if ALL conditions pass, it outputs a pool of eligible carriers to the next layer (Ordered List).

## Role in the Pipeline
```
[Pre-filter checks]  ← separate system, TBD
        ↓
  Filter Rules       ← THIS LAYER
        ↓
  Ordered List
        ↓
  Final Allocation
```

## Key Behaviours
- All conditions within a rule are evaluated with **AND logic** — every condition must pass
- Rules are evaluated in **priority order** — lowest number first
- **First matching rule wins** — evaluation stops, remaining rules are skipped
- If a filter matches but no OL matches → filter's `account_list` IS the final allocation in list order
- If no filter matches → no allocation

---

## Top-Level Fields

| Field | Type | Create | Update | Description |
|-------|------|--------|--------|-------------|
| `rule_type` | string | `"FILTER"` | `"FILTER"` | Always `"FILTER"` |
| `name` | string | required | required | Human-readable rule name |
| `priority` | integer | required | required | Evaluation order. Lower = first. Must be unique |
| `enabled` | boolean | required | required | `true` = active, `false` = skipped |
| `id` | integer / null | `null` | `<rule_id>` | null for create, existing ID for update |
| `updated_at` | string | `""` | `"<ISO 8601>"` | empty for create, timestamp for update |
| `account_list` | integer[] | required | required | Ordered carrier account IDs eligible if rule matches |
| `allocation_type` | string | `"PERCENTAGE"` | `"PERCENTAGE"` | Always `"PERCENTAGE"` currently |
| `load_distribution_filter_enabled` | boolean | `false` | `false` | false unless load distribution configured |
| `rule` | object[] | required | required | Array of condition objects (AND logic) |

---

## Condition Object (`rule[]`)

Each object is one condition. All must be true (AND) for the rule to fire.

| Field | Type | Description |
|-------|------|-------------|
| `rule_type` | string | `"CLICKPOST_DEFINED"` or `"ENTERPRISE_CUSTOM"` |
| `key` | string | The field being evaluated |
| `op` | string | The operator |
| `value` | string | Value to compare against — always a string even for numeric types |
| `value_type` | string | `"string"`, `"float"`, or `"integer"` |
| `field_name` | null | Legacy field — always `null` |
| `field_type` | string | Legacy field — always `""` |
| `field_value` | null | Legacy field — always `null` |

---

## Condition Rule Types

### `CLICKPOST_DEFINED`
Fields that Clickpost extracts and understands natively from every order API call. No custom setup needed — always available for rule evaluation. 34 fields total.

| API Key | Value Type |
|---------|-----------|
| `item` | string |
| `reference_number` | string |
| `order_type` | string |
| `delivery_type` | string |
| `invoice_value` | float |
| `pickup_pincode` | string |
| `drop_pincode` | string |
| `weight` | float |
| `volumetric_weight` | float |
| `higher_of_dry_weight_and_volumetric_weight` | float |
| `pickup_city` | string |
| `drop_city` | string |
| `drop_postal_code` | string |
| `pickup_postal_code` | string |
| `pickup_country_code` | string |
| `drop_country_code` | string |
| `length` | float |
| `height` | float |
| `breadth` | float |
| `pickup_city_postal_code` | string |
| `drop_city_postal_code` | string |
| `is_restricted` | string |
| `fareye_store_id` | string |
| `enterprise_store_id` | string |
| `pickup_address` | string |
| `drop_address` | string |
| `zone` | string |
| `pickup_name` | string |
| `item_count` | integer |
| `pickup_location_code` | string |
| `drop_location_code` | string |
| `pickup_state` | string |
| `drop_state` | string |
| `box_code` | string |

> Tuple format (display name, computed flag etc.) to be confirmed and added later.

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

| Operator | Meaning | Applies To |
|----------|---------|------------|
| `EQ` | Exact match | string, float, integer |
| `NEG` | Not equals / exclude — same as `NOT IN` | string |
| `NOT IN` | Not in list — same as `NEG` | string |
| `IN` | Value exists in comma-separated list | string |
| `CONTAINS` | Partial string match | string |
| `GTE` | Greater than or equal | float, integer |
| `GT` | Strictly greater than | float, integer |
| `LTE` | Less than or equal | float, integer |
| `LT` | Strictly less than | float, integer |

> `NEG` and `NOT IN` are identical in behaviour.
> `IN` accepts comma-separated values as a single string: `"value": "110001,110002,110003"`

---

## Priority

- Must be **unique** across all filter rules for a customer
- Evaluation is strictly sequential: Priority 1 → 2 → 3 → ...
- **Lower number = evaluated first**
- First rule where ALL conditions pass wins; rest are not evaluated
- Dashboard shows all priorities currently in use to avoid conflicts

---

## Important Notes

1. `value` is always sent as a **string** in the API regardless of `value_type`
2. `field_name`, `field_type`, `field_value` are legacy — always send `null`, `""`, `null`
3. `account_list` order matters only as fallback — when no OL matches, carriers are tried in this order
4. `allocation_type: "PERCENTAGE"` is always used currently
