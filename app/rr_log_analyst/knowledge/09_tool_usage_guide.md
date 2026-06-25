## Tool Usage Guide

**Pattern:** Tool fetches **full data** first → stored as complete JSON + `raw_data_parts` (split to fit Haiku context) → **separate Haiku call per part** builds scratchpad batch summaries → main model gets compact envelope.

### Scratchpad & drill-down

| Phase | What happens |
|-------|----------------|
| 1. Fetch | PRIMARY tool returns full shaped JSON |
| 2. Store | Full JSON + raw data parts saved in memory (no LLM) |
| 3. Summarize | One new Haiku call per raw data part → scratchpad batches |
| 4. Answer | Main model reads scratchpad envelope; drills down as needed |

| Need | Action |
|------|--------|
| Overview | Read `scratchpad` in envelope, or `query_full_scratchpad(result_id)` |
| Raw fetched data (one part) | `query_raw_data_part(result_id, part_index=1..N)` |
| Scratchpad summary (one batch) | `query_scratchpad_batch(result_id, batch_index=1..N)` |
| Raw JSON keyword slice | `query_tool_result(result_id, keywords=[...])` |
| User asks about a rule name, stage key, account, priority | Keywords are auto-matched from the user message before each turn — check injected `[Auto-fetched tool result slices ...]` if present |

**SUB-TOOLs** (`summarize_*`, `get_*_pipeline`, etc.) still reduce Clickpost API load when you know upfront you only need a slice.

### RR logs

| Question | PRIMARY | SUB-TOOLs if needed |
|----------|---------|---------------------|
| User gives AWB or order_id | `fetch_rr_log_by_shipment` | `resolve_reference_number`, then `fetch_rr_log` |
| User gives reference_number | `fetch_rr_log` | `summarize_rr_log`, `get_rr_log_pipeline`, `get_rr_log_ranking` |
| Why Delhivery when OL puts it last? | `fetch_rr_log` → inspect `filters_ran` | `get_rr_log_pipeline(stage=...)`, `get_ordered_list_rule` |
| Why was X allocated? | `fetch_rr_log` / `fetch_rr_log_by_shipment` | `summarize_rr_log`, `get_rr_log_pipeline`, `get_rr_log_ranking` |
| Why was courier Y not selected? | `fetch_rr_log` | `search_rr_log_courier`, `get_rr_log_pipeline(stage=...)` |
| Compare orders | `fetch_rr_logs_batch` | `summarize_rr_logs_batch` |

**AWB / order_id lookup:** `fetch_rr_log_by_shipment` tries the **last 90 days** when the user does not give `start_date` / `end_date`. If no shipment is found, the tool returns `needs_date_range: true` — ask the user for the shipment registration window and retry with both dates.

### Enterprise context

The enterprise you are assisting (name + `enterprise_user_profile_id`) and the current message time are injected into the **## Context** section of the system prompt. Use those directly — there is no enterprise lookup tool.

### Rules

| Question | PRIMARY | SUB-TOOLs if needed |
|----------|---------|---------------------|
| List all filter / OL rules | `list_filter_rules` / `list_ordered_list_rules` | `search_filter_rules(priority=12)`, `summarize_filter_rules` |
| Find rule by name | `find_rule` / `get_filter_rule` | `search_filter_rules(name=...)` |
| Rules for pincode | `find_rules_for_pincode` | `search_filter_rules(pickup_pincode=...)` |
| Rules for carrier | `get_account_info` → `find_rules_for_carrier` | `search_filter_rules` |

### Flow config & load distribution

| Question | PRIMARY | SUB-TOOLs if needed |
|----------|---------|---------------------|
| Pipeline toggles | `get_enterprise_flow_config` | `get_flow_config_flag(flag=...)` |
| LD targets | `get_load_distribution_config` | pass `account_id` to filter one carrier |

### Simulation & accounts

| Question | PRIMARY | SUB-TOOLs if needed |
|----------|---------|---------------------|
| Simulate allocation | `run_recommendation` | `summarize_recommendation`, `get_recommendation_pipeline` |
| Account lookup | `get_account_info` | `summarize_accounts`, `search_accounts` |

### Order creation logs

| Question | PRIMARY |
|----------|---------|
| What was sent to the courier partner? | `fetch_order_creation_log` |
| Why did order creation fail? | `fetch_order_creation_log` — check CP response |
| Was order placed with CP? | `fetch_order_creation_log` — check timestamps |
| Full debug (allocation + creation) | `fetch_rr_log` + `fetch_order_creation_log` |

Requires `reference_number`. If user gives AWB/order_id, resolve first with `fetch_rr_log_by_shipment` or `resolve_reference_number`.

### Audit logs

| Question | PRIMARY | SUB-TOOLs if needed |
|----------|---------|---------------------|
| Who changed rules / config? | `fetch_audit_logs` | `fetch_audit_logs_for_rule(rule_id=...)` to narrow to one rule |

Always fetch actual data before explaining. Never guess without evidence. See `11_audit_logs.md` for API fields, limits, and matching behavior.

**"Who changed this rule?"** → `fetch_audit_logs_for_rule(rule_id=...)` or `fetch_audit_logs` with a ≤7-day window and `action_taken=Allocation`; match by `payload.id` / `payload.name` and log `updated_at`.

**"What changed in this period?"** → `fetch_audit_logs(start_date=..., end_date=..., action_taken=...)`; read `payload` snapshots (no guaranteed diff).

**"When was this rule last changed?"** → Rule `updated_at` from rule tools, then audit logs around that date. Rule `updated_at` and log `updated_at` may differ; widen the window if `matching_logs` is empty.