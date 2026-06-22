# Supply Chain Journey — End to End

## Overview

```
User visits website (PDP)
        ↓
[1] Serviceability Check
        ↓
[2] EDD Display
        ↓
[3] Order Placement (OMS)
        ↓
[4] Fulfillment Routing (WMS)
        ↓
[5] Warehouse Fulfillment (Pick → Pack)
        ↓
[6] Carrier Selection
        ↓
[7] Shipment Creation with Carrier
        ↓
[8] Status Updates → OMS
        ↓
[9] Tracking & Customer Notification
```

---

## Stage 1 — Serviceability Check (PDP)

When a user enters their pincode on a product page, the brand checks whether that pincode is serviceable.

**Two approaches:**

| Approach | How it works |
|----------|--------------|
| **Carrier API (live)** | Brand calls carrier's serviceability API in real-time with pickup + drop pincode |
| **Bulk stored (internal)** | Brand pre-fetches serviceability data from carriers, stores it internally, and serves it from their own system |

The internal approach is faster and reduces dependency on carrier API uptime.

---

## Stage 2 — EDD Display (PDP)

Alongside serviceability, an Estimated Delivery Date (EDD) is shown to the user.

**Two approaches:**

| Approach | How it works |
|----------|--------------|
| **Carrier EDD API (live)** | Brand calls carrier's EDD API with route details |
| **Bulk SLA file (internal)** | Brand uploads carrier SLA data (transit days per zone/pincode), computes EDD internally |

---

## Stage 3 — Order Placement (OMS)

User places the order → brand's OMS (Order Management System) APIs are called.

- Standard order creation APIs handle capturing order details, payment, customer info
- OMS becomes the source of truth for the order lifecycle
- All downstream status updates flow back into the OMS

---

## Stage 4 — Fulfillment Routing (WMS)

As part of post-processing after order placement, the system determines **which warehouse** will fulfill the order and places a fulfillment entry there.

**Two setups:**

| Setup | How it works |
|-------|--------------|
| **OMS doubles as WMS** | OMS handles warehouse allocation logic internally |
| **Dedicated WMS** | OMS calls a separate WMS API to route the fulfillment to the right warehouse |

The fulfillment entry is created at the chosen warehouse — either via internal API or the WMS API.

---

## Stage 5 — Warehouse Fulfillment (Pick → Pack)

Once the fulfillment entry is created, the order appears in the warehouse queue.

```
Order appears in WH queue
        ↓
Picker generates a pick list
        ↓
Items collected and brought to packing desk
        ↓
Items packed and shipment is ready
```

---

## Stage 6 — Carrier Selection

With the shipment ready, the brand resolves which carrier will fulfill the order.

**Two approaches:**

| Approach | How it works |
|----------|--------------|
| **Internal logic** | Brand's own rules (weight, zone, cost, SLA) decide the carrier |
| **SaaS API** | External allocation engine evaluates rules, serviceability, performance scores, and returns a ranked carrier list |

---

## Stage 7 — Shipment Creation with Carrier

Once a carrier is selected, the brand creates a shipment with that carrier via API integration.

- Brand (or via a SaaS layer) calls the carrier's order creation API
- Carrier returns an AWB number
- AWB is stored against the order in OMS

---

## Stage 8 — Status Updates → OMS

At each stage of the journey, status is pushed back to the OMS so the brand and customer can track progress.

- Warehouse fulfillment stages (picked, packed, dispatched)
- Carrier handoff confirmation
- In-transit and delivery scans

---

## Stage 9 — Tracking & Customer Notification

Once the carrier picks up the shipment:

- Brand has a **tracking integration** to pull status updates from the carrier
- Updates are fetched periodically (webhook push or polling)
- Customer is notified at key milestones (shipped, out for delivery, delivered, failed delivery)

**Two tracking approaches:**

| Approach | How it works |
|----------|--------------|
| **Direct carrier integration** | Brand polls each carrier's tracking API individually |
| **Unified tracking layer (SaaS)** | Single integration; aggregates tracking across all carriers |

