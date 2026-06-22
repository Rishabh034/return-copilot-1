
# Load Distribution (LD)

Load distribution reorders or caps carriers to hit target shipment shares or daily caps. It can run at two points in the pipeline depending on enterprise config.

## When LD runs

| Mode | Flow flag | RR log stage key | Config tool |
|------|-----------|------------------|-------------|
| Terminal forward LD | `load_distribution=true` | `accounts_after_load_distribution` | `get_load_distribution_config` |
| Terminal reverse LD | `load_distribution_reverse=true` | `accounts_after_load_distribution` | `get_load_distribution_reverse_config` |
| Per-filter lane LD | `load_distribution_filter=true` | `accounts_after_load_distribution_filter` | `get_load_distribution_filter_config(filter_id)` |

Check flags with `get_enterprise_flow_config` (or `get_flow_config_flag`) before investigating LD in an RR log.

## Terminal LD (account-level)

Used when no Ordered List matched (or OL stage disabled) and terminal LD is on. Compares today's shipment counts per account against target percentages and daily caps from `RecommendationLoadDistribution` (forward) or `ReverseRecommendationLoadDistribution` (reverse).

**Config source:** `RecommendationLoadDistribution` (forward) or `ReverseRecommendationLoadDistribution` (reverse). Fetch with `get_load_distribution_config` or `get_load_distribution_reverse_config`.

- Without `account_id`: compact summary of all entries.
- With `account_id`: full entry for that carrier only.

**Investigation steps:**

1. `get_enterprise_flow_config` → confirm `load_distribution=true` (or reverse variant)
2. `get_load_distribution_config` → see target % and caps per account (pass `account_id` to filter one carrier)
3. `fetch_rr_log` or `get_rr_log_pipeline(stage=...)` — compare `priority_list_account_names` vs `accounts_after_load_distribution`

## Per-filter LD (lane-level)

Runs at the Filter stage when `load_distribution_filter=true` and the matched filter has `load_distribution_filter_enabled=true`.

**Config source:** `RecommendationLoadDistributionV2` keyed by `filter_id`. Fetch with `get_load_distribution_filter_config(filter_id)`.

- `allocation_value` meaning depends on the filter's `allocation_type`: **PERCENTAGE** (target % share on this lane) or **ABSOLUTE_CAPPING** (daily shipment cap).
- Pass `account_id` to get one carrier's entry only.

**Investigation steps:**

1. `get_enterprise_flow_config` → confirm `load_distribution_filter=true`
2. `fetch_rr_log` or `summarize_rr_log` → get `matched_filter_rule`
3. `search_filter_rules(rule_id=...)` → confirm filter id
4. `get_load_distribution_filter_config(filter_id)` → see per-account allocation_value targets
5. `get_rr_log_pipeline(stage="accounts_after_load_distribution_filter")` — see post-LD account order

## Quick reference

| Question | Tool |
|----------|------|
| "Is load distribution enabled?" | `get_enterprise_flow_config` or `get_flow_config_flag` |
| "What are the LD targets per account?" | `get_load_distribution_config` (forward) or `get_load_distribution_reverse_config` (RVP) |
| "Why did LD reorder carriers for this order?" | `fetch_rr_log` + LD config tool (+ `get_rr_log_pipeline` if log is large) |
| "What LD config applies to filter rule X?" | `search_filter_rules(rule_id=...)` → `get_load_distribution_filter_config(filter_id)` |

## Rules of thumb

1. Always call `get_enterprise_flow_config` first to know which LD mode(s) are active.
2. For terminal LD questions → `get_load_distribution_config` (or reverse variant); pass `account_id` when asking about one carrier.
3. For filter-stage LD → identify the matched filter id, then `get_load_distribution_filter_config`.
4. Cross-reference with `get_rr_log_pipeline` — never explain LD outcomes without log evidence.
5. Use `get_account_info` to resolve account names when config only shows account_ids.
6. Pass `account_id` to LD config tools when you only need one carrier's entry — avoids dumping the full config.
