# Ordered List Rules

## What is an Ordered List Rule?
An Ordered List (OL) rule is the second evaluation layer in Clickpost's carrier allocation engine. It takes the eligible carrier pool output by a matched Filter Rule and defines the **priority order** for a specific route. The final carrier allocation is the intersection of the filter's eligible carriers and the OL's carrier list, preserved in OL's priority order.

## Role in the Pipeline
```
[Pre-filter checks]  ← separate system, TBD
        ↓
  Filter Rules
        ↓
  Ordered List       ← THIS LAYER
        ↓
  Final Allocation
```

## Key Behaviours
- Conditions within a rule are evaluated with **AND logic** — every condition must pass
- Rules are evaluated in **priority order** — lowest number first
- **First matching rule wins** — evaluation stops, remaining rules are skipped
- `account_list` is an **ordered priority list** — P1 (index 0) is highest priority
- Final allocation = OL carriers filtered to only those present in the matched Filter's `account_list`, in OL's order
- If a carrier is in OL but NOT in the matched filter's pool → dropped from final allocation
- OL does not work independently — it requires a filter to have matched first and passed an eligible pool

## Intersection Logic
```python
final_carriers = [c for c in OL.account_list if c in Filter.account_list]
```
OL's priority order is preserved. Filter acts as the eligibility gate.

---

## API

**Endpoint:** `POST https://internal.clickpost.in/api/v1/selector-filters-config/`

### Create
```json
{
  "rule_type": "ORDERED_LIST",
  "name": "BLR WH → Delhi",
  "priority": 1,
  "enabled": true,
  "id": null,
  "updated_at": "",
  "allocation_type": "PERCENTAGE",
  "load_distribution_filter_enabled": false,
  "account_list": [1006980, 817, 187852],
  "rule": [
    {
      "rule_type": "CLICKPOST_DEFINED",
      "key": "pickup_pincode",
      "op": "IN",
      "value": "562123,562162,560093",
      "value_type": "string",
      "field_name": null,
      "field_type": "",
      "field_value": null
    },
    {
      "rule_type": "CLICKPOST_DEFINED",
      "key": "drop_pincode",
      "op": "IN",
      "value": "110001,110002,110003",
      "value_type": "string",
      "field_name": null,
      "field_type": "",
      "field_value": null
    }
  ]
}
```

### Update
Same payload with `id` and `updated_at` populated:
```json
{
  "rule_type": "ORDERED_LIST",
  "id": 489264,
  "updated_at": "2026-01-19T14:07:54.412432Z",
  ...rest same as create
}
```

---

## Top-Level Fields

| Field | Type | Create | Update | Description |
|-------|------|--------|--------|-------------|
| `rule_type` | string | `"ORDERED_LIST"` | `"ORDERED_LIST"` | Always `"ORDERED_LIST"` |
| `name` | string | required | required | Human-readable rule name |
| `priority` | integer | required | required | Evaluation order. Lower = first. Must be unique |
| `enabled` | boolean | required | required | `true` = active, `false` = skipped |
| `id` | integer / null | `null` | `<rule_id>` | null for create, existing ID for update |
| `updated_at` | string | `""` | `"<ISO 8601>"` | empty for create, timestamp for update |
| `account_list` | integer[] | required | required | Carriers in **priority order** — P1 first, P2 second... |
| `allocation_type` | string | `"PERCENTAGE"` | `"PERCENTAGE"` | Always `"PERCENTAGE"` currently |
| `load_distribution_filter_enabled` | boolean | `false` | `false` | false unless load distribution configured |
| `rule` | object[] | required | required | Array of condition objects (AND logic) |

---

## Condition Object (`rule[]`)

Identical structure to Filter condition objects.

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

---

## Priority

- Must be **unique** across all OL rules for a customer
- Evaluation is strictly sequential: Priority 1 → 2 → 3 → ...
- **Lower number = evaluated first**
- First rule where ALL conditions pass wins; rest are not evaluated

---

## Difference from Filter `account_list`

| | Filter `account_list` | OL `account_list` |
|-|----------------------|-------------------|
| Meaning | Eligible carrier pool | Priority order (P1→PN) |
| Order matters? | Only as fallback when no OL matches | Always — defines P1, P2, P3... |
| Used for | Gating which carriers can be used | Ordering the eligible carriers |

---

## Important Notes

1. OL **cannot work alone** — it needs a Filter to have matched first
2. Carriers in OL not present in the matched Filter's pool are **silently dropped**
3. If intersection is empty → no eligible carriers for that order
4. `value` is always sent as a **string** regardless of `value_type`
5. `field_name`, `field_type`, `field_value` are legacy — always send `null`, `""`, `null`
6. Most common OL conditions are `pickup_pincode IN` + `drop_pincode IN` for route-based routing
