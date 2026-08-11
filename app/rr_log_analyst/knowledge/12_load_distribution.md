
# Load Distribution (LD)

Load distribution reorders or caps carriers to hit target shipment shares or daily caps. It can run at two points in the pipeline depending on enterprise config.

## When LD runs

| Mode | Flow flag | RR log stage key | Config tool |
|------|-----------|------------------|-------------|
| Terminal forward LD | `load_distribution=true` | `accounts_after_load_distribution` | `get_load_distribution_config` |
| Terminal reverse LD | `load_distribution_reverse=true` | `accounts_after_load_distribution` | `get_load_distribution_reverse_config` |
| Per-filter lane LD | `load_distribution_filter=true` | `accounts_after_load_distribution_filter` | `get_load_distribution_filter_config(filter_id)` |

Check flags with `get_enterprise_flow_config` (or `get_flow_config_flag`) before investigating LD in an RR log — except when the RR log itself already carries per-filter LD runtime fields (see below); then prefer the log.

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

### RR log fields on the matched filter (preferred evidence)

Newer RR logs embed per-filter LD state on each entry in `filters_ran.3_clickpost_filters_rules_list[]`:

| Field | Meaning |
|-------|---------|
| `allocation_type` | `"PERCENTAGE"` or `"ABSOLUTE_CAPPING"` — how `allocation_value` is interpreted |
| `load_distribution_filter_enabled` | Whether this filter rule has per-filter LD turned on |
| `load_distribution_applied` | Whether LD actually ran and reshaped the pool for **this** request |
| `load_distribution_account_config` | Per-account snapshot at decision time (may be present even when `applied` is false) |

Each account under `load_distribution_account_config` is keyed like `AccountCode(Carrier(account_id))` and typically includes:

| Sub-field | Meaning |
|-----------|---------|
| `account_id` | Internal account id |
| `allocation_value` | Target share (%) or absolute daily/lane cap |
| `enterprise_account_lane_shipment_count` | Shipments already counted on this lane for the account |
| `deviation` | Headroom vs target: **positive** = under target/cap (room left); **negative** = over target/cap |

After LD, compare `3_clickpost_define_and_custom_filters_accounts` (pre-LD eligible pool) vs `accounts_after_load_distribution_filter` / `priority_list_account_names` (post-LD pool).

### Decision tree (read the RR log first)

For the matched filter entry in `3_clickpost_filters_rules_list`:

1. **LD fields absent** (older logs) → fall back to config tools: `get_enterprise_flow_config` → resolve filter id → `get_load_distribution_filter_config(filter_id)` → compare `accounts_after_load_distribution_filter`.
2. **`load_distribution_filter_enabled: false`** → per-filter LD is not configured on this rule; do **not** attribute drops/reordering to LD.
3. **`enabled: true`, `load_distribution_applied: false`** → LD was configured on the filter but did **not** apply for this request. If `load_distribution_account_config` is present, cite it as the intended targets/counts; if absent, fetch `get_load_distribution_filter_config`. Do not claim LD removed or reordered carriers.
4. **`load_distribution_applied: true`** → explain the outcome from the log: `allocation_type`, per-account `allocation_value` / `enterprise_account_lane_shipment_count` / `deviation`, and how that produced `accounts_after_load_distribution_filter`. Config tools are optional (confirm dashboard targets) — not required to explain **this** order.

**Example:** filter eligible pool = XpressBeesNCP + AmazonCP; XpressBees `deviation: -618` (over absolute cap 600 with count 1218) and Amazon `deviation: +6013` (under cap 9000) with `load_distribution_applied: true` → only Amazon remains after LD. That is sufficient evidence without calling the LD config tool.

**Investigation steps (newer logs):**

1. `fetch_rr_log` / `summarize_rr_log` / `get_rr_log_pipeline(stage="3_clickpost_filters_rules_list")`
2. Apply the decision tree above using `load_distribution_*` fields
3. Cross-check `accounts_after_load_distribution_filter`
4. Only if LD fields are missing or you need dashboard confirmation → `get_enterprise_flow_config` + `get_load_distribution_filter_config(filter_id)`

**Investigation steps (older logs / fields missing):**

1. `get_enterprise_flow_config` → confirm `load_distribution_filter=true`
2. `fetch_rr_log` or `summarize_rr_log` → get matched filter rule name
3. `search_filter_rules` → confirm filter id
4. `get_load_distribution_filter_config(filter_id)` → per-account allocation_value targets
5. `get_rr_log_pipeline(stage="accounts_after_load_distribution_filter")` — post-LD account order

## Quick reference

| Question | Tool / source |
|----------|----------------|
| "Is load distribution enabled?" | `get_enterprise_flow_config` or `get_flow_config_flag` |
| "Did per-filter LD apply on this order?" | RR log: `3_clickpost_filters_rules_list[].load_distribution_applied` |
| "Why did LD drop / keep account X?" | RR log: `load_distribution_account_config` + `accounts_after_load_distribution_filter` (config tool only if fields missing) |
| "What are the LD targets per account?" | Log config snapshot if present; else `get_load_distribution_config` / `get_load_distribution_filter_config` |
| "What LD config applies to filter rule X?" | `search_filter_rules(rule_id=...)` → `get_load_distribution_filter_config(filter_id)` |

## Rules of thumb

1. For **per-filter LD on a specific order**, read `3_clickpost_filters_rules_list` first — prefer `load_distribution_applied` / `load_distribution_account_config` over config tools when present.
2. Call `get_enterprise_flow_config` when LD fields are missing, for terminal LD, or when asking about enterprise-wide toggles (not needed solely to explain an order whose log already has `load_distribution_applied`).
3. For terminal LD questions → `get_load_distribution_config` (or reverse variant); pass `account_id` when asking about one carrier.
4. For filter-stage LD when log fields are incomplete → identify the matched filter id, then `get_load_distribution_filter_config`.
5. Cross-reference with `accounts_after_load_distribution_filter` / `get_rr_log_pipeline` — never explain LD outcomes without log evidence.
6. Use `get_account_info` to resolve account names when config only shows account_ids.
7. Pass `account_id` to LD config tools when you only need one carrier's entry — avoids dumping the full config.
8. Never invent LD as the cause when `load_distribution_filter_enabled` is false, `load_distribution_applied` is false, or the LD fields are absent and stage keys do not show a post-LD change.
