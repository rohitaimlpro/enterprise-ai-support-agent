# Troubleshooting: Software Installation

Covers installing and running the Meridian Suite desktop apps. For
account-level access problems (can't log in at all), see Troubleshooting:
Login Issues. For storage/plan questions unrelated to installation, see
Pricing Guide.

## System requirements

- **Windows**: 64-bit Windows 10 or 11. 32-bit Windows is not supported.
- **macOS**: macOS 12 (Monterey) or later, Apple Silicon or Intel.
- **Minimum hardware**: 8GB RAM (16GB recommended for video/render-heavy
  apps in the suite), 10GB free disk space for installation plus
  additional space for your working files and local cache.
- **Linux**: not officially supported for the desktop app; the web app
  works in any modern Chromium or Firefox-based browser on Linux.

## Installer fails at "Downloading components"

1. Check your internet connection and disable any VPN temporarily -- some
   VPNs route traffic through regions where our CDN has degraded
   performance.
2. Confirm you have at least 10GB free disk space; the installer checks
   this but sometimes underestimates temp-file overhead on nearly-full
   drives.
3. Corporate firewalls sometimes block the installer's CDN. Ask your IT
   admin to allowlist `*.meridiansuite.example` and
   `*.meridian-cdn.example` on ports 443 (HTTPS) and 80 (HTTP redirect).
4. If the download consistently stalls at the same percentage, it's
   often a corrupted partial download -- delete the installer file
   entirely and re-download rather than resuming.
5. Enterprise admins deploying via MDM/SCCM should use the offline
   installer package (available at **Admin Console > Downloads > Bulk
   Deployment**) instead of the standard web installer, which requires
   a live internet connection per machine.

## "This app can't run on your PC"

Meridian Suite desktop apps require 64-bit Windows 10/11 or macOS 12+.
32-bit operating systems and older macOS versions are not supported, and
there is no supported workaround -- you'll need to use the web app at
`app.meridiansuite.example` instead, which works on any modern browser
regardless of OS version, though with a reduced feature set for
render-heavy tools.

## Installation succeeds but the app won't open

1. Restart your computer (resolves roughly 60% of cases -- a leftover
   installer process can lock required files that prevent the app from
   initializing).
2. Reinstall using the **Repair Tool** found at **Account Settings >
   Downloads > Repair Tools** -- this does a clean uninstall of cached
   application state before reinstalling, which fixes most corruption
   issues a plain reinstall doesn't.
3. Temporarily disable antivirus software that may be quarantining app
   files, then re-run the installer; if that resolves it, add an
   exclusion for the Meridian Suite install directory in your antivirus
   settings rather than leaving it disabled.
4. Check **Event Viewer** (Windows) or **Console.app** (macOS) for a
   crash log -- if you need to file a ticket, attaching this log
   significantly speeds up diagnosis.
5. As a last resort, a full manual uninstall (including removing
   `%APPDATA%\MeridianSuite` on Windows or `~/Library/Application
   Support/MeridianSuite` on macOS) followed by a fresh install resolves
   cases the Repair Tool doesn't catch.

## License says "in use on another device"

Individual plans allow activation on 2 devices at once. Deactivate an
old device from **Account Settings > Devices** (useful when you've
retired a laptop and forgot to deactivate it first), or upgrade to a
Team plan for more concurrent seats -- Team/Enterprise seats each get
their own 2-device allowance, it's not a shared pool across the team.

## Sync isn't working between devices

Check **Account Settings > Storage** to confirm you haven't exceeded
your storage quota -- sync silently pauses (without an error dialog, by
design, to avoid interrupting your work) when a plan is over quota. Free
up space or purchase additional cloud storage (see Pricing Guide for
add-on pricing). Other common causes:

- **Sync paused manually**: check the taskbar/menu-bar app icon for a
  "Sync Paused" state, which can be toggled on accidentally.
- **File conflicts**: if the same file was edited offline on two devices,
  sync creates a conflicted copy rather than silently overwriting either
  version -- look for a file named `<filename> (conflicted copy).ext`.
- **Firewall blocking the sync port**: corporate networks that allow web
  browsing but block other outbound ports can allow the app to open but
  prevent background sync -- same CDN allowlist as the installer section
  above generally resolves this too.

## Frequently asked questions

**Can I install on more computers than my device limit and just not use
some of them simultaneously?**
No -- the limit is on activated devices, not concurrent sessions;
installing counts as activating unless you deactivate an old device
first.

**Does reinstalling lose my local files?**
No, reinstalling only affects the application itself; files stored in
your Meridian Suite folder or synced to the cloud are untouched. Local
app *preferences* (like window layout) do reset.

**Is there a way to install without admin/local rights on a managed
corporate laptop?**
Ask your IT admin to deploy via the offline installer package mentioned
above, which is designed for managed-device rollout without requiring
each user to have local install permissions.
