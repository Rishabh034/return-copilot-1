# Filter + Ordered List Empty Intersection Fallback

## The scenario (most common misread)

A Filter rule **matched** and an Ordered List rule **also matched**, yet the allocated carrier does **not** follow the OL priority order (e.g. Delhivery allocated when OL puts Delhivery last).

This is usually **not** a PBA bug and **not** the OL being ignored by mistake — it is the expected engine path when the filter pool is empty.

## Correct system behaviour

```
Serviceable accounts (e.g. Delhivery, XpressBees, Shadowfax)
        ↓
Filter rule MATCHED (e.g. "Pune-SDD")
  account_list ∩ serviceable = EMPTY  →  has_serviceable_accounts: false
  priority_list_account_names = []
        ↓
Ordered List MATCHED (e.g. "SDD_HY")
  OL account_list may have serviceable carriers (has_serviceable_accounts: true)
  BUT intersection = upstream_pool ∩ OL_order
                   = [] ∩ [Shadowfax, Blitz, XpressBees, Delhivery]
                   = []
  ordered_list_filter_accounts = []
        ↓
Fallback (return_serviceable_accounts = true, default)
  preference_array = serviceable accounts
```

**Key insight:** OL carriers must appear in the **upstream pool** (`priority_list_account_names`) to survive intersection. An empty filter pool makes OL priority irrelevant even when the OL rule itself lists the right carriers.

## RR log signals

| Signal | Empty-filter + OL-matched case |
|--------|--------------------------------|
| `clickpost_defined_and_custom_rule_matched` | Filter name (e.g. `Pune-SDD`) |
| `3_clickpost_filters_rules_list[].has_serviceable_accounts` | `false` for matched filter |
| `3_clickpost_define_and_custom_filters_accounts` | `[]` |
| `priority_list_account_names` | `[]` |
| `order_list_matched_rule` | OL name (e.g. `SDD_HY`) |
| `4_ordered_list_rules_list[].has_serviceable_accounts` | may be `true` on OL |
| `4_ordered_list_account_names_filters_applied` | `true` |
| `ordered_list_filter_accounts` | `[]` |
| `preference_array` | serviceable accounts (fallback order) |

Read these fields from `filters_ran` via `fetch_rr_log`, `summarize_rr_log`, or `get_rr_log_pipeline` — do not infer outcomes without log evidence.

## How to explain to a customer

1. **Filter rule matched** — conditions were correct for the order.
2. **Filter carriers were not serviceable** for that route — only Shadowfax NDD was in the filter, but it does not serve the drop pincode.
3. **OL rule also matched** and has the right priority on paper — but intersection needs a non-empty upstream pool.
4. **System fell back to serviceable carriers** — Delhivery is #1 because of fallback ordering (serviceability list / zero-weight PBA), **not** because OL chose Delhivery first.

## Fix (configuration, not platform)

- Add the **serviceable** carriers to the matched **Filter** rule's `account_list`, so `priority_list_account_names` is non-empty; then OL intersection can apply Shadowfax → XpressBees → Delhivery as intended.
- OR widen the filter pool so upstream ∩ OL is non-empty.

## What NOT to say

- Do **not** claim OL ranked Delhivery first when `ordered_list_filter_accounts` is empty.
- Do **not** claim PBA intelligently chose Delhivery when `recs` / `intelligent_feedback` are off and scores are absent — it is a serviceable fallback.
- Do **not** say "OL returned empty" without explaining **why**: upstream pool was empty because the filter had zero serviceable accounts.

## Check `4_ordered_list_rules_list`

Each entry: `{sequence, rule_name, rule_priority, has_serviceable_accounts}`.

- `has_serviceable_accounts: false` on a **filter** entry → filter matched, no serviceable carriers in its list.
- `has_serviceable_accounts: true` on an **OL** entry → OL lists serviceable carriers, but intersection can still be empty if upstream is `[]`.

Use `get_rr_log_pipeline(stage="4_ordered_list_rules_list")` or inspect `filters_ran` from `fetch_rr_log` / `summarize_rr_log`.
