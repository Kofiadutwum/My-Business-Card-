# AD Smart Business Cards

A digital contact card service. Subscribers build one card, share it with a
link, a QR code or an NFC tag, and whoever receives it saves the details
straight to their phone. Plans are billed annually; a card stops resolving
when its plan lapses and returns when it is renewed.

Built with Flask, SQLAlchemy and Paystack.

---

## Running it

```bash
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
python -c "import secrets; print(secrets.token_hex(32))"   # paste as SECRET_KEY

python seed.py                    # demo data, optional but recommended
python run.py
```

Open http://127.0.0.1:5000

Seeded accounts:

| Role | Email | Password |
|---|---|---|
| Administrator | `admin@example.com` | `admin1234` |
| Subscriber | `ama.serwaa@example.com` | `password123` |

Change both before this goes anywhere public.

Verify everything still works after any change:

```bash
python smoke_test.py
```

Thirty-six checks covering every route, the payment flow, the guards, and the
HTML/CSS separation rule.

---

## Layout

```
cardhub/
├── config.py               Plans, pricing, gateway keys — all from the environment
├── run.py                  Development entry point
├── seed.py                 Demo clients, payments and card views
├── smoke_test.py           End-to-end route and behaviour checks
└── app/
    ├── __init__.py         Application factory, filters, CLI, error handlers
    ├── extensions.py       db, login_manager, csrf, migrate
    ├── models.py           User, Profile, SocialLink, Subscription, Payment, CardView
    ├── forms.py            Validation, including Ghanaian phone formats
    ├── blueprints/
    │   ├── main.py         Landing and pricing
    │   ├── auth.py         Register, sign in, password
    │   ├── dashboard.py    Card editor, links, sharing, own analytics
    │   ├── cards.py        Public card, .vcf, QR, lead capture
    │   ├── billing.py      Checkout, MoMo charge, card redirect, webhook
    │   └── admin.py        Client analytics and finance
    ├── services/
    │   ├── paystack.py      Card and mobile money gateway
    │   ├── sms.py           Arkesel / mNotify / Hubtel / console
    │   └── jobs.py          Renewal reminders and payment reconciliation
    ├── utils/              vcard.py, qr.py, analytics.py, decorators.py
    ├── templates/
    └── static/
        ├── css/            Nine stylesheets. No CSS lives in any template.
        ├── js/             app.js, payment-poll.js
        └── fonts/          Montserrat woff2 files + OFL licence
```

---

## Brand

**AD Smart Business Cards**
adgraphics881@gmail.com · 0545875881

The logo lives in `app/static/img/` at four sizes, all generated from the
supplied artwork:

| File | Size | Used for |
|---|---|---|
| `logo.png` | 512px | Source / print |
| `logo-small.png` | 96px | Header, footer, card credit |
| `favicon.png` | 64px | Browser tab |
| `apple-touch-icon.png` | 180px | iOS home screen (white backing, no alpha) |

The grey backdrop was removed by measuring the blue ring's exact radius and
masking to it, so the mark sits cleanly on any background. iOS ignores
transparency on home-screen icons and composites onto black, so that one file
is flattened onto white deliberately.

### Palette

Every colour is sampled from the logo itself, not matched by eye:

| Token | Hex | From | Role |
|---|---|---|---|
| `--blue` | `#3070ae` | the ring and the 'd' | Primary actions, links, charts |
| `--purple` | `#7b19a2` | the 'a' and the gemstone | Secondary emphasis, step markers |
| `--amber` | `#e5af42` | the gem facet and the tittle | Warnings, unread markers, the card's edge rule |
| `--ash` | `#5b616d` | the original backdrop | Muted text |

Greys are pulled cool to sit with the blue; a warm grey beside this blue reads
as dirty rather than neutral.

Amber is used sparingly on purpose. It is the smallest colour in the logo and
it stays the smallest colour on the page — every appearance means "look here":
a grace-period badge, an unread contact, the sandbox warning, and a 3px rule
under every card's colour band.

### Contrast

Checked, not assumed. The old palette's yellow was light enough to carry dark
text; this blue is not. Dark text on `--blue` measures **3.05:1** and fails
WCAG AA, so primary buttons use white at **5.18:1**. White on `--amber` is
1.99:1 and is never used — amber always carries dark text.

### Card colourways

Subscribers choose Blue, Purple, Amber or Charcoal — three of the brand's own
colours plus a neutral, rather than an arbitrary swatch picker. The old
`gold` / `jade` / `clay` classes are retained in `tokens.css` so cards saved
under the previous palette still render; remove them once the data is migrated.

---

## The font

**Montserrat**, self-hosted, shipped with the project. Designed by Julieta
Ulanovsky and licensed under the SIL Open Font License 1.1, which permits
commercial use, embedding and redistribution. The licence text is at
`app/static/fonts/OFL.txt` and must stay there.

Six weights (300–800), upright only. No italics are included because the
design uses none.

### Why self-hosted rather than a Google Fonts link

A CDN link costs a DNS lookup and a TLS handshake to a third domain before a
single character renders. On a 3G connection that is a visible delay on the
one page that has to feel instant: someone else's phone, held out at a
meeting, opening your card for the first time. Self-hosted files arrive on the
connection already open. It also means no visitor's IP address is handed to a
third party, which keeps the privacy claim on the analytics page honest.

### Akan, Ewe and Dagbani characters

Two subsets are served. The Latin Extended file is included specifically
because it carries **ɛ ɔ ŋ** and their capitals **Ɛ Ɔ Ŋ** — checked against
the glyph table, not just the declared unicode range. Names and organisations
written properly in Akan, Ewe or Dagbani render correctly rather than as
boxes, which on a contact card is not a cosmetic detail.

The browser downloads the extended file only when a character from that range
actually appears on the page, so cards that never use one never pay for it.

### If you swap the family later

`--font` in `tokens.css` is the only place the family is named. But Montserrat
is a **wide geometric face with a large x-height** (525 against a cap height
of 700), and the type scale is tuned to it:

- `--t-display` and `--t-h1` ceilings are lower than the modular scale alone
  would give, because Montserrat 800 overflows its column otherwise.
- `--leading-body` is 1.7 rather than 1.65, because large x-heights read as
  crowded at normal leading.
- `--track-display` is negative and `--track-micro` positive — geometric faces
  set loose at large sizes and tight at small ones, so the correction runs in
  opposite directions at each end.
- Button and chip padding is reduced; at the original values
  "Download the contact file" wrapped on a phone.
- The dashboard sidebar is 16rem rather than 15rem.

Swapping to a narrower family means reversing those adjustments, not just
changing one variable.

### Tabular figures

Montserrat includes real tabular figures (`tnum`), which `base.css` switches
on for tables, statistics and anything in `.money-figure`. Without it the
digits are proportional and a column of cedi amounts will not align on the
decimal point — 1 is much narrower than 8.

---

## Payments

The gateway sits behind `app/services/paystack.py`. Nothing else in the
codebase talks to a payment provider, so switching to Hubtel or expressPay
means writing a second adapter with the same four functions, not editing views.

### Sandbox

With `PAYMENT_SANDBOX=1` (the default) the whole flow runs without a gateway
account. Charges complete instantly, nothing is billed, and the subscription
activates as it would in production. This is how you demonstrate the site
before Paystack verifies your business.

### Going live

1. Register at paystack.com and complete Ghanaian business verification.
2. Dashboard → Settings → API Keys & Webhooks. Copy the live keys into `.env`.
3. Set `PAYMENT_SANDBOX=0`.
4. Register your webhook URL: `https://yourdomain.com/billing/webhook`
5. Set `SITE_URL` to your real domain. Card links and QR codes are built from it.

### Why the webhook matters more than the callback

Mobile money payment completes on the customer's handset, not in the browser.
Paystack sends the final status to your webhook because the browser is not
part of that conversation. If you activate subscriptions only on the callback,
every customer who closes the tab while waiting for their prompt pays you and
stays deactivated.

Three defences are already in place:

- The webhook re-verifies with Paystack rather than trusting the posted body,
  so a replayed payload with a forged amount changes nothing.
- The signature is checked as HMAC-SHA512 over the **raw** request bytes.
  Re-serialising the parsed JSON changes key order and the digest never matches.
- `_activate()` is idempotent. A retried webhook, a refreshed callback and a
  poll can all fire for one payment without granting a second year.

### Provider codes

Paystack uses `mtn`, `atl` and `vod`. The last one predates the Vodafone Ghana
rebrand to Telecel — the customer sees "Telecel Cash", the API still wants `vod`.

Paystack does not support gh-link cards. If that matters to your market,
Hubtel and expressPay do.

---

## How the subscription rule works

Expiry is **computed**, never written. `Profile.is_live` asks whether the
owner's subscription is still inside its window, so a card deactivates on the
correct day even if no cron job has ever run.

- Lapsed cards return **HTTP 410 Gone**, not 404. The card existed and may
  return; 410 tells crawlers exactly that.
- Profile data is never deleted on expiry. Renewing restores the same card at
  the same link.
- There is a grace period (`GRACE_PERIOD_DAYS`, default 7) after the expiry
  date before the card goes dark.
- Renewal extends from the existing expiry, not from today. Paying three days
  early must not cost three days.

The optional nightly job is for reminder emails only:

```bash
flask --app run.py expire-check
```

---

## Analytics and privacy

Every card opening writes a `CardView` row: the date, how the visitor arrived
(link, QR or NFC tag) and whether they saved the contact file.

No IP address is stored. Ghana's Data Protection Act (Act 843) treats an IP
address as personal data, so it is salted and hashed on arrival and the
original is never written down. The hash is enough to tell a repeat scan from
a fresh one and is not reversible to a person.

The `?s=qr` and `?s=nfc` suffixes are what separate scans from taps in the
reports. They make no difference to what a visitor sees.

---

## vCard notes

Two decisions in `app/utils/vcard.py` are deliberate:

**Version 3.0, not 4.0.** Android and older iOS import 3.0 without complaint;
4.0 support is still patchy on the handsets your customers actually carry.

**Self-contained.** Every field is embedded in the `.vcf` rather than pointing
back at the website, so the recipient can save the contact with no internet
connection and no app installed. That is what makes a digital card as quick as
a paper one.

Line endings are CRLF, as RFC 6350 requires. Getting this wrong produces files
that import as one mangled contact on some devices.

Name splitting is a best-effort guess. Ghanaian naming does not always follow
given-then-family order, so `FN` always carries the name exactly as typed —
display is never wrong even when the structural guess is.

---

## The HTML/CSS separation rule

No `<style>` block and no `style=""` attribute appears in any template. The
smoke test walks every file under `app/templates/` and fails the build if one
does.

Two consequences worth knowing:

- **Charts** are inline SVG. Bar heights and positions are written as SVG
  geometry attributes (`x`, `y`, `width`, `height`) — those belong to the SVG
  language, not CSS, so the charts stay fully data-driven.
- **Progress bars** use the native `<progress>` element rather than a `div`
  with an inline width.

The one place JavaScript touches styling is the card tilt, and it sets CSS
custom properties (`--tilt-x`, `--tilt-y`). The `transform` itself lives in
`card.css`.

---

## Animations

All keyframes live in `app/static/css/animations.css`. Two rules govern them:

1. Motion must mean something. An entrance shows where content came from; the
   slow pulse on the MoMo waiting screen shows the system is still listening.
2. `prefers-reduced-motion` is honoured. For some people vestibular motion
   causes real nausea, so it is switched off rather than merely slowed.

The MoMo wait pulse is deliberately slow. A fast pulse reads as an error, and
that wait is normal — it can last a minute.

---

---

## Renewal reminders (SMS)

Annual billing dies of silence. Someone pays in March, hears nothing for
twelve months, and discovers the card went dark when a client tells them.
This is the cheapest insurance against that.

Three reminders go out — at 30 days, 7 days and 1 day before expiry — plus one
after the grace period ends telling them the card is now offline and their
details are safe. Three points, not six: past a certain frequency reminders
read as spam and people stop opening them, which costs you the one that
mattered.

```bash
flask --app run.py send-reminders --dry-run   # shows every message, sends none
flask --app run.py send-reminders             # actually sends
```

Schedule it nightly:

```cron
0 8 * * *  cd /srv/cardhub && venv/bin/flask --app run.py send-reminders
```

**Sending twice is impossible.** Every reminder writes a `Notification` row,
and a unique constraint on `(subscription_id, kind)` rejects the second one.
Cron double-fires, servers run two workers, and someone always runs the
command by hand to see what it does — none of that can text your customer
twice or bill you for it.

### Providers

`SMS_PROVIDER` accepts `console`, `arkesel`, `mnotify` or `hubtel`.
`console` is the default: it prints instead of sending, so a fresh clone runs
the whole job with no SMS account and nobody texts a real person by accident.

**Verify the endpoints before going live.** The request shapes in
`app/services/sms.py` reflect each provider's published API as understood at
the time of writing, but SMS providers move paths and rename parameters
without much ceremony. Open your provider's current documentation, send one
test message, and correct whatever has shifted.

### The sender ID trap

This catches nearly everyone once. In Ghana an alphanumeric sender ID must be
registered with your provider and approved before it will deliver.
Unregistered IDs are silently dropped or rewritten — the API returns success
and nothing arrives, which is the worst kind of failure because it looks fine.

Register the sender ID, then prove it works, then schedule anything:

```bash
flask --app run.py test-sms 0244123456
```

Then check the handset. "Sent" is not "delivered".

Message templates are written to fit one 160-character GSM-7 segment. Going
over doubles the cost per message, so the sender logs a warning if a template
has quietly grown.

---

## Payment reconciliation

Webhooks get dropped. The server restarts mid-request, a deploy takes the
endpoint down for nine seconds, the network blips. When that happens your
customer has paid, the payment sits on `pending` forever, and their card stays
dark until they phone you angry.

```bash
flask --app run.py reconcile
```

```cron
*/15 * * * *  cd /srv/cardhub && venv/bin/flask --app run.py reconcile
```

Every pending payment older than an hour is re-verified with the gateway and
settled: activated if it succeeded, marked failed if it failed, and abandoned
after 24 hours if the mobile money prompt was simply never approved. Recovered
customers get an SMS telling them the card is live, because from their side
the payment appeared to fail and they may already be drafting a complaint.

Two safety properties matter here:

- **An unreachable gateway never marks a payment failed.** If we could not ask,
  the payment stays pending and the next run tries again. Marking a paying
  customer as failed because of our own network problem is unrecoverable.
- **Re-running grants no extra time.** Activation is idempotent, and the test
  suite asserts that the expiry date does not move on a second run.

---

## Lead capture

The feature that makes this more than a digital business card. A paper card is
one-directional — you hand it over and hope. Here the person holding your card
can push their own name and number back to you while you are still standing
together.

The form sits on the public card behind a `<details>` disclosure, which opens
with no JavaScript and carries correct keyboard and screen-reader semantics
for free. Only the name is required; one of phone or email is enforced, because
a lead with neither is not a lead. Every extra required field costs completions,
and this form is filled in standing up, on someone else's phone, mid-conversation.

Replies land in **Contacts received** in the dashboard, and the card owner gets
an SMS immediately. Immediacy is the whole point — a notification that arrives
tomorrow is worth a fraction of one that arrives before the conversation ends.

Export is always available at `/dashboard/leads/export.csv`. These are the
subscriber's contacts, not ours; they should be able to take them to a
spreadsheet or a CRM without asking.

### Spam defences

1. **Honeypot field.** Hidden by CSS, irresistible to naive bots. A submission
   that fills it gets a cheerful success message and is silently discarded —
   telling a bot why it failed only helps it succeed next time. It is
   positioned off-screen rather than `display: none`, because some bots skip
   fields that are `display: none`.
2. **Per-visitor rate limit.** Three submissions per card per hour, keyed on
   the same salted hash the analytics use. No IP address is stored.
3. **Length caps** in the form, so nobody stores an essay.

Lapsed cards reject leads with 410 along with everything else, and the lead is
committed to the database *before* the SMS is attempted — a failing gateway
must never cost the card owner the contact itself.

Switch the whole feature off with `LEADS_ENABLED=0`.


## Deployment

```bash
pip install gunicorn
export FLASK_CONFIG=production
gunicorn -w 4 -b 0.0.0.0:8000 "run:app"
```

Before you go live:

- [ ] `SECRET_KEY` is random and secret. Changing it signs everyone out.
- [ ] `PAYMENT_SANDBOX=0` with live Paystack keys.
- [ ] `SITE_URL` is the real domain — QR codes are generated from it.
- [ ] HTTPS. `ProductionConfig` sets `SESSION_COOKIE_SECURE`, which means
      cookies simply will not be sent over plain HTTP.
- [ ] Move off SQLite. Set `DATABASE_URL` to PostgreSQL:
      `postgresql://user:pass@host/dbname`
- [ ] `flask db init && flask db migrate && flask db upgrade`
- [ ] Back up the database. It holds every subscriber's card.
- [ ] Serve `/static` from nginx rather than Flask.
- [ ] Create a real administrator: `flask --app run.py create-admin`
- [ ] Register your SMS sender ID and prove it with `flask --app run.py test-sms`
- [ ] Add both cron jobs: `send-reminders` nightly, `reconcile` every 15 minutes
- [ ] Confirm `reconcile` runs as a *different* process from the web server,
      so a crashed app does not also stop payments being rescued

---

## Known gaps

Honest list of what is not built:

- **Renewal reminders are SMS only.** Receipts and password reset still need an
  email service wiring in.
- **No password reset.** Only an in-session password change. This is the next
  thing to build; it will be your first support ticket.
- **SMS delivery reports are not collected.** The `Notification` row records
  that the provider accepted the message, not that it arrived. Providers offer
  delivery callbacks; none are wired up.
- **No rate limiting on sign-in.** Add Flask-Limiter before launch.
- **Avatars are stored on local disk.** Fine on one server, wrong the moment
  you run two. Move to S3 or Cloudflare R2.
- **No NFC writing in-app.** The share page gives the URL to write with any
  NFC writer app; there is no built-in writer.
- **Analytics has no geography.** Deliberate — that would mean either storing
  IP addresses or adding a third-party tracker.
- **Lead notifications have no digest option.** A busy card at a trade fair
  will send one SMS per contact, which gets expensive. An hourly digest is the
  obvious fix.
- **Deferred revenue is calculated on a straight-line day count**, which is a
  reasonable approximation but not a substitute for your accountant.

---

## Prior art

Patterns borrowed from open-source work:

- **rclement/business-card-generator** (Flask, segno, vCard) — the app-factory
  layout and server-side QR generation.
- **Steve0verton/qr-business-card** — embedding contact data directly in the
  vCard so the exchange needs no internet connection on the receiver's side.
- **CardMesh** — one card reachable three ways: URL, QR code, NFC tag.
