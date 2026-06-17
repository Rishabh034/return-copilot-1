# Performance Based Allocation (PBA)

## What is PBA?
Performance Based Allocation is the system's terminal ranking layer. It receives the upstream carrier pool and decides the final priority order based on performance signals — when the Ordered List layer produced no final list.
Performance Based Allocation is the system's terminal ranking layer. It receives the upstream carrier pool and decides the final priority order based on performance signals — when the Ordered List layer produced no final list.

## When PBA Runs
PBA runs when **both**:
1. `final_list_sorted` is empty — i.e. no OL rule matched, OR an OL matched but its intersection with the upstream pool was empty
2. `return_serviceable_accounts` is enabled in the enterprise's recommendation flow config (**default true**; a couple of enterprises are special-cased)

If OL produced a non-empty list, PBA does not run — OL's order is terminal.

```
[1] Active / Threshold / Blacklist checks
[2] Serviceability / ODA checks
## When PBA Runs
PBA runs when **both**:
1. `final_list_sorted` is empty — i.e. no OL rule matched, OR an OL matched but its intersection with the upstream pool was empty
2. `return_serviceable_accounts` is enabled in the enterprise's recommendation flow config (**default true**; a couple of enterprises are special-cased)

If OL produced a non-empty list, PBA does not run — OL's order is terminal.

```
[1] Active / Threshold / Blacklist checks
[2] Serviceability / ODA checks
        ↓
  Filter Rules
        ↓
  upstream pool ("priority_list_account_names")
  upstream pool ("priority_list_account_names")
        ↓
  ┌────────────────────────────────────────────┐
  │  OL produced non-empty list?               │
  │                                            │
  │  YES → OL × pool intersection              │
  │        → TERMINAL (final allocation)       │
  │                                            │
  │  NO  → PBA ranks the upstream pool         │
  │        → TERMINAL (final allocation)       │
  └────────────────────────────────────────────┘
  ┌────────────────────────────────────────────┐
  │  OL produced non-empty list?               │
  │                                            │
  │  YES → OL × pool intersection              │
  │        → TERMINAL (final allocation)       │
  │                                            │
  │  NO  → PBA ranks the upstream pool         │
  │        → TERMINAL (final allocation)       │
  └────────────────────────────────────────────┘
```

## What PBA Receives
| Scenario | Input to PBA |
|----------|-------------|
| Filter matched, OL did not match | Filter's eligible pool (∩ serviceable) |
| Neither filter nor OL matched | All serviceable accounts from pre-filter checks |
| OL matched but intersection with pool was empty | Depends on upstream pool — see `08_fl_ol_intersection_fallback.md` |

## How PBA Decides Priority (three variants in `recsv2.py`)

1. **Reverse weightage & feedback** — for RVP (reverse) orders when `reverse_wnf` flag is on. Sorts by configured weightages. RR log key: `5_accounts_after_performance_based_allocation`.
2. **Intelligent feedback** — when `intelligent_feedback` flag is on (**model default: true**). `IntelligentFeedbackService.get_scores` scores each account using performance signals (delivery performance, SLA, pricing, RTO model if `use_rto_model`, recency bias if `recency_bias`, transport mode, cohorts) and sorts by score. RR log key: `5_accounts_after_intelligent_performance_allocation`.
3. **Legacy scoring config** — otherwise. Uses `EnterpriseRecommendationConfig` (scoring params like RTO, SLA, PRICING, FAILED_SHIPMENTS, etc.) + `RecommendationScoringConfig` per-point values to compute scores.

Scores appear per account in `preference_array[].scores_computation`.

## Key Principles
- PBA is **not a fallback** in the degraded sense — it is a full terminal layer
- OL is **rule-based terminal** — explicit priority defined by the brand
- PBA is **performance-based terminal** — system decides priority autonomously
- If `return_serviceable_accounts` is false and nothing matched → empty preference array
- If `return_serviceable_accounts` is false and nothing matched → empty preference array

---

## Summary

| Terminal | Trigger | Priority Source |
|----------|---------|-----------------|
| OL | OL rule matched with non-empty intersection | Brand-defined explicit order |
| PBA | No OL list produced (and `return_serviceable_accounts` true) | System-decided based on performance scores |
| OL | OL rule matched with non-empty intersection | Brand-defined explicit order |
| PBA | No OL list produced (and `return_serviceable_accounts` true) | System-decided based on performance scores |