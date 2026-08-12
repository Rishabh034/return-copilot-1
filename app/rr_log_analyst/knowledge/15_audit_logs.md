# Audit Logs

`fetch_audit_logs` — dashboard activity. No structured before/after; infer by comparing `payload` across entries or to current config.

| Field | Meaning |
|-------|---------|
| `updated_at` | Log time (from `created_at`) |
| `section_name` / `service_name` | Dashboard section / action |
| `username`, `ip_address` | Actor |
| `payload` | Request-body snapshot (often string values). Nested `updated_at` → `payload_updated_at`, not log time |

Params: `start_date`, `end_date` (`YYYY-MM-DD`, inclusive), `action_taken` (exact `section_name`, one per call). **Max 7 days/call** — use non-overlapping windows for longer.

`action_taken` values: `Track`, `Channel`, `Cash Reconciliation`, `Carrier`, `Login`, `Manifest`, `Allocation`, `Notifications`, `Return & Exchange`, `Control Tower`, `NDR`, `Analytics`, `Dashboard`, `Settings`, `Dispatch`, `Access Control`, `Cancel Orders`, `QC Dashboard`, `Quick Commerce`. Start with `Allocation` for rule/config questions.

Tool paginates and merges. `count` = total matches; `latest_updated_at` / `earliest_updated_at` from returned entries.

### Find who changed a rule
1. Rule `updated_at` via `find_rule`.
2. `fetch_audit_logs` ≤7-day window around it, `action_taken=Allocation`.
3. Match `payload.id` / `payload.name`.

Limitations: rule vs log timestamps may not align — widen before concluding; missing `payload.id`/`name` can hide a change; **no entry ≠ unchanged** — say so explicitly.
