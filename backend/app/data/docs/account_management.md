# Account Management

Covers the account lifecycle: creating, editing, switching between, and
deleting accounts, plus registration and password-recovery problems. For
2FA, SSO, and compromised-account response, see Account Security FAQ.
For plan/seat changes, see Subscription & Billing FAQ.

## Creating an account

Sign up at the login page with an email and password, or via Google/
Microsoft SSO. You can create a separate account for a family member or
colleague (e.g. on a shared household or team) -- each person should use
their own email address rather than sharing a login, since plans are
licensed per seat and shared logins are a security risk (see Account
Security FAQ). A single email address can only be tied to one account,
though one person can be a *member* of multiple accounts (see "Switching
between accounts" below) using different emails or invited access.

New accounts start on a 14-day free trial of the Team plan by default
(see Pricing Guide) -- no credit card required to explore before
committing to a paid plan.

## Editing your account details

Go to **Account Settings > Profile** to update your name, email, or
profile photo. Changing your email requires confirming the new address
before it takes effect -- you'll receive a confirmation link at the new
address, and the old address remains active until you click it, so you
can't get locked out mid-change. Your login history and billing records
stay tied to the same account through an email change; only the contact
address changes, not your account identity.

Team/Enterprise admins can update a *member's* display name and role
from **Admin Console > Members**, but cannot change a member's login
email on their behalf -- that must be done by the member themselves for
security reasons.

## Switching between accounts

If you have access to more than one account (for example a personal
Individual account and a company Team account you're a member of), use
the account switcher in the top-right menu -- no need to log out and back
in. Switching accounts does not merge data between them; files, chat
history, and billing stay completely separate per account, as if they
were unrelated logins that happen to share your session.

If you were invited to a Team account by a colleague, accepting the
invite creates a *membership* rather than a new login -- you'll use the
same email/password (or SSO) across both, and the switcher lets you move
between "my personal files" and "the team's files" without re-authenticating.

## Deleting your account

Go to **Account Settings > Privacy > Delete Account**. This is
permanent: your files, conversation history, and billing history are
deleted within 30 days, except where we're required to retain records
for tax/legal purposes (see Account Security FAQ for the 7-year billing
record retention). Active subscriptions are cancelled first -- see the
Refund Policy for what's refundable on that cancellation, since deleting
the account doesn't itself trigger a refund beyond the normal policy.

There's a 14-day grace period after clicking delete during which you can
cancel the deletion by logging back in -- after that, the process is
irreversible. Team/Enterprise members being removed by an admin (rather
than deleting their own account) instead lose access immediately but
their historical activity remains part of the team's records.

## Registration problems

If sign-up fails with "email already in use," you likely already have
an account -- use **Forgot Password** instead of registering again,
since a second account can't be created with the same email. If the
confirmation email never arrives:

1. Check spam/promotions folders for an email from
   `no-reply@meridiansuite.example`.
2. Use **Resend confirmation email** on the login page (links expire
   after 24 hours, and only the most recently sent link is valid).
3. Double check the email was typed correctly during sign-up -- a typo'd
   address means the confirmation email goes nowhere, and you'd need to
   register again with the correct address.

If sign-up fails with a generic error rather than "email already in
use," it's usually a temporary issue -- retry after a minute, or file a
support ticket if it persists across multiple attempts and browsers.

## Recovering your password

Use **Forgot Password** on the login page to receive a reset link (valid
30 minutes, single-use). If you no longer have access to the email on
the account -- for example it's an old work email at a company you've
left -- file a support ticket with your account email and any
identifying details (recent order IDs, billing zip code, etc.) and we'll
verify your identity manually before restoring access. This manual
process typically takes 1-2 business days since it involves a human
review, unlike the instant self-service reset link.

## Frequently asked questions

**Can I merge two accounts into one?**
Not automatically -- there's no self-service account-merge tool, since
files, billing, and history would need to be reconciled. File a support
ticket describing both accounts and what you'd like merged; we'll
evaluate case by case.

**What happens to shared files if I delete my account?**
Files you've shared with others remain accessible to them if they saved
a copy to their own account; live shared links/folders you own stop
working once your account is deleted, since the underlying storage is
removed.

**Can a Team admin delete a member's account for them?**
Admins can *remove* a member from the team (revoking their access to
team resources), but cannot delete the member's underlying personal
account -- only the account owner can do that from their own Privacy
settings.
