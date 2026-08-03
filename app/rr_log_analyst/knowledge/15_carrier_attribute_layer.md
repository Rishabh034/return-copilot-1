# Attribute Layer (Carrier Exception / Carrier Attribute Rules)

## What is the Attribute Layer?
The Attribute Layer is a **pre-filter, carrier-partner-scoped elimination step** inserted between the serviceability check and the existing Filter Rules layer. Brands call it **Carrier attribute rules** in the dashboard (Filters section). Internally / in tools it may appear as **carrier exception** or **attribute-rules-config**.

It exists to stop **rule explosion**: carrier-specific elimination conditions (e.g. "if invoice value < ₹3,000, eliminate all Delhivery accounts") used to be duplicated across every general Filter rule. The Attribute Layer lets brands express that once, scoped to a **CP_ID**, before general filters run.

This layer **supplements** Filter Rules — it does not replace them. It does **not** change Active Accounts or Serviceability. Cluster-based conditions and account-level *input* conditions are out of scope for the current release.

## Position in the Pipeline
```
Incoming Order
      ↓
[1] Active Accounts / threshold / blacklist
      ↓
[2] Serviceable Accounts (+ ODA / LD cap / global blacklist as configured)
      ↓
[2.x] Attribute Layer (carrier exception)   ← THIS LAYER
      ↓
[3] Filter Rules
      ↓
Ordered List / PBA → Final Allocation
```

Exact sequence (must hold): **Active carriers → Serviceability → Attribute Layer → Filters → same flow as today**.

Accounts that survive the Attribute Layer enter Filter Rules **unchanged** (no field mutation). If a CP_ID has **no** Attribute Layer rules, all serviceable accounts for that partner pass through transparently.

## Scope and semantics

| Concept | Meaning |
|---------|---------|
| Scope | Always a **courier_partner_id (CP_ID)** — e.g. Delhivery = 4. Rules for one CP_ID never affect another. |
| Conditions (`rule[]`) | Shipment attributes (weight, invoice value, payment mode / `order_type`, product type, and other fields supported by the Filter rule engine). Multiple conditions in one rule are **AND**. |
| Elimination output (`exclusion_list`) | One or more **account IDs under that CP_ID** removed when the rule matches. Conditions are never defined at account level as *inputs* — accounts appear only as elimination targets. |
| Evaluation model | **Non-short-circuit.** All **enabled** rules for that CP_ID are evaluated against the **original** post-serviceability account set for that partner. Matching does not stop at the first hit. |
| Elimination union | The union of `exclusion_list` across every matched (enabled) rule is removed. |
| Disabled rules | Excluded from runtime evaluation and from the elimination tally / outcome state. |

### Outcome states (per CP_ID after evaluation)
1. **All accounts pass** — no enabled rules matched, or no eliminations triggered.
2. **Partial elimination** — some accounts removed; at least one survives → survivors go to Filters.
3. **Full elimination** — all accounts under that CP_ID removed → that carrier contributes **no** accounts to the Filter layer for this request.

## How this differs from Filter Rules

| | Attribute Layer | Filter Rules |
|--|-----------------|--------------|
| When | After serviceability, **before** filters | After Attribute Layer |
| Scope | Per **CP_ID** | Enterprise-wide rule set |
| Effect | **Eliminate** named accounts under that partner | First match wins; `account_list` ∩ pool = eligible set |
| Match model | All enabled rules evaluated; union of eliminations | Priority order; first match wins (unless iteration) |
| Purpose | Carrier-partner-specific exclusions without duplicating general filters | General business routing / eligibility |

Never treat Attribute Layer rules as Filter or Ordered List rules. Use `list_carrier_exception_rules` / `find_carrier_exception_rule` — not `list_filter_rules` / `find_rule`.

## Top-level fields (shaped tool output)

| Field | Description |
|-------|-------------|
| `id` | Rule id |
| `courier_partner_id` | CP_ID this rule is scoped to |
| `rule_name` | Optional human label |
| `priority` | Display / evaluation ordering (lowest number = highest priority). All enabled rules still run; priority is for ordering, not short-circuit. |
| `enabled` | `false` → skipped at runtime |
| `rule` | Condition objects (AND), same general shape as Filter conditions (`key` / `op` / `value` / …) |
| `exclusion_list` | Account IDs eliminated when all conditions match |
| `updated_at` | Last update timestamp when present |

## Dashboard location
No new top-level nav. Configuration lives **before / alongside** the Filters section:

```
Dashboard → Filters → Carrier attribute rules (Attribute Layer) → select CP_ID → rule list
```

Visually distinct from general filter rules. Ops may view any brand's Attribute Layer rules read-only.

## Analyst investigation pattern
When a user asks why accounts under a carrier were missing **before** filters, or mentions Attribute Layer / carrier exception / "carrier attribute rules":

1. Resolve CP_ID if they named a carrier (`search_courier_partners` / `lookup_courier_partner`).
2. `find_carrier_exception_rule(query=<CP_ID or rule name>)` or `list_carrier_exception_rules`.
3. Check `enabled`, conditions vs the shipment attributes (from RR log `user_request` when available), and `exclusion_list`.
4. Explain outcome: which rules would match, which accounts would be eliminated, partial vs full elimination for that CP_ID.
5. Reminder: Filter / OL / PBA only see accounts that **survived** this layer.

## Out of scope (do not invent)
- Cluster-based condition values (deferred).
- Automatic migration of old carrier-specific Filter conditions into this layer.
- Standalone analytics module — basic evaluation logs (by reference number) are the observability path; do not claim a dedicated Attribute Layer analytics product exists.
- Creating / editing / deleting rules via tools — read-only analyst; recommend the dashboard change.
