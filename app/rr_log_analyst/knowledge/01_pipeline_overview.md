## Clickpost Recommendation/Allocation Pipeline

You are an expert on the Clickpost allocation engine ("RecsV2", `selector/service/recsv2.py`). When an order comes in, the engine picks which courier partner account(s) should handle it.

### Full Pipeline

```
Incoming Order
      ↓
[1] Active Accounts                       (1_active_accounts_filter)
[1] Out-of-Threshold / Cutoff removal     (1_accounts_out_of_threshold)
[1] Blacklisted Pincode removal           (1_blacklisted_accounts)
      ↓
[2] Serviceable Accounts                  (2_serviceable_accounts_filter)
[2.1] ODA removal                         (2_1_serviceable_oda_accounts_filter)
[2.2] Overall load-distribution cap       (2_2_..., only if enabled)
[2.4] Global serviceability blacklisting  (2_4_..., only if enabled)
      ↓
[2.x] Attribute Layer (carrier exception) — CP_ID-scoped account elimination
      ↓
[3] Filter Rules                          (3_clickpost_filters_rules_list)
      ↓
upstream carriers ("priority_list_account_names")
      ↓
┌────────────────────────────────────────────────┐
│  OL matched?                                   │
│  YES → OL × upstream intersection              │
│        → if non-empty → TERMINAL (OL order)    │
│        → if empty upstream pool → intersection │
│           is empty → serviceable fallback      │
│        → if empty but upstream non-empty → PBA │
│  NO  → PBA / serviceable fallback              │
└────────────────────────────────────────────────┘
      ↓
Final Allocation (preference_array)
```

When a filter matched with `has_serviceable_accounts: false`, upstream is `[]`, OL intersection is `[]`, and preference_array comes from the **serviceable accounts fallback** — not OL order, not meaningful PBA ranking. See `08_fl_ol_intersection_fallback.md`.

### Pipeline Stages Summary

1. **Pre-Filter Checks** (Steps 1–2.x) — System-level checks narrow the universe of available carriers before any rules run. Output = `serviceable_account_ids`. See `03_pre_filter_checks.md`.

2. **Attribute Layer (carrier exception)** — After serviceability, before Filter Rules. Rules are scoped to a **CP_ID**; when conditions match, named accounts under that partner are eliminated (`exclusion_list`). All enabled rules for that CP_ID are evaluated (no short-circuit); eliminations are the union. See `15_carrier_attribute_layer.md`.

3. **Filter Rules** — Eligibility gate. Conditions are AND logic. Priority order (lowest first), first match wins. `account_list` ∩ accounts that survived Attribute Layer = eligible carrier pool. If **no filter matches**, the upstream pool defaults to **all surviving serviceable accounts** (except for a few enterprises hard-coded to return an empty preference array). See `04_filter_rules.md`.

4. **Ordered List Rules** — Define carrier priority order (typically per route). Final allocation = intersection of OL carriers with the upstream pool, in OL's priority order. OL can fire even when no filter matched (it then intersects with all surviving serviceable accounts). See `05_ordered_lists.md`.

5. **PBA (Performance Based Allocation)** — Terminal ranking layer when OL produced nothing. Runs only when `return_serviceable_accounts` is enabled (default true). System ranks the pool by performance scores (intelligent feedback / scoring configs). See `06_pba.md`.

6. **Rule Iteration** — When a matched rule has zero serviceable accounts, the system can move to the next priority rule. Two separate per-enterprise toggles (filters vs OL). See `07_rule_iteration.md`.

### Key Concepts
- **CPID** — Carrier Partner ID. Used as grouping; **ODA** and the **Attribute Layer** are scoped per CPID. Almost all other engine operations are at account level.
- **Account ID** — Clickpost's internal ID for a specific carrier account. Rules and allocation operate at this level.
- **Account Code** — Brand's specific contract/account name (e.g. "Delhivery_Maratahalli")
- **cp_name / Carrier Name** — Courier partner brand name (e.g. "Delhivery")
- **Attribute Layer** eliminates accounts under a CP_ID before filters; **Filter** further gates eligibility; **Ordered List** ranks what remains; **PBA** ranks when no OL order exists
- **Blacklist** = exclusion filter; **Whitelist** = inclusion filter
- Engine behavior flags come from `EnterpriseRecommendationFlowConfig` (per enterprise, optionally per merchant): `filters`, `ordered_list`, `oda_filter`, `recs`, `return_serviceable_accounts`, `intelligent_feedback`, `iterate_through_rules_filters`, `iterate_through_rules_ordered_list`, `load_distribution*`, `recency_bias`, `use_rto_model`, etc.


### What the RR Log Contains
- `filters_ran` → ordered pipeline trace. Keys are prefixed by stage: `1_*` (active/threshold/blacklist), `2_*` (serviceability/ODA/caps), Attribute Layer (carrier exception — when logged), `3_*` (filter rules), `4_*` (ordered lists), `5_*` (PBA/scoring). Accounts appear as `{account_code}({carrier_name}({account_id}))`, e.g. `Delhivery_Maratahalli(Delhivery(1005979))`. For Attribute Layer config itself use `list_carrier_exception_rules` / `find_carrier_exception_rule` even if a dedicated `filters_ran` key is not yet present in older logs.
- `preference_array` → final ranked couriers with cp_name, cp_id, account_id, account_code, courier_name, priority, scores_computation, shipping_charge, delivery_type, and (when available) predicted SLA.
- `pincode_serviceable`, `return_serviceable_accounts`, `execution_time_dict`.