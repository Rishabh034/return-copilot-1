# Rule Iteration

## What is Rule Iteration?
Rule Iteration is a feature that allows the system to continue evaluating subsequent FL/OL rules when a matched rule yields an empty preference array (zero valid carrier partners after all checks).

Without this feature, the system halts on the first matched rule even if that rule produces no usable carriers. With rule iteration enabled, the system moves to the next rule in priority order and tries again.

## Problem It Solves
Previously:
1. Rule at Priority 1 matches conditions
2. All carriers in that rule's output are dropped (non-serviceable, ODA, blacklisted, inactive)
3. System stops → order gets no carrier assigned

With Rule Iteration:
1. Rule at Priority 1 matches conditions
2. All carriers dropped → empty preference array
3. System moves to Priority 2 rule, evaluates conditions
4. If Priority 2 also yields empty → move to Priority 3
5. Continue until a rule produces valid carriers OR all rules are exhausted

## Trigger Conditions for Moving to Next Rule
A rule is skipped (iteration moves forward) when the preference array is empty due to any of:
- **Non-serviceability** — carrier does not serve the drop pincode
- **Blacklisted pincodes** — pincode is blocked for the carrier
- **ODA (Out of Delivery Area)** — pincode marked as ODA for the carrier
- **Inactive accounts** — carrier account is disabled

## Scope
Rule iteration applies to both **Filter Rules (FL)** and **Ordered List Rules (OL)**.

## Configuration
- Feature is **togglable** — Clickpost/administrators can enable or disable it per enterprise
- Not on by default — requires explicit enablement

## Visibility
- When rule iteration occurs, it is **surfaced in the recommendation response** so the caller can see which rules were evaluated
- Detailed logs are captured: which rule was evaluated, why it resulted in empty preference array

## Behaviour Summary

| Scenario | Without Rule Iteration | With Rule Iteration |
|----------|----------------------|---------------------|
| Rule 1 matches, 0 carriers after checks | Stop, no allocation | Move to Rule 2 |
| Rule 2 matches, 0 carriers after checks | N/A | Move to Rule 3 |
| Rule N matches, valid carriers found | N/A | Allocate from Rule N |
| All rules exhausted, 0 carriers | No allocation | TBD — details pending (depends on Return Serviceable Accounts True/False behaviour) |

## Key Notes
1. Rule iteration does **not** change condition matching logic — a rule still needs its conditions to pass before it can yield carriers
2. Iteration only triggers on **empty preference array after pre-filter checks**, not on condition mismatch
3. The priority order is still respected — system always tries lower priority number first
4. Feature must be **explicitly enabled** per enterprise — not on by default
