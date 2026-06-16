## Audit Logs

The `fetch_audit_logs` tool calls the Clickpost dashboard activity log API (`GET /dashboard/api/v1/activity-log`).

### What the API returns

Each log entry is one logged dashboard API action for the enterprise. Fields on each entry:

| Field | Meaning |
|---|---|
| `updated_at` | When the action was logged (sourced from the audit log `created_at`) |
| `section_name` | Dashboard section for the logged API |
| `service_name` | Label for the specific API action |
| `username` | Dashboard user who performed the action |
| `ip_address` | Client IP when present |
| `payload` | Request-body snapshot at log time (values are often strings) |

The API does **not** return a structured before/after diff. To infer what changed, compare `payload` across log entries or against the current rule/config.

If `payload` contains `updated_at`, that is metadata on the object in the payload — not the log timestamp. The tool may surface it as `payload_updated_at`.

### Parameters

| Parameter | Required | Description |
|---|---|---|
| `start_date` | Yes | Inclusive start, `YYYY-MM-DD` |
| `end_date` | Yes | Inclusive end, `YYYY-MM-DD` |
| `action_taken` | Yes | Filters by `section_name` (exact match). Comma-separated values are supported by the API; the tool accepts one value per call. |

**This tool allows at most 7 days per call.** The Clickpost API itself caps a single request at 31 days; the 7-day limit here is an analyst-tool guard. Use separate non-overlapping windows for longer periods.

### `action_taken` values

These must match `section_name` on subscribed audit-log services. Allowed values:

`Track`, `Channel`, `Cash Reconciliation`, `Carrier`, `Login`, `Manifest`, `Allocation`, `Notifications`, `Return & Exchange`, `Control Tower`, `NDR`, `Analytics`, `Dashboard`, `Settings`, `Dispatch`, `Access Control`, `Cancel Orders`, `QC Dashboard`, `Quick Commerce`

For allocation rule questions, start with `Allocation`. Use other section values only when the change likely belongs to that section.

### Pagination

The API returns paginated results (`data`, `totalCount`, `nextUrl`, `pageSize`). The tool follows `nextUrl` and merges all pages for the requested window before returning.

### Tool response shape

```
{
  "start_date": "2026-06-01",
  "end_date": "2026-06-07",
  "action_taken": "Allocation",
  "count": 12,
  "page_size": 100,
  "latest_updated_at": "...",
  "earliest_updated_at": "...",
  "logs": [ ... ]
}
```

- `count` is `totalCount` from the API (total matching logs in the window, not just the first page).
- `latest_updated_at` / `earliest_updated_at` are computed from log `updated_at` values in the returned entries.

### `fetch_audit_logs_for_rule`

Convenience wrapper when you already know which rule to investigate:

1. Resolves the rule by id or name.
2. Reads the rule object's `updated_at`.
3. Fetches audit logs for **that calendar date only** with the given `action_taken` (default `Allocation`).
4. Returns `matching_logs` where `payload.id` or `payload.name` matches the rule, plus `all_logs_in_window`.

```
fetch_audit_logs_for_rule(rule_id="ROI_XB_DEL_HY_1", action_taken="Allocation")
```

**Limitations (do not assume otherwise):**

- Uses a single-day window from the rule's `updated_at` date — the matching log may fall on an adjacent day.
- Rule `updated_at` and log `updated_at` are different fields and may not align exactly.
- Matching relies on `payload.id` / `payload.name`; if the payload omits them, `matching_logs` may be empty even when a change occurred.
- Empty `matching_logs` does not prove the rule was not changed; widen the date range with `fetch_audit_logs`.

### Suggested workflow

1. If investigating a named rule, call `fetch_audit_logs_for_rule` or read the rule's `updated_at` first.
2. Call `fetch_audit_logs` with a short date window (≤ 7 days) and the relevant `action_taken`.
3. Find the relevant entry by timestamp, `service_name`, and `payload.id` / `payload.name`.
4. Read `payload` for the state at log time; compare entries manually when needed.
