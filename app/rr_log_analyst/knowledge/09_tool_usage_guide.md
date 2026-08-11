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

### Attribute Layer (carrier exception / carrier attribute rules)

Runs **after** serviceability and **before** Filter Rules. Scoped to `courier_partner_id` (CP_ID); eliminates accounts via `exclusion_list` when conditions match. **Not** Filter or OL rules — do not use `list_filter_rules` / `find_rule` for these.

| Question | PRIMARY | Notes |
|----------|---------|-------|
| List all Attribute Layer rules | `list_carrier_exception_rules` | Full shaped rules (conditions, exclusion_list, enabled, priority) |
| Find by rule id | `find_carrier_exception_rule` | `query` = id, `match_category=RULE_ID` |
| Find by rule name | `find_carrier_exception_rule` | `query` = name, `match_category=RULE_NAME` |
| Rules for a carrier partner | `find_carrier_exception_rule` | `query` = CP_ID and `match_category=COURIER_PARTNER_ID`; or resolve name → CP_ID via `search_courier_partners` first |
| Why were Delhivery accounts gone before filters? | Resolve CP_ID → `find_carrier_exception_rule` | Check enabled rules, conditions vs shipment attrs, union of `exclusion_list` |
| What accounts does this Attribute Layer rule eliminate? | `find_carrier_exception_rule` | Read `exclusion_list`; map IDs with `get_account_info` if needed |

**Evaluation reminder:** all **enabled** rules for that CP_ID are evaluated (no short-circuit); eliminations are the **union** across matches; disabled rules do not count. Full elimination of a CP_ID means that partner never reaches Filters.

**Required clarification:** if the user provides an unlabeled numeric ID in an Attribute Layer question, ask **"Is this a rule ID or a courier partner ID (CP_ID)?"** before calling the tool. Never infer the category or search both. The user’s answer determines `match_category`.

See `15_carrier_attribute_layer.md` for pipeline position, outcome states, and differences from Filter Rules.

### Flow config & load distribution

| Question | PRIMARY | SUB-TOOLs if needed |
|----------|---------|---------------------|
| Pipeline toggles | `get_enterprise_flow_config` | `get_flow_config_flag(flag=...)` |
| Did per-filter LD apply on this order? | `fetch_rr_log` / `get_rr_log_pipeline(stage="3_clickpost_filters_rules_list")` | Read `load_distribution_applied` / `load_distribution_account_config` on the matched filter |
| Why did LD drop / keep account X? | Same RR log fields + `accounts_after_load_distribution_filter` | `get_load_distribution_filter_config` only if LD fields missing (older logs) |
| LD targets (terminal / dashboard) | `get_load_distribution_config` | pass `account_id` to filter one carrier |
| LD targets for a filter rule | Prefer RR log `load_distribution_account_config` when present | else `search_filter_rules` → `get_load_distribution_filter_config(filter_id)` |

### Serviceability

**Always use the serviceability tools for any serviceability question.** Do **not** use `run_recommendation` / `summarize_recommendation` / `get_recommendation_pipeline` to answer serviceability — those simulate full allocation (filters, OL, PBA) and are the wrong tool when the user only asks which accounts can serve a pincode or route.

| Question / phrasing | PRIMARY | Notes |
|---------------------|---------|-------|
| "Serviceable accounts for pickup X → drop Y" | `check_route_serviceability` | Pass `pickup_pincode` + `drop_pincode`; omit account filters to list all |
| "Is this route / lane / pincode pair serviceable?" | `check_route_serviceability` | Same — return `accounts` from the tool |
| "Is account X serviceable for this route?" | `check_route_serviceability` | Add `account_type_id` (preferred) or `account_name` + `courier_partner` |
| "Why was account X dropped at `2_serviceable_accounts_filter`?" | `check_route_serviceability` | Then compare to RR log `filters_ran.2_serviceable_accounts_filter` |
| "Is drop / pickup pincode Z serviceable?" | `check_pincode_serviceability` | Set `is_pickup_pincode` false for drop, true for pickup |
| COD vs PREPAID on a route | `check_route_serviceability` | Pass `order_type=COD` or `PREPAID` |
| RVP / reverse on a route | `check_route_serviceability` | Pass `is_rvp=true` |

**Route vs single pincode:** `check_route_serviceability` calls the API twice (pickup + drop). Pickup is treated as an account-id set; for shared accounts, **all drop order_types** are kept (so COD from drop is included even when pickup only listed PREPAID). Use `check_pincode_serviceability` only when one end of the route is asked about.

**Filters:** Prefer `account_type_id` from the RR log over `account_name` (many accounts share generic names like `"1"` / `"test"`). When any filter is set, the response includes `serviceable: true/false`.

**Anti-pattern:** User asks for serviceable accounts on a pickup→drop pair → agent calls `run_recommendation` → wrong. Call `check_route_serviceability` instead.

See `14_serviceability_tools.md` for full mapping, RR-log reconciliation, and dashboard links.

### Simulation & accounts

| Question | PRIMARY | SUB-TOOLs if needed |
|----------|---------|---------------------|
| Simulate **full** allocation (filters / OL / PBA ranking) | `run_recommendation` | `summarize_recommendation`, `get_recommendation_pipeline` — **not** for serviceability-only questions |
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