# Allocation Pipeline (RecsV2)

Recommendation engine picks which courier account(s) handle an order. Stages below are in execution order; each stage includes its evidence and edge cases. Accounts in `filters_ran` render as `AccountCode(Carrier(account_id))`. Rule condition/operator schema is in the rule schema doc.

```
Incoming Order
  → [1] Active / out-of-threshold / blacklisted pincode
  → [2] Serviceable (+ ODA, overall LD cap, global blacklist) → serviceable_account_ids
  → [2.x] Attribute Layer (CP_ID eliminations)
  → [3] Filter Rules (+ per-filter LD) → priority_list_account_names
  → [4] Ordered List
       YES (non-empty ∩) → OL order TERMINAL
       NO  → [5] PBA (+ terminal LD)
  → preference_array
```

An account removed earlier can never be allocated later. Never assume a flow-config flag — check with `get_enterprise_flow_config`.

---

## Stage 1–2 — Pre-filter checks

System narrowing before Attribute Layer / rules. Output: `serviceable_account_ids`.

**Active:** `active=True` accounts for the enterprise (forward/RVP match). Inactive excluded entirely → `1_active_accounts_filter`.

**Serviceability (one call, three outputs):**
- remaining → `2_serviceable_accounts_filter`
- out of threshold/cutoff → `1_accounts_out_of_threshold` (removed)
- brand-blacklisted drop pincode + order type → `1_blacklisted_accounts` (removed)

Must upload serviceability per account/`orderType`. No upload = dropped. A few enterprises skip serviceability (hard-coded). Verify live with `check_route_serviceability`.

**ODA (`oda_filter`, default off)** → `2_1_serviceable_oda_accounts_filter`. Stored per **CPID + enterprise + pincode** (not account) — removes **all** accounts of that carrier. Scan-driven mark; manual removal via support ticket.

**Overall LD cap** (`load_distribution_overall`) → removes exhausted accounts → `2_2_serviceable_accounts_without_exhausted_overall_cap`.

**Global serviceability blacklist** (service + `global_serviceability_blacklisting_enabled=True`) → `2_4_global_blacklisting_serviceability_filter`.

Enterprise-specific keys may appear: `1_accounts_missing_cod_or_prepaid_support`, `1_accounts_exceeding_invoice_value_limit`, `2_3_serviceable_accounts_after_enterprise_custom_key_filter` (TataCliq), `2_removed_accs_for_ndd_filter`, `2_account_removed_missing_sla` / `_transport_mode` / `_tat`.

---

## Stage 2.x — Attribute Layer (Carrier Exception)

CP_ID-scoped elimination between serviceability and Filters. Dashboard: **Carrier attribute rules**; tools: `list_carrier_exception_rules` — **not** `list_rules`/`find_rule`. Supplements Filters; does not change Active/Serviceability. Survivors pass through unchanged. No rules for a CP_ID = transparent.

- Scope: one CP_ID only (e.g. Delhivery=4).
- Conditions: shipment attrs (same shape/ops as Filter/OL); AND.
- `exclusion_list`: Account IDs under that CP_ID removed on match (accounts are never condition inputs).
- **Non-short-circuit:** all enabled rules for the CP_ID evaluated; union of eliminations removed. `priority` is order-only.
- Outcomes: all pass / partial / full elimination of that carrier.

Investigate: resolve CP_ID (`search_courier_partners`) → `list_carrier_exception_rules` (omit query for all; or `query` + `match_category` = `RULE_ID`/`COURIER_PARTNER_ID`/`RULE_NAME`) → check `enabled`, conditions vs `user_request`, `exclusion_list`. On newer logs also read `filters_ran.2_3_1_attribute_rules_matched` (older logs may lack it — use config). Filter/OL/PBA only see survivors.

**Unlabeled numeric ID:** ask rule ID vs CP_ID before calling. Never infer or search both.

---

## Stage 3 — Filter Rules

Eligibility gate. Loads `rule_type IN ('CLICKPOST_DEFINED','ENTERPRISE_CUSTOM','FILTER')`, `enabled=True`, by `priority`. Runs if `filters` on.

- AND conditions; **first match wins** (unless iteration below).
- Pool = `account_list` ∩ Attribute Layer survivors; `account_list` order preserved but is **not** final order when PBA re-ranks.
- Filter matched, no OL → pool to PBA.
- No filter → upstream = all surviving serviceable accounts (except GlobalBees/Savana/Urbanic → empty `preference_array` if no filter and no OL).
- Filter matched, zero serviceable (`has_serviceable_accounts: false`) → `priority_list_account_names=[]` → empty OL upstream (classic misread; Stage 4).

**Evidence:** `clickpost_defined_and_custom_rule_matched`; `3_clickpost_filters_rules_list` (`sequence`, `rule_name`, `rule_priority`, `has_serviceable_accounts`, + LD fields); `3_clickpost_define_and_custom_filters_accounts` (pre-LD); `accounts_after_load_distribution_filter`; `priority_list_account_names`.

### Rule iteration (Filters and OL)
Skip a **matched** rule with `has_serviceable_accounts: false` and continue to next priority. Also when per-filter LD yields empty. Flags (default **false**, independent): `iterate_through_rules_filters`, `iterate_through_rules_ordered_list`. Evidence: skipped entry then higher `sequence` in `3_clickpost_filters_rules_list` / `4_ordered_list_rules_list`. Does not change condition matching; only matched-empty triggers it. Exhausted empty pool + `return_serviceable_accounts` → empty `preference_array` with tried rules in the trace.

### Per-filter load distribution
Runs when `load_distribution_filter=true` **and** matched filter has `load_distribution_filter_enabled=true`. Prefer log fields on `3_clickpost_filters_rules_list[]` over config tools:

| Field | Meaning |
|-------|---------|
| `allocation_type` | `PERCENTAGE` or `ABSOLUTE_CAPPING` |
| `load_distribution_filter_enabled` | LD on for this rule |
| `load_distribution_applied` | LD reshaped **this** request |
| `load_distribution_account_config` | Snapshot: `allocation_value`, `enterprise_account_lane_shipment_count`, `deviation` (**+**=under target/cap, **−**=over) |

Compare pre-LD `3_clickpost_define_and_custom_filters_accounts` vs post-LD `accounts_after_load_distribution_filter` / `priority_list_account_names`.

1. Fields absent (old logs) → flow config → filter id → `get_load_distribution_config(mode='filter', filter_id=...)`.
2. `load_distribution_filter_enabled: false` → do **not** blame LD.
3. enabled, `applied: false` → configured but did not apply; cite config snapshot if present; don't claim LD dropped/reordered.
4. `applied: true` → explain from log (`allocation_type`, values, counts, deviation → post-LD pool). Config optional.

Never invent LD when enabled/applied is false or fields absent with no post-LD change. Pass `account_id` to LD config for one carrier; `get_account_info` for names.

---

## Stage 4 — Ordered List

Priority order (usually per route). `rule_type='ORDERED_LIST'`; runs if `ordered_list` on. Final = OL ∩ upstream pool, preserving OL order (index 0 = P1). Absent upstream carriers silently dropped. Intersection is vs `priority_list_account_names`, **not** full serviceable set — empty filter pool ⇒ empty ∩ even if OL lists serviceable carriers.

- No filter match → upstream = all serviceable; OL still can fire.
- Non-empty ∩ → OL terminal; PBA skips.
- Empty ∩ → not "no allocation"; fall back to PBA over upstream, or serviceable fallback if upstream empty.
- No OL → upstream to PBA; `4_ordered_list_account_names_filters_applied: false`.
- Iteration: Stage 3. Common conditions: `pickup_pincode IN` + `drop_pincode IN`.

**Evidence:** `order_list_matched_rule`, `4_ordered_list_rules_list`, `4_ordered_list_account_names_filters_applied`, `ordered_list_filter_accounts`.

### Empty-intersection fallback (common misread)
Filter **and** OL matched, but allocation ignores OL order (e.g. Delhivery #1 while OL puts it last). Not a PBA bug / not OL ignored — filter pool was empty:

Filter matched, `has_serviceable_accounts: false` → `priority_list_account_names=[]` → OL ∩ = `[]` → `ordered_list_filter_accounts=[]` → with `return_serviceable_accounts`, `preference_array` = serviceable fallback.

Log: filter `has_serviceable_accounts: false`; filter accounts + upstream `[]`; OL applied true; `ordered_list_filter_accounts=[]`; `preference_array` = serviceable order. (OL can show `has_serviceable_accounts: true` and still empty-∩.)

Explain: filter conditions matched but its carriers weren't serviceable; OL priority needs non-empty upstream; #1 is fallback, not OL/PBA choice. Fix: put serviceable carriers on the matched **Filter** `account_list` (or widen until upstream ∩ OL non-empty). Never say OL ranked first when intersection empty; never call it intelligent PBA without scores/`intelligent_feedback`; always say **why** OL ∩ was empty.

---

## Stage 5 — PBA

Terminal ranking when OL produced no list. Not a degraded fallback — performance-based terminal vs OL's rule-based terminal.

Runs when `final_list_sorted` empty **and** `return_serviceable_accounts` (default true). Non-empty OL → PBA skips. Flag false + nothing matched → empty `preference_array`.

| Scenario | PBA input |
|----------|-----------|
| Filter matched, no OL | Filter eligible pool |
| Neither matched | All serviceable |
| OL matched, empty ∩ | Upstream pool; if that empty → serviceable fallback, not a ranking |

| Variant | When | Evidence |
|---------|------|----------|
| Reverse weightage | RVP + `reverse_wnf` | `5_accounts_after_performance_based_allocation` |
| Intelligent feedback | `intelligent_feedback` (default true) — scores on delivery/SLA/pricing/RTO (`use_rto_model`)/recency (`recency_bias`)/transport/cohorts | `5_accounts_after_intelligent_performance_allocation` |
| Legacy | else — `EnterpriseRecommendationConfig` + `RecommendationScoringConfig` | — |

Scores: `preference_array[].scores_computation`. Absent scores with `recs`/`intelligent_feedback` off = serviceable fallback — **not** intelligent selection.

**Terminal LD** after PBA when no OL and `load_distribution` / `load_distribution_reverse` on → `accounts_after_load_distribution`. Compare counts vs targets/caps via `get_load_distribution_config(mode='forward'|'reverse')`; confirm flag; compare `priority_list_account_names` vs post-LD list.

---

## Final allocation & flags

`preference_array`: ranked list with `cp_name`, `cp_id`, `account_id`, `account_code`, `courier_name`, `priority`, `scores_computation`, `shipping_charge`, `delivery_type`, SLA when present.

| Flag | Effect | Default |
|------|--------|---------|
| `filters` / `ordered_list` | Run Filter / OL layers | on |
| `oda_filter` | ODA removal | **off** |
| `return_serviceable_accounts` | PBA / serviceable fallback fills `preference_array` | true |
| `intelligent_feedback` | Intelligent PBA scoring | true |
| `iterate_through_rules_filters` / `_ordered_list` | Rule iteration | **false** |
| `load_distribution` / `_reverse` | Terminal LD | off |
| `load_distribution_filter` | Per-filter LD | off |
| `load_distribution_overall` | Overall LD cap (2.2) | off |
| `recs`, `recency_bias`, `use_rto_model`, `reverse_wnf` | PBA scoring | varies |

**Vocabulary:** Attribute Layer eliminates by CP_ID; Filter gates eligibility; Ordered List ranks; PBA ranks when no OL order. Blacklist=exclusion; whitelist=inclusion.
