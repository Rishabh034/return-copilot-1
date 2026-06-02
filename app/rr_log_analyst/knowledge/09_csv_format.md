# CSV Export Format — Filters & Ordered Lists

## Overview
Clickpost exports filter rules and ordered list rules as CSV files. Both share the same structure. Each row is one rule. The CSV is highly wide because the `account_list` (carrier names) is stored as a Python list string in one cell, but the commas inside the list cause the CSV to split it across hundreds of columns.

---

## Named Columns (first 6)

| Column | Description |
|--------|-------------|
| `Name` | Rule name |
| `Accounts` | **Starts** the carrier accounts list — a Python list string e.g. `['SHIPROCKET_DELHI', 'SHIPROCKET_MUMBAI', ...]` |
| `Rule` | Continuation of the accounts list (overflow due to CSV comma splitting) |
| `Priority` | Continuation of the accounts list (overflow) |
| `Enabled` | Continuation of the accounts list (overflow) |
| `Load Distribution Filter Enabled` | Continuation of the accounts list (overflow) |

After these 6 named columns, there are hundreds of `Unnamed: N` columns that continue the accounts list overflow, until the actual rule conditions column is reached.

---

## Actual Column Structure (after parsing)

The true structure of each row, once the CSV overflow is understood:

| Logical Field | Location in CSV | Example |
|---------------|-----------------|---------|
| Rule Name | `Name` column | `"0 - 3 KG"` |
| Accounts list (P1→PN) | `Accounts` → `Rule` → ... → `Unnamed: N` (spans many columns) | `['SHIPROCKET_DELHI', 'DELHIVERY_BLRH', ...]` |
| Rule conditions | First column after accounts list ends | `[{'op': 'LTE', 'key': 'weight', 'value': '3000', ...}]` |
| Priority | Column after rule conditions | `5` |
| Enabled | Column after priority | `TRUE` |
| Load Distribution Filter Enabled | Column after enabled | `FALSE` |

---

## Why the Accounts List Spans Many Columns

The `Accounts` cell in Clickpost's export contains a Python list string:
```
['SHIPROCKET_DELHI', 'SHIPROCKET_MUMBAI', 'XPRESSBEES_MUMBAI', ...]
```
Each `, ` inside the list is treated as a CSV column separator, splitting each carrier name into its own column. A rule with 300 carriers will span ~300 columns.

**Additional edge case:** Carrier names containing apostrophes (e.g. `Let's wag_MH`) cause the CSV quote parser to terminate the cell early. The remaining carriers after such a name are wrapped in double quotes in subsequent columns.

---

## Rule Conditions Column

Once the accounts list ends, the next non-null column contains the rule conditions as a JSON-like Python list string:

```python
[
  {'op': 'LTE', 'key': 'weight', 'value': '3000', 'rule_type': 'CLICKPOST_DEFINED', 'field_name': None, 'field_type': '', 'value_type': 'float', 'field_value': None},
  {'op': 'EQ', 'key': 'delivery_type', 'value': 'FORWARD', 'rule_type': 'CLICKPOST_DEFINED', 'field_name': None, 'field_type': '', 'value_type': 'string', 'field_value': None},
  {'op': 'IN', 'key': 'order_type', 'value': 'Prepaid', 'rule_type': 'CLICKPOST_DEFINED', 'field_name': None, 'field_type': '', 'value_type': 'string', 'field_value': None}
]
```

Each condition object fields:

| Field | Description |
|-------|-------------|
| `op` | Operator: `EQ`, `NEG`, `IN`, `CONTAINS`, `GTE`, `GT`, `LTE`, `LT` |
| `key` | Field name: `weight`, `drop_pincode`, `pickup_pincode`, `order_type`, `delivery_type`, etc. |
| `value` | Value to compare against |
| `rule_type` | `CLICKPOST_DEFINED` or `ENTERPRISE_CUSTOM` |
| `value_type` | `float`, `string`, `integer` |
| `field_name` | Always `None` (legacy) |
| `field_type` | Always `""` (legacy) |
| `field_value` | Always `None` (legacy) |

---

## Difference: Filter CSV vs OL CSV

| Aspect | Filter CSV | OL CSV |
|--------|------------|--------|
| Accounts meaning | Eligible carrier pool (order = fallback priority) | Priority order — P1 is index 0 |
| Typical condition keys | `weight`, `order_type`, `delivery_type` | `pickup_pincode`, `drop_pincode` |
| Number of accounts | Large (~150–300 carriers) | Small (1–12 carriers per route) |
| Number of rows | Small (one per weight/payment tier) | Large (one per pickup-drop zone) |

---

## How to Parse the CSV

### Step 1 — Find the rule conditions column
Iterate over non-null values in a row. The rule conditions column is the first value containing both `'op'` and `'key'` as substrings.

```python
for i, val in enumerate(values):
    if isinstance(val, str) and "'op'" in val and "'key'" in val:
        rule_col_idx = i
        break
```

### Step 2 — Extract carrier accounts
- `values[1]` (the `Accounts` column) contains the bulk of the list as a Python list string — extract single-quoted names from it
- `values[2]` through `values[rule_col_idx - 1]` each contain exactly one carrier name — strip outer double quotes and single quotes

```python
def extract_accounts(values, rule_col_idx):
    carriers = []
    # First cell has bulk of the list
    first = str(values[1])
    for name in re.findall(r"'([^']+)'", first):
        if name.strip():
            carriers.append(name.strip())
    # Remaining cells have one carrier each
    for v in values[2:rule_col_idx]:
        s = str(v).strip().strip('"').strip().strip("'").strip()
        if s and s != 'nan':
            carriers.append(s)
    return carriers
```

> **Note:** Carrier names with apostrophes (e.g. `Let's wag_MH`) will be partially garbled in the first cell extraction — `Let` instead of `Let's wag_MH`. This is a known CSV export issue. These names appear correctly in subsequent cells.

### Step 3 — Parse rule conditions
```python
import ast
conditions = ast.literal_eval(values[rule_col_idx])
```

### Step 4 — Extract metadata
```python
priority = values[rule_col_idx + 1]
enabled  = values[rule_col_idx + 2]
load_dist = values[rule_col_idx + 3]
```

---

## Real Examples

### Filter CSV Row (0 - 3 KG Prepaid)
```
Name:       "0 - 3 KG"
Accounts → ... (315 carriers across ~315 columns) ... → Rule conditions:
[
  {'op': 'LTE', 'key': 'weight', 'value': '3000', 'rule_type': 'CLICKPOST_DEFINED', 'value_type': 'float', ...},
  {'op': 'EQ',  'key': 'delivery_type', 'value': 'FORWARD', 'rule_type': 'CLICKPOST_DEFINED', 'value_type': 'string', ...},
  {'op': 'IN',  'key': 'order_type', 'value': 'Prepaid', 'rule_type': 'CLICKPOST_DEFINED', 'value_type': 'string', ...}
]
Priority: 5
Enabled: TRUE
```

### OL CSV Row (BLR WH)
```
Name:       "BLR WH"
Accounts → ['Elasticrun_Blrwh', 'Zippee_VTRSF', 'elasticrun_BLRWH_NDD', ...12 carriers...] → Rule conditions:
[
  {'op': 'IN', 'key': 'drop_pincode', 'value': '560082, 560083, 560016, ...491 pincodes...', 'rule_type': 'CLICKPOST_DEFINED', 'value_type': 'string', ...},
  {'op': 'IN', 'key': 'pickup_pincode', 'value': '562123,562162,560093', 'rule_type': 'CLICKPOST_DEFINED', 'value_type': 'string', ...}
]
Priority: 1
Enabled: TRUE
```

---

## Key Gotchas

1. **CSV is extremely wide** — can have 16,000+ columns due to account list overflow
2. **Use `low_memory=False`** when reading with pandas: `pd.read_csv(file, low_memory=False)`
3. **Apostrophe names** cause partial garbling in the first accounts cell — `Let` instead of `Let's wag_MH`
4. **Account names in CSV ≠ Account IDs in API** — CSV uses string names (e.g. `SHIPROCKET_DELHI`), API uses integer IDs (e.g. `1006980`). Mapping required to convert between the two.
5. **Condition values for pincodes** are comma-separated within a single string: `"560082, 560083, 560016"` — split on `,` and strip to get individual pincodes
6. **`enabled`** may appear as `True` (Python bool) or `"TRUE"` (string) depending on the row — always use `str(enabled).upper() == 'TRUE'` to check
