# Clickpost Platform

Multi-carrier logistics layer. Brands use one Clickpost integration instead of many carrier APIs.

```
Serviceability → Serviceability APIs
EDD            → EDD API
Carrier select → Recommendation API (RecsV2)
Shipment create→ Order Creation API
Tracking       → Registration + webhooks / polling
```

## Account onboarding (per account)

1. **Create + activate** — `active=true` required; inactive accounts never reach allocation.
2. **Credentials** — stored per account ID; brand passes CPID + account code, Clickpost resolves credentials.
3. **Upload serviceability** — required for Recommendation only. Missing upload = dropped in pre-filters even if active. Modes: **cross-product** (pickup list × per-order-type drop list) or **lane-to-lane** (`(pickup, drop)` tuples per order type).

## Recommendation

Returns a ranked `preference_array` (CPID + account code, plus charge/SLA/scores). Factors: serviceability, weight, performance/RTO, Filter/OL rules, load distribution.

## Order Creation

Brand passes CPID + account code; Clickpost calls the carrier. **No serviceability check** — it trusts the caller. Output: AWB. Supports SPS/MPS, reverse (QC), B2B MPS, International V4.
