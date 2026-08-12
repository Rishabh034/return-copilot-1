# Allocation and Order Placement Records

Both retained ~**7 days**. For a full investigation, fetch both.

| Record | Answers |
|--------|---------|
| Allocation (`fetch_rr_log`) | Why a courier was selected |
| Order placement (`fetch_order_creation_log`) | What happened when placed with that courier |

## Allocation

| Field | Meaning |
|-------|---------|
| `filters_ran` | Pipeline trace (stage keys live with each pipeline stage) |
| `preference_array` | Final ranked couriers (`cp_name`, `cp_id`, `account_id`, `account_code`, `priority`, `scores_computation`, charge, SLA, …) |
| `user_request` | Order payload (pincodes, order/delivery type, weight, dims) |
| `pincode_serviceable` | Drop pincode serviceable at all? |
| `return_serviceable_accounts` | Whether fallback/PBA may fill `preference_array` |
| `execution_time_dict` | Per-stage timings |

Accounts in `filters_ran`: `AccountCode(Carrier(account_id))`.

AWB/order_id lookup: last **90 days** if dates omitted; `needs_date_range: true` → ask registration window and retry with both dates.

## Order placement

One entry per attempt (retries/re-allocations = multiple). Fields: `user_request`, `courier_partner_request`, `courier_partner_response`, `clickpost_response`, timestamps (`clickpost_request`, `cp_request`, `cp_response`, `clickpost_response`, `order_creation_time`).

Use for payload to CP, creation failure (read partner response), AWB returned, whether order actually created. Needs `reference_number` — resolve via `fetch_rr_log` first if user gave AWB/order_id.
