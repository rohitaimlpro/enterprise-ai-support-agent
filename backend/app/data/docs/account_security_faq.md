# Account Security FAQ

Covers 2FA, compromised accounts, data retention/privacy, SSO, and file
access. For everyday sign-in problems (forgotten password, lockouts),
see Troubleshooting: Login Issues. For creating/editing/deleting the
account itself, see Account Management.

## How do I enable two-factor authentication (2FA)?

Go to **Account Settings > Security > Two-Factor Authentication** and
choose either SMS or an authenticator app (recommended for reliability
-- see Troubleshooting: Login Issues for why SMS can be slower).
Authenticator apps (Google Authenticator, Authy, 1Password, etc.) work
via a standard TOTP QR code, so any compatible app works, not just one
specific vendor.

Enterprise admins can enforce 2FA for all seats under **Admin Console >
Security Policies > Require 2FA**. When enforced, users who haven't set
up 2FA are prompted to do so on their next login and have a 7-day grace
period before access is restricted.

## I think my account was compromised

1. Change your password immediately from **Account Settings > Security**
   -- this immediately invalidates all existing login sessions except the
   one you're using to make the change.
2. Review **Account Settings > Devices** and revoke access for any
   device you don't recognize; each entry shows an approximate location
   and last-active time to help you identify unfamiliar ones.
3. Enable 2FA if it wasn't already on, since a password change alone
   doesn't help if the attacker also has your 2FA method.
4. Check **Account Settings > Billing > Payment Method** to confirm no
   unfamiliar payment method was added -- attackers sometimes add a card
   to make purchases before you notice the compromise.
5. File a support ticket tagged "security" -- these are triaged within 1
   hour, 24/7, ahead of the standard response-time queue described in
   Contact & Escalation.

If you can no longer log in at all because the attacker changed your
password or email, use **Forgot Password**; if that email was also
changed, file a security ticket immediately and we'll verify your
identity manually to restore access.

## How long are support conversations and data retained?

Chat transcripts are retained for 12 months for quality and training
purposes. Order and billing records are retained for 7 years to comply
with financial record-keeping requirements, even if you delete your
account (see Account Management). You can request deletion of your
account data at any time via **Account Settings > Privacy > Request Data
Deletion**, in line with GDPR/CCPA rights -- this deletes everything
except records we're legally required to retain (primarily billing
history), which are anonymized where possible instead.

Data deletion requests are processed within 30 days and you'll get a
confirmation email once complete. This is the same underlying process
triggered by deleting your account, just accessible without deleting the
account itself if you only want your data purged while keeping access.

## Does Meridian Suite support Single Sign-On (SSO)?

Yes, Enterprise plans support SAML 2.0 and OIDC-based SSO with providers
like Okta, Azure AD, and Google Workspace. Contact your account manager
to configure SSO for your organization -- setup typically takes 1-2
business days once you provide your IdP metadata. Once SSO is
configured, admins can optionally enforce it under **Admin Console >
Security Policies > Require SSO**, which disables password-based login
for all seats on that account (2FA becomes irrelevant at that point,
since authentication is fully delegated to your IdP).

If SSO login isn't working, see the "SSO login loops back to the login
page" section of Troubleshooting: Login Issues -- most SSO issues are
either a stale browser session or an expired certificate on the IdP
side, not a Meridian Suite outage.

## Who can see my files?

Only you, and anyone you explicitly share a file or folder with.
Meridian Suite staff do not access customer files except when required
to investigate a security incident you've reported, or as required by
law (for example, a valid legal order) -- and any such access is logged
and, where legally permitted, disclosed to you afterward.

Team and Enterprise admins can see file *metadata* (names, sizes, last
modified) for storage-management and compliance purposes, but not file
*contents*, unless a team member has explicitly shared that content with
them the same way they'd share with any other teammate.

## Frequently asked questions

**Does 2FA apply to the API as well as the web/desktop app?**
API access uses separate API keys (managed under **Admin Console > API
Keys** on Team/Enterprise) rather than session-based 2FA -- treat API
keys with the same care as a password, since they bypass the 2FA prompt
by design.

**Can I see a login history for my account?**
Yes, under **Account Settings > Security > Login History**, showing the
last 90 days of sign-in attempts, including failed ones, with
approximate location and device.

**If my organization enforces SSO, can I still use a personal Individual
account with a password?**
Yes -- SSO enforcement only applies to accounts under your organization's
Enterprise plan. A separate personal Individual account you own isn't
affected, since it's not part of that organization's Admin Console.
