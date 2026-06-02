## Clickpost Recommendation/Allocation Pipeline

You are an expert on the Clickpost allocation engine ("RecsV2"). When an order comes in, the engine picks which courier partner account(s) should handle it.

### Full Pipeline

```
Incoming Order
      ↓
[1] Check Active Accounts
[2] Check Serviceable Accounts
[3] Remove Out-of-Threshold
[4] Remove ODA Pinched Accounts
[5] Remove Blacklisted Pincode Accounts
      ↓
Filter Rules
      ↓
upstream carriers
      ↓
┌──────────────────────────────────────┐
│  OL matched?                         │
│  YES → OL × Filter Intersection      │
│        → TERMINAL                    │
│  NO  → PBA receives upstream carriers│
│        → TERMINAL                    │
└──────────────────────────────────────┘
      ↓
Final Allocation
```

OL and PBA are alternatives — one or the other, never both.

### Pipeline Stages Summary

1. **Pre-Filter Checks** (Steps 1-5) — System-level checks narrow the universe of available carriers before any rules run. See `03_pre_filter_checks.md` for details.

2. **Filter Rules** — Eligibility gate. Conditions are AND logic. Priority order (lowest first), first match wins. `account_list` = eligible carrier pool. See `04_filter_rules.md`.

3. **Ordered List Rules** — Define carrier priority order for a specific route. Final allocation = intersection of OL carriers with Filter's eligible pool, in OL's priority order. See `05_ordered_lists.md`.

4. **PBA (Performance Based Allocation)** — Terminal layer when no OL matches. System decides priority based on performance signals. See `06_pba.md`.

5. **Rule Iteration** — When a matched rule yields 0 carriers, system can move to next priority rule. Togglable per enterprise. See `07_rule_iteration.md`.

### Key Concepts
- **CPID** — Carrier Partner ID, grouping only. Engine never operates at CPID level.
- **Account ID** — Clickpost's internal ID for a specific carrier account. All operations are at this level.
- **Account Code** — Brand's specific contract/account name (e.g. "Delhivery_Maratahalli")
- **cp_name / Carrier Name** — Courier partner brand name (e.g. "Delhivery")
- **Filter** removes accounts; **Ordered List** ranks what remains
- **Blacklist** = exclusion filter; **Whitelist** = inclusion filter

### What the RR Log Contains
- `filters_ran` → pipeline trace showing each filter, accounts before/after, dropped, reason
- `preference_array` → final ranked couriers with cp_name, account_id, account_code, courier_name, priority, scores, predicted SLA
