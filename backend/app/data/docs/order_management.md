# Order Management

Covers actions on any order -- a subscription purchase or a hardware
item. For hardware delivery timelines and returns specifically, see
Shipping & Delivery. For getting money back rather than changing/
cancelling an order, see Refund Policy.

## Placing an order

Subscriptions are purchased from **Plans & Pricing**; hardware (tablets,
styluses, docks) from the **Store** tab. Both go through the same
checkout with your saved payment method (see Payments & Invoices for
accepted methods). You'll get an order confirmation email immediately,
and the order appears under **Account Settings > Orders** right away
with status `processing`.

Subscription orders provision access instantly once payment succeeds --
there's no separate "delivery" wait the way there is for hardware.
Hardware orders follow the timeline in Shipping & Delivery.

Team/Enterprise admins can place bulk seat orders (e.g. adding 20 seats
at once) from **Admin Console > Seats > Add Seats**, which uses the same
order/billing pipeline but skips individual checkout per seat.

## Changing an order

While an order is still `processing`, you can change quantity, plan
tier, or add an item from **Account Settings > Orders > [order] >
Modify**. For subscription orders, "changing" here means adjusting what
you're purchasing *before* the initial charge settles -- once billing is
complete, use the normal upgrade/downgrade flow in Subscription &
Billing FAQ instead, not this order-modify screen. For hardware, this
covers changing the model, quantity, or shipping speed before the
warehouse packs it.

Once an order has moved to `shipped` (hardware) or completed billing
(subscriptions), it can no longer be changed through this flow -- cancel
and reorder instead, subject to the cancellation terms below, or (for
hardware already shipped) use the return process in Shipping &
Delivery.

## Cancelling an order and cancellation fees

Orders still `processing` can be cancelled free of charge, no questions
asked, from **Account Settings > Orders > [order] > Cancel**. Beyond
that:

- **Subscription orders cancelled after the first billing cycle**: may
  be subject to the $10 early-termination processing fee described in
  the Refund Policy, specifically if you're on an annual plan billed
  monthly. Monthly-billed, monthly-committed plans (the default) have no
  cancellation fee at all -- cancelling simply stops future renewals.
- **Hardware orders that have already shipped**: cannot be cancelled --
  use the standard 30-day return process in Shipping & Delivery instead.
- **Enterprise multi-seat orders**: cancellation of a bulk seat order
  should go through your account manager rather than the self-service
  flow, since it may involve a contract amendment.

Cancelling a subscription order does not delete your account or your
files -- it stops future billing. See Account Management if you actually
want to delete the account itself.

## Setting up or changing a shipping address

Add or edit shipping addresses under **Account Settings > Shipping
Addresses**. You can save multiple addresses (e.g. home and office) and
pick one at checkout, or set a default so it's pre-selected. If you need
to correct the address on an order that's still `processing`, edit it
from the order detail page directly; once an order is `shipped`, the
address can't be changed through our system -- contact the carrier
directly using the tracking link in your shipment email, since most
carriers support redirecting an in-transit package for a small fee.

Team/Enterprise accounts can maintain a shared "office" address available
to all members for hardware orders, managed from **Admin Console >
Shipping Addresses**, separate from each member's personal saved
addresses.

## Tracking an order

Check **Account Settings > Orders** or ask the support assistant "where
is my order" for live status (`processing`, `shipped`, `delivered`,
`cancelled`, `refunded`). Tracking numbers are emailed automatically
once a hardware order ships, and the same link is always available on
the order detail page if the email gets lost. Subscription orders don't
have meaningful "tracking" beyond `processing` -> active, since there's
no physical transit step.

## Frequently asked questions

**Can I combine a hardware item and a subscription in one order?**
Yes, checkout supports mixed carts -- the subscription activates
immediately while the hardware portion follows the normal shipping
timeline; you'll see both reflected as line items on the same order but
they progress through status independently.

**I placed an order by mistake seconds ago -- can I undo it instantly?**
Cancel it the same way as any `processing` order from Account Settings >
Orders -- there's no special "instant undo," but since orders typically
stay `processing` for about a day, a same-second mistake is easily
caught in time.

**Does changing my shipping address on file affect orders already
placed?**
No -- updating your default saved address only affects future orders;
existing orders keep whatever address was selected at checkout unless
you edit that specific order while it's still `processing`.
