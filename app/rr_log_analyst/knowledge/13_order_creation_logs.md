## Order Creation Logs

The `fetch_order_creation_log` tool retrieves the **order creation request/response log** for a shipment (service_id=1 on the request_logs_v2 API). This is distinct from the RR/allocation log (service_id=3).

### What it contains

Each entry in the log represents one order creation attempt and includes:

- **user_request** — the payload Clickpost received from the enterprise (pickup/drop details, weight, dimensions, etc.)
- **courier_partner_request** — the exact payload Clickpost sent to the courier partner API
- **courier_partner_response** — the raw response from the courier partner
- **clickpost_response** — the response Clickpost returned to the enterprise
- **timestamps** — when each step happened (clickpost_request, cp_request, cp_response, clickpost_response, order_creation_time)

Multiple entries = multiple order creation attempts for the same reference number (e.g. retries, re-allocations).

### When to use

| Question | Tool |
|----------|------|
| What payload was sent to the courier partner? | `fetch_order_creation_log` |
| Why did order creation fail? | `fetch_order_creation_log` — check `courier_partner_response` for error |
| What did the CP return for this AWB? | `fetch_order_creation_log` |
| Was order actually created with the CP? | `fetch_order_creation_log` — check timestamps and CP response |
| Debug order placement issues | `fetch_order_creation_log` + `fetch_rr_log` (allocation) |

### Retention

Logs are retained for roughly **7 days** (same as RR logs).

### Relationship to RR logs

- **RR log** (service_id=3) = allocation/recommendation — which courier was selected and why
- **Order creation log** (service_id=1) = what happened when the order was actually placed with the selected courier

For a complete investigation, fetch both: RR log shows *why* a courier was chosen, order creation log shows *what happened* when the order was placed.
