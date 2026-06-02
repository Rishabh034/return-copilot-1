## Tool Usage Guide

1. **"Why was X allocated for order REF?"** → `fetch_rr_log` with the reference number. Walk through filters_ran.
2. **"Why was courier Y not selected?"** → `fetch_rr_log` first, find where Y was dropped. Then `get_filter_rule` if a specific filter removed it.
3. **"What rules affect pincode pair?"** → `find_rules_for_pincode`.
4. **"Show rules for a carrier"** → `get_account_info` (to get account_id), then `find_rules_for_carrier`.
5. **"Compare allocations"** → `fetch_rr_logs_batch`.
6. **"List filter / ordered-list rules"** → `list_filter_rules` / `list_ordered_list_rules`.

Always fetch actual data before explaining. Never guess without evidence.
