# API Execution Guide

## Endpoint
All filter and ordered list operations use the same endpoint:

```
POST https://internal.clickpost.in/api/v1/selector-filters-config/
```

## Auth Headers (required on every request)
```
authorization: Token <customer_token>
content-type: application/json
accept: application/json, text/plain, */*
staff-user: <staff_user_id>
```

---

## Operation Matrix

| Operation | `rule_type` | `id` | `updated_at` |
|-----------|-------------|------|--------------|
| Create Filter | `"FILTER"` | `null` | `""` |
| Update Filter | `"FILTER"` | `<existing_id>` | `"<ISO 8601 timestamp>"` |
| Create OL | `"ORDERED_LIST"` | `null` | `""` |
| Update OL | `"ORDERED_LIST"` | `<existing_id>` | `"<ISO 8601 timestamp>"` |

---

## Example Curls

### Create Filter
```bash
curl 'https://internal.clickpost.in/api/v1/selector-filters-config/' \
  -H 'authorization: Token <token>' \
  -H 'content-type: application/json' \
  -H 'staff-user: <staff_user_id>' \
  --data-raw '{
    "rule_type": "FILTER",
    "name": "0-3 KG Prepaid",
    "priority": 5,
    "enabled": true,
    "id": null,
    "updated_at": "",
    "allocation_type": "PERCENTAGE",
    "load_distribution_filter_enabled": false,
    "account_list": [1006980, 1007112],
    "rule": [
      {
        "rule_type": "CLICKPOST_DEFINED",
        "key": "weight",
        "op": "LTE",
        "value": "3000",
        "value_type": "float",
        "field_name": null,
        "field_type": "",
        "field_value": null
      },
      {
        "rule_type": "CLICKPOST_DEFINED",
        "key": "order_type",
        "op": "IN",
        "value": "Prepaid",
        "value_type": "string",
        "field_name": null,
        "field_type": "",
        "field_value": null
      }
    ]
  }'
```

### Update Filter
```bash
curl 'https://internal.clickpost.in/api/v1/selector-filters-config/' \
  -H 'authorization: Token <token>' \
  -H 'content-type: application/json' \
  -H 'staff-user: <staff_user_id>' \
  --data-raw '{
    "rule_type": "FILTER",
    "name": "0-3 KG Prepaid",
    "priority": 5,
    "enabled": true,
    "id": 489264,
    "updated_at": "2026-01-19T14:07:54.412432Z",
    "allocation_type": "PERCENTAGE",
    "load_distribution_filter_enabled": false,
    "account_list": [1006980, 1007112],
    "rule": [...]
  }'
```

### Create Ordered List
```bash
curl 'https://internal.clickpost.in/api/v1/selector-filters-config/' \
  -H 'authorization: Token <token>' \
  -H 'content-type: application/json' \
  -H 'staff-user: <staff_user_id>' \
  --data-raw '{
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
        "value": "562123,560093",
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
  }'
```

### Update Ordered List
```bash
curl 'https://internal.clickpost.in/api/v1/selector-filters-config/' \
  -H 'authorization: Token <token>' \
  -H 'content-type: application/json' \
  -H 'staff-user: <staff_user_id>' \
  --data-raw '{
    "rule_type": "ORDERED_LIST",
    "name": "BLR WH → Delhi",
    "priority": 1,
    "enabled": true,
    "id": 489264,
    "updated_at": "2026-01-19T14:07:54.412432Z",
    "allocation_type": "PERCENTAGE",
    "load_distribution_filter_enabled": false,
    "account_list": [1006980, 817, 187852],
    "rule": [...]
  }'
```

---

## Condition Object Template
Copy-paste template for each condition in `rule[]`:

```json
{
  "rule_type": "CLICKPOST_DEFINED",
  "key": "<field_name>",
  "op": "<operator>",
  "value": "<value>",
  "value_type": "<string|float|integer>",
  "field_name": null,
  "field_type": "",
  "field_value": null
}
```

For Enterprise Custom fields:
```json
{
  "rule_type": "ENTERPRISE_CUSTOM",
  "key": "<custom_field_key>",
  "op": "<operator>",
  "value": "<value>",
  "value_type": "<string|float|integer>",
  "field_name": null,
  "field_type": "",
  "field_value": null
}
```

---

## Notes
- `value` is always a string in the payload regardless of `value_type`
- `field_name`, `field_type`, `field_value` are legacy — always `null`, `""`, `null`
- `id: null` + `updated_at: ""` = create new rule
- `id: <int>` + `updated_at: "<timestamp>"` = update existing rule
