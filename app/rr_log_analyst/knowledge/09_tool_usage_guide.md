## Tool Usage Guide

**Pattern:** Always try the **PRIMARY** tool first — it returns the full, properly shaped result. If the response is too large or you only need a slice, fall back to **SUB-TOOLs** (search_*, summarize_*, get_*_pipeline, etc.).

### RR logs

| Question | PRIMARY | SUB-TOOLs if needed |
|----------|---------|---------------------|
| Why was X allocated? | `fetch_rr_log` | `summarize_rr_log`, `get_rr_log_pipeline`, `get_rr_log_ranking` |
| Why was courier Y not selected? | `fetch_rr_log` | `search_rr_log_courier`, `get_rr_log_pipeline(stage=...)` |
| Compare orders | `fetch_rr_logs_batch` | `summarize_rr_logs_batch` |

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

### Audit logs

| Question | PRIMARY | SUB-TOOLs if needed |
|----------|---------|---------------------|
| Who changed rules? | `fetch_audit_logs` | `search_audit_logs(query=...)`, `summarize_audit_logs` |

Always fetch actual data before explaining. Never guess without evidence.
