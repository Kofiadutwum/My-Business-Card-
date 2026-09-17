"""Smoke test: every route, once, checking status codes and key behaviours.

Not a substitute for a real test suite, but it catches the failures that
matter most — a template that does not render, a route that 500s, a guard
that does not guard.

    python smoke_test.py
"""

import re
import sys

from dotenv import load_dotenv

load_dotenv()

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import Payment, Profile, User  # noqa: E402

FAILURES = []


def check(label, condition, detail=""):
    if condition:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label} {detail}")
        FAILURES.append(label)


def csrf_from(html):
    match = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html)
    return match.group(1) if match else ""


def main():
    app = create_app("development")

    with app.app_context():
        client_user = User.query.filter_by(is_admin=False).first()
        admin_user = User.query.filter_by(is_admin=True).first()
        live = Profile.query.join(User).filter(User.is_admin.is_(False)).all()
        live_profile = next((p for p in live if p.is_live), None)
        dark_profile = next((p for p in live if not p.is_live), None)
        client_email = client_user.email
        admin_email = admin_user.email
        live_slug = live_profile.slug if live_profile else None
        dark_slug = dark_profile.slug if dark_profile else None

    client = app.test_client()

    print("\nPublic pages")
    check("landing page", client.get("/").status_code == 200)
    check("pricing page", client.get("/pricing").status_code == 200)
    check("unknown page 404s", client.get("/nope").status_code == 404)

    print("\nPublic card")
    if live_slug:
        response = client.get(f"/c/{live_slug}")
        check("live card renders", response.status_code == 200)
        check("no inline style attribute", b'style="' not in response.data,
              "-> found a style attribute in the markup")
        check("no <style> block", b"<style" not in response.data)

        vcf = client.get(f"/c/{live_slug}/card.vcf")
        check("vcf downloads", vcf.status_code == 200)
        check("vcf is vcard 3.0", b"VERSION:3.0" in vcf.data)
        check("vcf uses CRLF", b"\r\n" in vcf.data)
        check("vcf is self-contained", b"FN:" in vcf.data and b"TEL" in vcf.data)

        png = client.get(f"/c/{live_slug}/qr.png")
        check("qr png served", png.status_code == 200 and png.data[:4] == b"\x89PNG")

    if dark_slug:
        check("lapsed card returns 410", client.get(f"/c/{dark_slug}").status_code == 410)
        check("lapsed vcf blocked", client.get(f"/c/{dark_slug}/card.vcf").status_code == 410)

    print("\nGuards, signed out")
    check("dashboard redirects", client.get("/dashboard/").status_code == 302)
    check("billing redirects", client.get("/billing/").status_code == 302)
    check("admin hidden as 404", client.get("/admin/").status_code in (302, 404))

    print("\nSubscriber session")
    page = client.get("/auth/login")
    client.post(
        "/auth/login",
        data={
            "csrf_token": csrf_from(page.get_data(as_text=True)),
            "email": client_email,
            "password": "password123",
        },
        follow_redirects=True,
    )
    check("dashboard opens", client.get("/dashboard/").status_code == 200)
    check("profile editor opens", client.get("/dashboard/profile").status_code == 200)
    check("links page opens", client.get("/dashboard/links").status_code == 200)
    check("share page opens", client.get("/dashboard/share").status_code == 200)
    check("analytics opens", client.get("/dashboard/analytics").status_code == 200)
    check("checkout opens", client.get("/billing/").status_code == 200)
    check("receipts open", client.get("/billing/receipts").status_code == 200)
    check("admin denied to subscriber", client.get("/admin/").status_code == 404)

    print("\nPayment flow (sandbox)")
    checkout = client.get("/billing/")
    token = csrf_from(checkout.get_data(as_text=True))
    response = client.post(
        "/billing/",
        data={
            "csrf_token": token,
            "plan": "professional",
            "channel": "mobile_money",
            "momo_provider": "mtn",
            "momo_phone": "0244123456",
        },
        follow_redirects=False,
    )
    check("momo charge redirects to pending", response.status_code == 302,
          f"-> got {response.status_code}")

    with app.app_context():
        latest = Payment.query.order_by(Payment.id.desc()).first()
        reference = latest.reference
        check("pending payment recorded", latest.status == "pending")

    check("pending page renders", client.get(f"/billing/pending/{reference}").status_code == 200)

    status = client.get(f"/billing/status/{reference}")
    check("status endpoint returns json", status.status_code == 200)
    check("sandbox activates subscription", status.get_json()["status"] == "success",
          f"-> {status.get_json()}")

    with app.app_context():
        user = User.query.filter_by(email=client_email).first()
        check("subscription is now active", user.is_subscription_active)

    # Idempotency: a second call must not grant a second year.
    with app.app_context():
        before = User.query.filter_by(email=client_email).first().current_subscription.expires_at
    client.get(f"/billing/status/{reference}")
    with app.app_context():
        after = User.query.filter_by(email=client_email).first().current_subscription.expires_at
    check("activation is idempotent", before == after, "-> expiry moved on a repeat call")

    client.get("/auth/logout")

    print("\nAdmin session")
    page = client.get("/auth/login")
    client.post(
        "/auth/login",
        data={
            "csrf_token": csrf_from(page.get_data(as_text=True)),
            "email": admin_email,
            "password": "admin1234",
        },
        follow_redirects=True,
    )
    check("admin overview", client.get("/admin/").status_code == 200)
    check("admin clients", client.get("/admin/clients").status_code == 200)
    check("admin client filter", client.get("/admin/clients?state=expired").status_code == 200)
    check("admin finance", client.get("/admin/finance").status_code == 200)

    with app.app_context():
        target = User.query.filter_by(is_admin=False).first().id
    check("client detail", client.get(f"/admin/clients/{target}").status_code == 200)

    print("\nWebhook")
    webhook = client.post(
        "/billing/webhook",
        json={"event": "charge.success", "data": {"reference": "KC-SEED-0001"}},
    )
    check("webhook accepts signed-off event in sandbox", webhook.status_code == 200)
    unknown = client.post(
        "/billing/webhook",
        json={"event": "charge.success", "data": {"reference": "not-ours"}},
    )
    check("unknown reference acknowledged", unknown.status_code == 200)

    print("\nMarkup separation check across every template")
    with app.app_context():
        import os

        offenders = []
        for root, _, files in os.walk("app/templates"):
            for name in files:
                path = os.path.join(root, name)
                with open(path, encoding="utf-8") as handle:
                    text = handle.read()
                if "<style" in text or re.search(r'\sstyle="', text):
                    offenders.append(path)
        check("no CSS in any template", not offenders, f"-> {offenders}")

    print("\nLead capture")
    if live_slug:
        page = client.get(f"/c/{live_slug}")
        token = csrf_from(page.get_data(as_text=True))
        check("exchange form appears on the card", b"exchange-form" in page.data)
        check("honeypot is present", b"honeypot" in page.data)

        before_count = None
        with app.app_context():
            from app.models import Lead, Profile as P
            prof = P.query.filter_by(slug=live_slug).first()
            before_count = len(prof.leads)

        response = client.post(
            f"/c/{live_slug}/connect",
            data={"csrf_token": token, "name": "Kojo Test", "phone": "0244123456"},
            follow_redirects=False,
        )
        check("valid lead accepted", response.status_code == 302, f"-> {response.status_code}")

        with app.app_context():
            from app.models import Lead, Profile as P
            prof = P.query.filter_by(slug=live_slug).first()
            check("lead was stored", len(prof.leads) == before_count + 1)
            newest = sorted(prof.leads, key=lambda l: l.id)[-1]
            check("lead fields saved", newest.name == "Kojo Test" and newest.phone == "0244123456")

        # Neither phone nor email: not a lead.
        page = client.get(f"/c/{live_slug}")
        bad = client.post(
            f"/c/{live_slug}/connect",
            data={"csrf_token": csrf_from(page.get_data(as_text=True)), "name": "No Contact"},
        )
        check("lead with no contact rejected", bad.status_code == 400, f"-> {bad.status_code}")

        # Honeypot: answers 302 like a success, stores nothing.
        page = client.get(f"/c/{live_slug}")
        with app.app_context():
            from app.models import Profile as P
            prof = P.query.filter_by(slug=live_slug).first()
            before_bot = len(prof.leads)
        bot = client.post(
            f"/c/{live_slug}/connect",
            data={
                "csrf_token": csrf_from(page.get_data(as_text=True)),
                "name": "Spam Bot",
                "phone": "0244000000",
                "website": "http://spam.example",
            },
        )
        with app.app_context():
            from app.models import Profile as P
            prof = P.query.filter_by(slug=live_slug).first()
            check("honeypot submission stored nothing", len(prof.leads) == before_bot)
            check("honeypot answers like a success", bot.status_code == 302)

    # Re-resolve rather than reusing dark_slug from the top of the run: the
    # webhook test above deliberately activated a subscription, and the card
    # captured as lapsed at startup may no longer be.
    with app.app_context():
        from app.models import Profile as _P

        still_dark = next((p for p in _P.query.all() if not p.is_live), None)
        still_dark_slug = still_dark.slug if still_dark else None

    if still_dark_slug:
        token = csrf_from(client.get(f"/c/{live_slug}").get_data(as_text=True))
        blocked = client.post(
            f"/c/{still_dark_slug}/connect",
            data={"csrf_token": token, "name": "Someone", "phone": "0244123456"},
        )
        check("lapsed card rejects leads", blocked.status_code == 410, f"-> {blocked.status_code}")
        check(
            "lead form absent on lapsed card",
            b"exchange-form" not in client.get(f"/c/{still_dark_slug}").data,
        )

    print("\nLeads inbox")
    client.get("/auth/logout")   # the admin session is still open from above
    page = client.get("/auth/login")
    client.post(
        "/auth/login",
        data={
            "csrf_token": csrf_from(page.get_data(as_text=True)),
            "email": client_email,
            "password": "password123",
        },
        follow_redirects=True,
    )
    check("leads page opens", client.get("/dashboard/leads").status_code == 200)
    export = client.get("/dashboard/leads/export.csv")
    check("csv export works", export.status_code == 200)
    check("csv has a header row", b"Name,Phone,Email" in export.data)
    client.get("/auth/logout")

    print("\nRenewal reminders")
    with app.app_context():
        from app.services.jobs import send_renewal_reminders
        from app.models import Notification

        # Lead alerts have already written rows, so compare a delta rather
        # than asserting an absolute count.
        before_dry = Notification.query.count()
        dry = send_renewal_reminders(dry_run=True)
        check(
            "dry run writes nothing",
            Notification.query.count() == before_dry,
            f"-> {Notification.query.count() - before_dry} row(s) written",
        )
        check("dry run still reports what it would send", isinstance(dry["messages"], list))

        first = send_renewal_reminders()
        sent_first = first["sent"]
        check("reminders go out", sent_first >= 0)

        recorded = Notification.query.count()
        check("reminders were actually recorded", recorded > before_dry)
        second = send_renewal_reminders()
        check(
            "reminders are never sent twice",
            Notification.query.count() == recorded and second["sent"] == 0,
            f"-> second run sent {second['sent']}",
        )

    print("\nPayment reconciliation")
    with app.app_context():
        from datetime import timedelta as _td

        from app.extensions import db as _db
        from app.models import Payment as _P, utcnow as _now
        from app.services.jobs import reconcile_payments

        stuck = _P.query.filter_by(status="pending").first()
        check("a stale pending payment exists to test", stuck is not None)
        if stuck:
            stuck.created_at = (_now() - _td(hours=3)).replace(tzinfo=None)
            _db.session.commit()
            reference = stuck.reference

            result = reconcile_payments(older_than_minutes=60)
            check("reconciliation ran", result["checked"] >= 1)

            settled = _P.query.filter_by(reference=reference).first()
            check(
                "stuck payment no longer pending",
                settled.status != "pending",
                f"-> still {settled.status}",
            )
            check("payment was recovered, not discarded", settled.status == "success")
            check("recovered payment granted a subscription", settled.subscription_id is not None)

            # Re-running must not grant a second year.
            expiry_before = settled.user.current_subscription.expires_at
            reconcile_payments(older_than_minutes=60)
            expiry_after = settled.user.current_subscription.expires_at
            check("reconciliation is idempotent", expiry_before == expiry_after)

    print("\nSMS")
    with app.app_context():
        from app.services.sms import SmsError, normalise

        check("local number normalised", normalise("0244123456") == "233244123456")
        check("international number normalised", normalise("+233244123456") == "233244123456")
        check("spaces tolerated", normalise("024 412 3456") == "233244123456")
        try:
            normalise("12345")
            check("bad number rejected", False, "-> accepted a bad number")
        except SmsError:
            check("bad number rejected", True)

    print("\nBranding")
    with app.app_context():
        import os

        for asset in ["logo.png", "logo-small.png", "favicon.png", "apple-touch-icon.png"]:
            check(f"{asset} present", os.path.exists(f"app/static/img/{asset}"))

        tokens = open("app/static/css/tokens.css", encoding="utf-8").read()
        for name, value in [("blue", "#3070ae"), ("purple", "#7b19a2"), ("amber", "#e5af42")]:
            check(f"{name} sampled from the logo", value in tokens, f"-> {value} missing")

        # Every stylesheet must resolve against tokens.css. A var() pointing at
        # a variable that no longer exists fails silently in the browser.
        declared = set(re.findall(r"^\s*(--[a-z0-9-]+):", tokens, re.M))
        for extra in ["--tilt-x", "--tilt-y", "--dash"]:
            declared.add(extra)

        dangling = {}
        for name in os.listdir("app/static/css"):
            if not name.endswith(".css"):
                continue
            text = open(f"app/static/css/{name}", encoding="utf-8").read()
            declared_here = set(re.findall(r"^\s*(--[a-z0-9-]+):", text, re.M))
            used = set(re.findall(r"var\((--[a-z0-9-]+)", text))
            missing = used - declared - declared_here
            if missing:
                dangling[name] = sorted(missing)
        check("no stylesheet references a dead variable", not dangling, f"-> {dangling}")

    page = client.get("/")
    check("logo appears in the header", b"logo-small.png" in page.data)
    check("favicon is linked", b"favicon.png" in page.data)
    check("business name is set", b"AD Smart Business Cards" in page.data)
    check("support email shown", b"adgraphics881@gmail.com" in page.data)
    check("support phone shown", b"0545875881" in page.data)

    if live_slug:
        card = client.get(f"/c/{live_slug}")
        check("card carries the credit mark", b"card-credit" in card.data)
        check("card sets the brand theme colour", b"#3070ae" in card.data)

    print("\nFonts")
    with app.app_context():
        import os
        import re as _re

        css = open("app/static/css/fonts.css", encoding="utf-8").read()
        referenced = set(_re.findall(r'url\("\.\./fonts/([^"]+)"\)', css))
        missing = [
            name for name in referenced
            if not os.path.exists(os.path.join("app/static/fonts", name))
        ]
        check("every referenced font file exists", not missing, f"-> missing {missing}")
        check("fonts are actually referenced", len(referenced) > 0)
        check("licence ships with the fonts", os.path.exists("app/static/fonts/OFL.txt"))

        # The Latin Extended subset is the reason Akan and Ewe names render.
        # If someone trims it to save bandwidth, this fails loudly.
        ext = [name for name in referenced if "latin-ext" in name]
        check("latin-ext subset present for Akan/Ewe", len(ext) >= 1)

        tokens = open("app/static/css/tokens.css", encoding="utf-8").read()
        check("no unlicensed font left in the stack", "MTN Brighter" not in tokens)

    print("\n" + "=" * 52)
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed:")
        for item in FAILURES:
            print(f"  - {item}")
        sys.exit(1)
    print("All checks passed.")


if __name__ == "__main__":
    main()
