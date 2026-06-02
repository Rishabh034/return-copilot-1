# Performance Based Allocation (PBA)

## What is PBA?
Performance Based Allocation is the system's terminal allocation layer. It receives the upstream carrier pool and decides the final priority order based on performance signals — when no Ordered List rule has matched.

## Role in the Pipeline
PBA and OL are **alternatives** — one or the other fires, never both.

```
[1] Check Active Accounts
[2] Check Serviceable Accounts
[3] Remove Out-of-Threshold
[4] Remove ODA Pinched Accounts
        ↓
  Filter Rules
        ↓
  upstream carriers
        ↓
  ┌──────────────────────────────────────┐
  │  OL matched?                         │
  │                                      │
  │  YES → OL × Filter Intersection      │
  │        → TERMINAL (final allocation) │
  │                                      │
  │  NO  → PBA receives upstream carriers│
  │        → TERMINAL (final allocation) │
  └──────────────────────────────────────┘
```

## What PBA Receives
| Scenario | Input to PBA |
|----------|-------------|
| Filter matched, OL did not match | Filter's eligible carrier pool |
| Neither filter nor OL matched | All upstream carriers from pre-filter checks |

## Key Principles
- PBA is **not a fallback** in the degraded sense — it is a full terminal layer
- OL is **rule-based terminal** — explicit priority defined by the brand
- PBA is **performance-based terminal** — system decides priority autonomously
- If OL fires, PBA does not run. If OL does not fire, PBA always runs.

## How PBA Decides Priority
TBD — performance signals and scoring logic to be documented.

---

## Summary

| Terminal | Trigger | Priority Source |
|----------|---------|-----------------|
| OL | OL rule matched | Brand-defined explicit order |
| PBA | No OL rule matched | System-decided based on performance |
