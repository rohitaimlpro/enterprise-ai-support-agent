# Payments & Invoices

Covers how you pay us and how to get proof of what you paid. For plan
prices themselves, see Pricing Guide; for getting money back, see Refund
Policy; for order-level actions, see Order Management.

## Accepted payment methods

We accept Visa, Mastercard, American Express, and Discover, plus PayPal,
for all plans. Enterprise plans (50+ seats) can additionally pay by
invoiced bank transfer with NET-30 terms -- contact your account manager
to set this up, since it requires a signed order form rather than
self-service checkout. Apple Pay and Google Pay are supported on mobile
web checkout where the browser/device supports them.

You can have multiple payment methods saved under **Account Settings >
Billing > Payment Methods**, with one marked as default; if the default
fails, we do not automatically fall back to a secondary method -- you'll
need to update the default or manually select another before the next
retry.

## Payment failures

If a payment fails, we retry automatically up to 3 times over 5 days;
you'll get an email each time with the reason if the card issuer
provided one. Common causes:

- **Expired card**: the most common cause -- update the expiry/CVV under
  Payment Methods.
- **Insufficient funds**: retries later in the 5-day window sometimes
  succeed once funds are available; you don't need to do anything unless
  you want it resolved immediately.
- **Bank blocking the charge as unusual activity**: contact your bank to
  authorize charges from "Meridian Suite" -- this is especially common
  for the first charge on a new card, or after a long period of the same
  recurring amount suddenly changing (e.g. after a plan upgrade).
- **Billing address mismatch**: some card issuers reject charges if the
  billing address on file doesn't match what's registered with the bank
  -- double-check it under Payment Methods.

Update your card under **Account Settings > Billing > Payment Method**
to trigger an immediate retry rather than waiting for the next scheduled
attempt. If your plan lapses due to repeated failures across all 3
retries, your data is kept for 30 days before the account is downgraded
to a free/read-only state -- you can restore full access anytime in that
window by simply updating payment info, with no reactivation fee.

## Viewing and downloading invoices

Every invoice is available under **Account Settings > Billing > Invoice
History**, as a downloadable PDF, going back to your first purchase.
Invoices include your billing address, tax details (VAT/GST number if
provided), and a line-item breakdown (plan charge, add-ons, proration,
tax) for expense reporting. If you need an invoice reissued with
different company details (e.g. you provided a personal name at
signup but need a company name for expense purposes), file a support
ticket with the correct billing information and we'll reissue past
invoices going back up to 12 months.

Team/Enterprise admins can view and download invoices for the whole
account's charges from **Admin Console > Billing**, not just their own
individual charges.

## Getting a specific invoice

If you can't find an invoice for a specific charge, check the date range
filter in **Invoice History** -- it defaults to the last 12 months. For
older invoices, or invoices tied to a cancelled/deleted account, request
one via a support ticket with the approximate purchase date and amount;
we retain billing records for 7 years (see Account Security FAQ) even
after an account is deleted, specifically to support requests like this.

## Sales tax and VAT

Sales tax (US) or VAT/GST (international) is calculated automatically at
checkout based on your billing address and appears as a separate line
item on every invoice -- it is not baked into the listed plan price. If
you're a tax-exempt organization or have a valid VAT number for reverse
charge, add it under **Account Settings > Billing > Tax Information**
before your next renewal; it can't be applied retroactively to past
invoices.

## Frequently asked questions

**Can I pay by wire transfer if I'm not on an Enterprise plan?**
No, invoiced bank transfer (NET-30) is an Enterprise-only payment
option; Individual and Team plans must use a card or PayPal.

**Why does my invoice show a different amount than the plan's list
price?**
Most commonly proration (a mid-cycle plan or seat change), a discount
applied to your account (education, nonprofit, volume), or tax --
Invoice History itemizes exactly which of these applies to a given
charge.

**Can I split a single invoice across two payment methods?**
No, each charge is billed to a single payment method (your current
default at the time of billing); to split costs internally, that's
typically handled outside our billing system, e.g. via your own
department's cost allocation.

**Does a failed payment retry attempt notify my card issuer of
anything?**
No differently than any normal charge attempt -- each retry is a
standard authorization request, not flagged to your bank as anything
unusual on our end.
