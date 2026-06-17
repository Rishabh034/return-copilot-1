# RR Log Analyst — Release Update

## What is RR Log Analyst?

RR Log Analyst is an AI chatbot that explains **why Clickpost's courier allocation engine (RecsV2) picked the courier(s) it did** for any shipment. Instead of manually digging through RR logs, filter rules, ordered lists, account config, and audit history, a user just asks a question in plain language and the bot investigates using live Clickpost data and returns an evidence-backed answer.

It is a domain expert on the allocation pipeline: pre-filter checks → filter rules → ordered lists → Performance Based Allocation (PBA) → rule iteration. It knows how each stage narrows or ranks the carrier pool, and it reasons over the actual `filters_ran` trace and `preference_array` from real logs.

## Who it's for

- **Support / ops teams** debugging "why did this order go to courier X?" or "why wasn't courier Y used?"
- **Solutions / account managers** explaining allocation behaviour to a brand.
- **Engineers** validating rule changes, load distribution, and flow config without reading raw logs.

## What it can do

- Look up the RR log for a shipment by reference number, AWB, or order ID (incl. date-range search).
- Explain the full allocation pipeline trace for an order and where a specific courier was dropped.
- Inspect filter rules and ordered lists (by id, name, pincode, or carrier) and read their conditions.
- Read enterprise flow config and load distribution (forward, reverse, per-filter).
- Pull order-creation request/response logs to debug CP order failures (~7-day retention).
- Check audit logs to see recent dashboard changes, and tie a setting change back to a rule.
- Re-run the recommendation engine ("what would happen now?") and compare against the historical log.
- Compare allocations across multiple shipments in one go.

## How it works

- **Conversational + agentic.** Each question runs an agent loop (up to `RR_ANALYST_MAX_ITERATIONS`): the model decides which tools to call, fetches evidence, then answers. It is not a fixed script — it adapts checks to the question.
- **Grounded in product knowledge.** A curated knowledge base (pipeline overview, filter/OL semantics, PBA, rule iteration, load distribution, audit/order-creation log behaviour) is loaded into the system prompt so answers match real RecsV2 behaviour.
- **Live data, scoped per enterprise.** Tools call internal Clickpost APIs using the caller's token; every call is automatically scoped to that enterprise/account.
- **Streaming UI.** Answers, tool activity ("Looking up the order…"), and now reasoning stream live over SSE. Conversations are stored so users can continue a thread.
- **Memory + scratchpad.** Large tool results are summarized by a secondary model and stored, so the agent can re-query specific slices instead of re-fetching huge payloads.

## What's new this release

- **Extended thinking is ON** for the main analyst model. The bot now reasons before answering (thinking budget 2048 tokens, max output 8192).
- Reasoning **streams live** to the UI in a separate dim "Thinking…" panel and is kept out of the final answer.
- Toggles: `RR_ANALYST_THINKING_ENABLED` (on/off), `RR_ANALYST_THINKING_BUDGET_TOKENS` (budget).

## Under the hood: tool catalog (42 tools)

These are the tools the agent calls to investigate. Users never call them directly — they're listed here for reference.

- **User-facing label** = text shown in chat while the tool runs.
- **PRIMARY** = call-first tools. **SUB-TOOL** = compact/drill-down variants for large results.

### Shipment / RR log lookup

| Tool | Type | User-facing label | What it does |
|---|---|---|---|
| `fetch_rr_log_by_shipment` | PRIMARY | Finding the order | Given AWB/order_id, resolves reference_number then fetches RR log. Date-range lookup; asks for dates if nothing in last N days. |
| `fetch_rr_log` | PRIMARY | Looking up the order | Full RR log when reference_number is known (order, filters_ran pipeline, full preference_array). |
| `fetch_rr_logs_batch` | PRIMARY | Looking up the orders | Full RR logs for up to 20 reference numbers to compare allocations. |
| `resolve_reference_number` | SUB-TOOL | Finding the order | Resolve AWB/order_id → reference_number only (no RR log). |
| `summarize_rr_log` | SUB-TOOL | Reviewing the order | Compact RR log overview (matched filter, top courier, total ranked). |
| `summarize_rr_logs_batch` | SUB-TOOL | Reviewing the orders | Compact summaries for many orders. |
| `get_rr_log_order` | SUB-TOOL | Reading the order details | Order/request fields only. |
| `get_rr_log_pipeline` | SUB-TOOL | Checking how the delivery company was picked | One pipeline stage from filters_ran; can check a carrier's survival. |
| `get_rr_log_ranking` | SUB-TOOL | Checking the delivery company choices | Final ranking only; filter by account/cp_name or top N. |
| `search_rr_log_courier` | SUB-TOOL | Tracing a delivery company | Trace one courier through pipeline stages. |

### Order creation & audit

| Tool | Type | User-facing label | What it does |
|---|---|---|---|
| `fetch_order_creation_log` | PRIMARY | Checking the order creation history | Order-creation request/response sent to courier + CP response + timestamps. ~7 day retention. |
| `fetch_audit_logs` | PRIMARY | Checking recent changes | Dashboard activity logs for a date range (≤7 days/call), filterable by section. |
| `fetch_audit_logs_for_rule` | PRIMARY | Checking changes to a setting | Resolves a rule, fetches audit logs around its updated_at date. |

### Rules (Filter & Ordered List)

| Tool | Type | User-facing label | What it does |
|---|---|---|---|
| `list_filter_rules` | PRIMARY | Reading the delivery settings | All Filter rules in priority order, full detail. |
| `list_ordered_list_rules` | PRIMARY | Reading the priority settings | All Ordered List rules in priority order, full detail. |
| `find_rule` | PRIMARY | Finding a setting | Find Filter/OL rule by id or name from query text. |
| `get_filter_rule` | PRIMARY | Opening a setting | Fetch a Filter/OL rule by id/name (RR-log follow-ups). |
| `get_ordered_list_rule` | PRIMARY | Opening a setting | Fetch an OL rule by id/name. |
| `find_rules_for_pincode` | PRIMARY | Finding settings for this area | Rules referencing a pickup/drop pincode. |
| `find_rules_for_carrier` | PRIMARY | Finding settings for this delivery company | Rules whose account_list includes a carrier account_id. |
| `search_filter_rules` | SUB-TOOL | Searching the settings | Filter rules by priority/name/id/pincode/enabled. |
| `search_ordered_list_rules` | SUB-TOOL | Searching the settings | Same filters, for OL rules. |
| `summarize_filter_rules` | SUB-TOOL | Reviewing the settings | Compact index of all Filter rules. |
| `summarize_ordered_list_rules` | SUB-TOOL | Reviewing the settings | Compact index of all OL rules. |

### Config (flow / load distribution)

| Tool | Type | User-facing label | What it does |
|---|---|---|---|
| `get_enterprise_flow_config` | PRIMARY | Checking the delivery setup | All flow toggles + flow_summary. Call early. |
| `get_flow_config_flag` | SUB-TOOL | Checking a setup option | Look up one flow-config flag. |
| `summarize_flow_config` | SUB-TOOL | _(none set)_ | Compact flow config. |
| `get_load_distribution_config` | PRIMARY | Checking how orders are split | Full forward load distribution (percent, daily cap per account). |
| `get_load_distribution_reverse_config` | PRIMARY | _(none set)_ | Full reverse (RVP) load distribution. |
| `get_load_distribution_filter_config` | PRIMARY | _(none set)_ | Per-filter load distribution for a Filter rule (needs filter_id). |

### Recommendation simulation

| Tool | Type | User-facing label | What it does |
|---|---|---|---|
| `run_recommendation` | PRIMARY | Running a test | Re-run recommendation engine for an order; full pipeline + preference_array. |
| `summarize_recommendation` | SUB-TOOL | Reviewing the test | Compact simulation result. |
| `get_recommendation_pipeline` | SUB-TOOL | Reviewing the test details | One pipeline stage from a simulation. |
| `get_recommendation_ranking` | SUB-TOOL | _(none set)_ | Final ranking from a simulation. |
| `search_recommendation_courier` | SUB-TOOL | _(none set)_ | Trace one courier through a simulation. |

### Accounts

| Tool | Type | User-facing label | What it does |
|---|---|---|---|
| `get_account_info` | PRIMARY | Looking up delivery company accounts | Carrier account metadata; filter by account_id/code/cp_name/cpid. |
| `search_accounts` | SUB-TOOL | Searching the accounts | Filter accounts by query substring. |
| `summarize_accounts` | SUB-TOOL | Reviewing the accounts | Total count + carrier brand names only. |

### Memory / result query (internal context tools)

| Tool | Type | What it does |
|---|---|---|
| `query_tool_result` | SUB-TOOL | Keyword-matched slices from a stored tool result by result_id. |
| `query_scratchpad_batch` | SUB-TOOL | Read one stored scratchpad batch by result_id + batch_index. |
| `query_full_scratchpad` | SUB-TOOL | Full stored scratchpad for a result_id (no length cap). |
| `query_raw_data_part` | SUB-TOOL | Read one raw fetched-data part by result_id + part_index. |

## Config flags

| Flag | Default | Purpose |
|---|---|---|
| `RR_ANALYST_MODEL` | `claude-sonnet-4-6` | Primary analyst model. |
| `RR_ANALYST_SECONDARY_MODEL` | `claude-haiku-4-5` | Scratchpad summarization model. |
| `RR_ANALYST_THINKING_ENABLED` | `true` | Extended thinking on/off. |
| `RR_ANALYST_THINKING_BUDGET_TOKENS` | `2048` | Thinking token budget. |
| `RR_ANALYST_MAX_OUTPUT_TOKENS` | `8192` | Max output tokens (must exceed thinking budget). |
| `RR_ANALYST_MAX_ITERATIONS` | `10` | Max agent loop iterations. |
| `RR_ANALYST_TOOL_TIMEOUT` | `30` | Per-tool timeout (seconds). |
| `RR_ANALYST_SCRATCHPAD_ENABLED` | `true` | Haiku scratchpad summarization. |

## Known gaps

- 6 SUB-TOOLs have no user-facing label (`summarize_flow_config`, `get_load_distribution_reverse_config`, `get_load_distribution_filter_config`, `get_recommendation_ranking`, `search_recommendation_courier`, and the `query_*` tools) — chat currently shows raw snake_case names for these.
- A stale `resolve_enterprise` friendly-label points to a tool that is not registered (`enterprise.py` is not in `_TOOL_MODULES`).
