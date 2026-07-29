# Troubleshooting: Login Issues

Covers sign-in failures specifically. For account recovery when you no
longer have access to your email, or for creating/editing/deleting an
account, see Account Management. For enabling 2FA or reporting a
compromised account, see Account Security FAQ.

## "Invalid email or password"

1. Confirm caps lock is off and there's no leading/trailing space in the
   email field -- copy-pasting a password from a password manager
   sometimes carries a trailing space or newline.
2. Use **Forgot Password** to reset -- reset links expire after 30
   minutes and are single-use; requesting a new one invalidates the
   previous link.
3. If you normally sign in with Google/Microsoft SSO, use that button
   instead of typing a password -- SSO accounts don't have a Meridian
   Suite password at all, so "invalid password" on an SSO account
   usually means you're on the wrong tab of the login page.
4. Double-check you're using the email the account was created with --
   if your organization uses multiple domains (e.g. a personal Gmail and
   a work email), you may have two separate accounts.

## "Too many attempts, try again later"

Accounts lock for 15 minutes after 5 failed login attempts, as an
anti-brute-force measure. The lockout is per-account, not per-device, so
retrying from a different browser or device won't bypass it. Wait 15
minutes, or reset your password via **Forgot Password** to unlock
immediately (a successful reset clears the lockout).

## Stuck on "Verifying your account"

This usually means the confirmation email hasn't been clicked yet after
registration. Check spam/promotions folders for an email from
`no-reply@meridiansuite.example`. Confirmation links expire after 24
hours; request a new one from the login page's "Resend confirmation
email" link. If you registered with a typo'd email address, the
confirmation email will never arrive -- in that case, register again
with the correct address rather than waiting.

## SSO login loops back to the login page

Usually caused by a stale session cookie. Clear cookies for
`meridiansuite.example`, or try an incognito/private window. If it
persists across a fresh browser session too, your organization's SSO
configuration may need to be re-verified by an Enterprise admin --
common causes on the admin side are an expired SAML certificate or a
changed Identity Provider (IdP) entity ID. Ask your admin to check
**Admin Console > Security > SSO Configuration** for a certificate
expiry warning.

## Two-factor authentication codes aren't arriving

SMS codes can take up to 2 minutes, especially on international numbers.
If nothing arrives after 2 minutes:

1. Check that the phone number under **Account Settings > Security** is
   current -- a number change doesn't retroactively update codes already
   in flight.
2. Switch to an authenticator app (TOTP) under **Account Settings >
   Security > Two-Factor Authentication** -- this is also the recommended
   method for reliability, since it doesn't depend on carrier delivery.
3. If you're locked out of both your password and your 2FA device, use
   the backup codes generated when you first enabled 2FA, or file a
   support ticket tagged "account recovery" for manual identity
   verification.

## Browser-specific issues

- **Blank page after login**: usually an ad blocker or privacy extension
  blocking a required script -- try disabling extensions or use a private
  window to confirm.
- **"This connection is not private" warning**: almost always a
  local-network issue (public wifi captive portal, corporate proxy
  intercepting HTTPS) rather than a problem with our servers -- try a
  different network if possible.
- **Login works on mobile but not desktop (or vice versa)**: clear
  cookies/cache on the failing device; if it's a managed corporate
  device, IT-managed browser policies can sometimes block third-party
  cookies required for SSO.

## Frequently asked questions

**I reset my password but still can't log in -- why?**
Password managers or browsers sometimes autofill a cached old password.
Manually clear the field and type the new password, or paste it and
verify no extra characters were included.

**Can I have two 2FA methods active at once?**
Yes -- you can have both SMS and an authenticator app enabled; either one
will satisfy the 2FA prompt, which is useful as a fallback if your phone
is unreachable for SMS.

**Does logging in on a new device require re-verification?**
Not by default for Individual/Team accounts. Enterprise admins can
enable "new device confirmation" under Security Policies, which emails
an approval link the first time a device signs in.
