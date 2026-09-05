# Courier & delivery API

Everything added for ODOS Delivery. Existing endpoints are unchanged; the only
behavioural change to an existing route is noted under *Vendor* below.

All routes require a Bearer JWT (`get_current_user`). "Courier" additionally
means `courier_status = approved` (admins pass too) — `require_courier_access`.

## Concepts

| | |
|---|---|
| **Order** | the commercial fact. Untouched by this work except two denormalized pointers that already existed (`courier_id`, `courier_assigned_at`). |
| **Delivery** | the fulfilment fact. One live row per order (partial unique index excludes terminal states). Holds the state machine position. |
| **DeliveryOffer** | one invitation to take a delivery. Many per delivery over its life. `status='open'` is partial-unique per delivery. |
| **DeliveryEvent** | append-only audit row per transition. Never contains a secret. |
| **DeliveryAttempt** | one try at handing over. Phase 3 verification binds to an attempt. |

`Delivery.vendor_id` = which store to collect from (always set).
`DeliveryOffer.vendor_id` = **who may take it**: set → that vendor's own riders
only; NULL → the ODOS open pool. These are different questions and conflating
them empties the open pool.

## State machine

Owned by `app/services/delivery_state_machine.py`. Clients post an **intent**;
the server decides the resulting state. No endpoint accepts a status.

```
pending_assignment → offered → accepted → en_route_to_pickup → arrived_at_pickup
  → pickup_verification → picked_up → en_route_to_customer → arrived_at_customer
  → delivery_verification → delivered

exceptional: customer_unavailable · delivery_failed · return_required
             returning_to_vendor · returned · cancelled · disputed
```

`complete_pickup` and `complete_delivery` are **not** in `COURIER_INTENTS`. A
rider cannot reach `delivered` by any request. That transition exists only for
the verification path (Phase 3), because completion is what eventually releases
money.

## Courier endpoints

Prefix `/api/courier`.

| Method | Path | Purpose | Notes |
|---|---|---|---|
| GET | `/me` | rider profile | 404 if no profile yet — the app routes to setup |
| POST | `/profile` | create profile | 409 if one exists; 400 on unknown vehicle type |
| PATCH | `/status` | go online/offline | |
| PATCH | `/location` | push coordinates | **409 unless the rider holds an active delivery** |
| GET | `/pool` | claimable offers | redacted; see below |
| POST | `/pool/{offer_id}/claim` | claim | returns the **Delivery**, not the offer |
| GET | `/deliveries/active` | the one being carried | `null` when none |
| GET | `/deliveries/history` | terminal deliveries | `limit` ≤ 100 |
| GET | `/deliveries/{id}` | delivery + event timeline | own deliveries only |
| POST | `/deliveries/{id}/start-pickup` | intent | |
| POST | `/deliveries/{id}/arrive-at-pickup` | intent | |
| POST | `/deliveries/{id}/begin-pickup-verification` | intent | stops here until Phase 3 |
| POST | `/deliveries/{id}/start-dropoff` | intent | |
| POST | `/deliveries/{id}/arrive-at-customer` | intent | |
| POST | `/deliveries/{id}/begin-delivery-verification` | intent | stops here until Phase 3 |
| POST | `/deliveries/{id}/report-customer-unavailable` | intent | body: `reason`, `note` |
| POST | `/deliveries/{id}/fail-delivery` | intent | body: `reason`, `note` |
| POST | `/deliveries/{id}/start-return` | intent | |
| POST | `/deliveries/{id}/release` | hand back to the pool | clears `courier_id` on both delivery and order |

One endpoint per intent rather than `{intent}` as a path parameter, so the
OpenAPI schema lists exactly what a rider may do and no intent is reachable by
editing a URL segment.

### Errors

| Code | When |
|---|---|
| 403 | not an approved courier; suspended; intent not available to riders; offer belongs to another fleet |
| 404 | no profile; delivery not found **or not yours** (deliberately indistinguishable) |
| 409 | offer already claimed/expired; illegal transition from the current state; already carrying a delivery; location without an active delivery |
| 400 | unknown vehicle type; unknown intent |

### What the pool discloses

`DeliveryOfferRead` (pre-claim) carries: order number, pickup store name and
**area**, drop-off **area**, item count, SLA deadline.

It does **not** carry the street address, coordinates, customer name, phone, or
the order value. A rider deciding whether to take a job needs where it goes and
how long they have; what the customer spent and exactly where they live is not
part of that decision.

`DeliveryRead` (post-claim, only for the holder) adds the full pickup and
drop-off addresses, coordinates, delivery instructions, and the customer's
**first name only**.

## Vendor endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/vendor/orders/{order_id}/request-courier` | ask ODOS to deliver this order |

Opt-in **per order**. Idempotent — asking twice returns the existing delivery.

**The one behavioural change to an existing route:** `PATCH
/api/vendor/orders/{id}/status` moving an order to `ready` now creates an offer
*if* that vendor previously called `request-courier` for it. For every other
order it does nothing at all, so the existing vendor-dispatch flow is unchanged
for every vendor who hasn't opted in.

## Background work

`expire_stale_offers(db)` retires offers past `sla_deadline`. The delivery
returns to `pending_assignment` rather than dying, and a **vendor-scoped** offer
that expired escalates to the open pool — "an order cannot sit unclaimed" means
it becomes visible to more riders, not that it disappears. Not yet scheduled;
it needs a Celery beat entry or a cron hook.

## Not built yet (Phase 3+)

Pickup and delivery verification (the OTP path), courier settlement, realtime
`delivery.*` events, push notifications, admin assignment and the courier
application flow. `picked_up` and `delivered` are therefore unreachable in
production today — by design, not by omission.
