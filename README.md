# Virtual Suggestion Box

## Run locally
```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Pages
- `/` — homepage: what the product does, feature overview, and a live demo form that doesn't save anything
- `/register` — create an account: full name, username, email, password
- `/login` — log in with email *or* username, plus your authenticator code (or a recovery code) if 2FA is on
- `/forgot-password` and `/reset-password/{token}` — password recovery (see below)
- `/dashboard` (or `/admin` — same page) — "My Forms" for everyone; admins
  additionally see a "Users" tab, from which each user's forms can be
  drilled into (view submissions, or open the form directly).
- `/vb/{slug}` — public submission page for a form
- `/edit/{token}` — read-only view of your own submission, valid for 24hrs after submitting (submissions can't be edited, only viewed)

A dark mode toggle (🌙/☀️) sits in the top-right corner of every page.

## First-time setup
1. Run the server, go to `/register`, create your account.
2. There's no public path to becoming an admin — promote yourself with `promote_admin.py`:
```bash
python3 promote_admin.py your@email.com admin
```
3. Reload `/dashboard` — you'll see the "Users" tab alongside your own forms.

There's a tier above admin too — see **Super admin & site settings** below.

## Two-factor authentication
From `/dashboard` → **Account** → **Enable 2FA**:
1. Scan the QR code with an authenticator app (Google Authenticator, Authy, 1Password, etc.) — or type in the manual key shown beneath it.
2. Enter the 6-digit code the app shows to confirm. 2FA isn't active until this step succeeds.
3. From then on, logging in with that account's password also prompts for a fresh code.

Disabling 2FA requires re-entering your password, and TOTP codes are checked with a small clock-drift window (±30s) so it isn't overly strict about exact timing.

**Recovery codes**: 8 single-use backup codes are generated the moment 2FA is confirmed, shown once, and can be used at the login screen in place of a 6-digit code if you lose your device. Regenerating codes (Account page) invalidates all previous ones. Remaining-code count is shown on the Account page so you know when to regenerate.

Implementation note: TOTP (RFC 6238) is implemented directly with the standard library (`hmac`/`hashlib`/`base64`/`struct`) — no extra package. The QR code itself is rendered entirely in the browser from a standard `otpauth://` URI via a small CDN-hosted JS library, so the backend never needs an image-generation dependency either.

## Password reset
`/login` → **Forgot password?** → enter your email or username. Links expire after 1 hour and are single-use; resetting invalidates every existing session on the account, forcing a fresh login everywhere.

**If `SMTP_HOST`/`SMTP_USERNAME`/`SMTP_PASSWORD` are set** (see `install.md`), the reset link is actually emailed — no reset link is ever shown on-screen or returned by the API in this mode.

**If SMTP isn't configured**, the app falls back to handing the link back directly on-screen instead — functionally the same as clicking a reset link, just without the email round-trip. Treat that link like a password: anyone with it can reset the account. This is also what happens automatically if SMTP is configured but the actual send fails for some reason (bad credentials, host unreachable) — nobody gets locked out of resetting just because email delivery broke.

Submission view links and new-submission notifications are intentionally *not* emailed — only password reset uses this. Implementation is stdlib `smtplib` only, no extra package to install.

As a backup path, an admin can reset any user's password directly from the Users tab — this generates a temporary password shown once to the admin, who's responsible for getting it to the account holder out of band (Slack, in person, etc.).

## Login lockout
5 failed login attempts (tracked per IP + identifier) locks that combination out for 15 minutes. This is in-memory, not persisted — restarting the server clears all lockouts. If this ever runs behind multiple worker processes, this (and the submission rate limiter below) would need to move to a shared store like Redis, since each process currently tracks its own counts independently.

## Rate limiting on public submissions
Each IP is capped at 10 submissions per 10 minutes across all forms, to blunt naive spam/scripted abuse. Legitimate shared-office IPs shouldn't hit this under normal use. Same in-memory-only caveat as login lockout above.

## Sessions
Account page → **Active sessions** shows every device currently logged into your account (IP + user-agent + when), with the ability to log out any individual one or all others at once — useful if you left yourself logged in somewhere, or suspect something's wrong. Password resets (self-service or admin-triggered) automatically clear all sessions on that account as a security measure.

Cookies aren't marked `Secure` by default so local `http://` dev keeps working out of the box. Before deploying behind real HTTPS (e.g. a Cloudflare Tunnel), set `COOKIE_SECURE=true` in the environment:
```bash
COOKIE_SECURE=true uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## How a form works
Each form (`Project`) has a `type`:
- `'suggestion'` — submitters only see a suggestion field
- `'nomination'` — submitters only see nominee fields (name + role, can add several)
- `'both'` — submitters check "I have a suggestion" and/or "I want to nominate someone," and fill in whichever they pick

Every form always accepts anonymous submissions. A submitter can optionally give their name (and email, which unlocks a 24hr link to view what they submitted — it's read-only, not an edit link); leaving it blank submits anonymously.

## Reviewing submissions
Each submission has a status — `new`, `reviewed`, or `done` — that the owner or anyone the form is shared with can update inline while browsing. The submissions list supports text search (matches suggestion text, nomination reason, submitter name, and nominee names) and a status filter, both server-side and paginated (10 per page) so this stays usable once a form has hundreds of entries. Deleting an individual submission is owner-only, unlike status updates or viewing, which shared viewers can also do.

## Sharing forms with other users
From `/dashboard` → find your form → **Share**:
- **Give access**: enter someone's email or username to let them view that form's submissions and export its CSV. They can't edit the form, delete it, manage who else has access, or reach this Share page at all — those stay owner-only.
- **Remove**: revokes a person's access instantly.
- **Transfer ownership**: hands the form to someone else entirely. They become the owner (can edit/delete/share it); you automatically keep view-only access so you don't lose visibility into data you used to own. This can't be undone by you alone — the new owner would have to transfer it back.

A form shows up in everyone's "My Forms" list who has any relationship to it — owned forms and forms shared with you both appear there, with a "Shared by {name}" badge distinguishing the ones you don't own.

## Editing your profile
From `/dashboard` → **Account** → **Edit name**. Username and email aren't editable yet (kept simple to avoid uniqueness-collision handling on those fields for now).

## Super admin & site settings
A tier above regular admin, for whoever actually runs the site — currently
the only thing it controls is site-wide config, via a **Site Settings** tab
that only appears for this role:
- **Footer credit** — text and an optional link shown at the bottom of the homepage (e.g. "Built by Christian Taylor" linking to a resume/portfolio site). Leave the text blank to hide it entirely.
- **Dark mode on/off, site-wide** — disabling it removes the theme toggle from every page and forces light mode for every visitor, overriding their own browser/system preference.

A superadmin also has every regular admin capability (Users tab and its forms drill-down, etc.) plus their own forms like any owner — it's a strict superset, not a separate parallel role.

There's no path to this role through the app at all, not even for another admin — it's promoted the same way as admin, via the script, just with the role argument:
```bash
python3 promote_admin.py your@email.com superadmin
```
Regular admins can't view, disable, delete, or reset the password of a superadmin's account — only another superadmin can. This is enforced server-side, not just hidden in the UI.

## Backups (superadmin only, via Site Settings)
`/dashboard` → **Site Settings** → **Backups**:
- **Export full backup** — downloads every user, form, submission, nominee, share, recovery code, and site setting as structured JSON. Deliberately *not* a raw copy of the `.db` file — a raw file copy wouldn't survive a schema change (new columns/tables), which is exactly the situation this is meant to protect against. Export reads through the current models; import writes through the current models. As long as you export before wiping and import after the new schema is up, this works across updates.
- **Import a backup** — wipes every user, form, submission, and setting currently in the database and replaces it all with the file's contents, preserving the original IDs (so foreign key relationships stay intact). Sessions and password-reset tokens are intentionally excluded from both export and import; everyone (including whoever's doing the import) gets logged out and has to log back in afterward.
- See `install.md` for the exact "export → wipe → git pull → import" sequence when deploying an update that changes the schema.

## Account management (admin only, via the Users tab)
- Promote/demote between owner and admin
- Disable/enable an account — disabling immediately invalidates that user's active session too, not just future logins
- Delete an account — cascades to their forms, submissions, and nominees
- An admin can't demote, disable, or delete their own account; another admin has to do it

## Notes
- Sessions are cookie-based (httpOnly, 14-day expiry), stored server-side in `auth_sessions` — logging out invalidates the session row, not just the cookie.
- Passwords are hashed with PBKDF2-SHA256 (stdlib only, no bcrypt to install).
- Login accepts either email or username in the same field.
- CSV export (including status) is available to owners, anyone the form is shared with, and admins (for any form).

## Still to build
- Email for the 24hr submission-view link (intentionally out of scope — only password reset uses email, see above)
- Per-project Google Sheets export
- Shared Redis-backed rate limiting / lockout if this ever runs across multiple worker processes
