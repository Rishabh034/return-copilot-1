# Rule Iteration

## What is Rule Iteration?
Rule Iteration is a feature that allows the system to continue evaluating subsequent Filter/OL rules when a matched rule has **no serviceable accounts** in its `account_list` (all of them were dropped by pre-filter checks).

Without this feature, the system stops on the first matched rule even if that rule produces no usable carriers. With rule iteration enabled, the system moves to the next rule in priority order and tries again.

## Problem It Solves
Previously:
1. Rule at Priority 1 matches conditions
2. All carriers in that rule's `account_list` were dropped upstream (non-serviceable, ODA, blacklisted, inactive, out of threshold)
3. System stops → order gets no carrier from rules

With Rule Iteration:
1. Rule at Priority 1 matches conditions
2. No serviceable account in its list → rule skipped
3. System moves to Priority 2 rule, evaluates conditions
4. If Priority 2 also yields nothing serviceable → move to Priority 3
5. Continue until a rule produces serviceable carriers OR all rules are exhausted

## Trigger Condition for Moving to Next Rule
A matched rule is skipped when **none of its `account_list` is in the serviceable set** (`has_serviceable_accounts: false`). The serviceable set already excludes accounts dropped for:
- **Non-serviceability** — carrier does not serve the route/order type
- **Blacklisted pincodes** — pincode is blocked for the account
- **ODA** — drop pincode marked ODA for the carrier
- **Inactive accounts** — account is disabled
- **Out-of-threshold** — order outside the account's threshold/cutoff window

(With load distribution filters, iteration can also move forward when the LD allocation for the matched rule comes up empty.)

## Scope & Configuration
Two **independent** boolean flags on `EnterpriseRecommendationFlowConfig`, both **default false** (must be explicitly enabled per enterprise, optionally per merchant):
- `iterate_through_rules_filters` — iteration for Filter rules
- `iterate_through_rules_ordered_list` — iteration for Ordered List rules

An enterprise can have one enabled and not the other.

## Visibility in the RR Log
Every matched rule (including skipped ones) is recorded with its evaluation order:
- Filters: `filters_ran.3_clickpost_filters_rules_list` — list of `{sequence, rule_name, rule_priority, has_serviceable_accounts, allocation_type}`
- OLs: `filters_ran.4_ordered_list_rules_list` — list of `{sequence, rule_name, rule_priority, has_serviceable_accounts}`

A rule with `has_serviceable_accounts: false` followed by another entry with a higher `sequence` = rule iteration happened.

## Behaviour Summary

| Scenario | Without Rule Iteration | With Rule Iteration |
|----------|----------------------|---------------------|
| Rule 1 matches, 0 serviceable accounts | Stop — empty pool | Move to Rule 2 |
| Rule 2 matches, 0 serviceable accounts | N/A | Move to Rule 3 |
| Rule N matches, serviceable carriers found | N/A | Allocate from Rule N |
| All rules exhausted, 0 serviceable carriers | Empty pool | Empty pool |

When the pool ends up empty: if `return_serviceable_accounts` is true (default), the PBA stage still runs but over an empty pool → empty preference array; the RR log trace shows which rules were tried and why.

## Key Notes
1. Rule iteration does **not** change condition matching logic — a rule still needs its conditions to pass before it can yield carriers
2. Iteration only triggers when a **matched** rule has no serviceable accounts — not on condition mismatch (non-matching rules are skipped anyway)
3. The priority order is still respected — system always tries lower priority number first
4. Two separate flags — filters and OL iteration are enabled independently, both off by default
