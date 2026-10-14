## Tool Usage Guide

| Question | PRIMARY tool | Do NOT also call |
|----------|--------------|------------------|
| Find this return/exchange | `get_return_exchange_feed` | — |
| What is the current status? | `get_return_item_status` (shipment key comes from the feed row or tracking link; otherwise the feed snapshot) | — |
| What happened on this case? | `get_return_and_refund_logs` | — |
| Why is refund stuck? | `get_payment_reversal_info` (+ refund logs) | all config tools |
| Refund health for enterprise | `get_refund_summary` | — |
| What R&E settings are on? / broad config | **ONLY** `get_enterprise_return_exchange_configs` (token selects the enterprise; do not ask for a domain) | window / refund / rejection / warehouse / QC tools |
| Return window only | `get_return_window_config` | enterprise configs + other configs |
| Exchange window only | `get_exchange_window_config` | enterprise configs + other configs |
| Refund mode / store credit / gift card only | `get_return_refund_config` | other config tools |
| Why non-returnable / rejected? | `get_rejection_config` (+ case status) | unrelated config tools |
| Warehouse only | `get_warehouse_config` | other config tools |
| QC only | `get_qc_door_step_config` | other config tools |
| Exchange products available | `get_exchange_catalogue` (token selects the enterprise; domain only if the user typed one) | — |
| Return analytics | `get_rne_overview_return_stats` (dates DD-MM-YYYY) | — |

### Config tool budget (hard rule)
- Broad settings question → **1 tool**: `get_enterprise_return_exchange_configs`.
- Specific settings question → **1 tool**: the matching specific config tool.
- Never call all config tools in parallel for one question.
- Parallel tool calls are for case assist (feed + status + logs), not for config fan-out.

Always fetch before explaining.
