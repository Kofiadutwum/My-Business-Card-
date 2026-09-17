"""Background jobs, run from cron.

Two jobs, both idempotent, both safe to run more often than needed.

    send_renewal_reminders()  -- warn people before their card goes dark
    reconcile_payments()      -- rescue payments whose webhook never arrived

Idempotence is the whole design constraint here. Cron double-fires, servers
run two workers, and someone always runs the command by hand to see what it
does. None of that may text a customer twice or grant a second year.
"""

from datetime import timedelta

from flask import current_app, url_for

from ..extensions import db
from ..models import Notification, Payment, Subscription, utcnow
from ..services import paystack, sms
from ..services.paystack import PaymentError
from ..services.sms import SmsError

# Reminder schedule. Days remaining -> notification kind.
# Three points, not six: past a certain frequency reminders read as spam and
# people stop opening them, which costs you the one message that mattered.
SCHEDULE = [
    (30, "renewal_30"),
    (7, "renewal_7"),
    (1, "renewal_1"),
]


def _recipient(user):
    """Best number to reach a subscriber on.

    The profile phone is the one they gave for their card, so it is the one
    they actually carry. The MoMo number from their last payment is the
    fallback, since it is demonstrably live.
    """
    if user.profile and user.profile.phone:
        return user.profile.phone
    if user.profile and user.profile.whatsapp:
        return user.profile.whatsapp
    for payment in user.payments:
        if payment.momo_phone:
            return payment.momo_phone
    return None


def _first_name(user):
    if user.profile and user.profile.full_name:
        return user.profile.full_name.split()[0]
    return "there"


def _message(kind, user, subscription):
    """Message templates, each written to fit one 160-character SMS segment.

    Every template names the expiry date and gives one link. A reminder that
    does not say when, or that makes the person hunt for where to pay, is a
    reminder that gets ignored.
    """
    site = current_app.config["SITE_NAME"]
    link = current_app.config["SITE_URL"].rstrip("/") + "/billing/"
    name = _first_name(user)
    expires = subscription.expires_at.strftime("%d %b")

    # The site name goes at the front as a label rather than inside the
    # sentence. Written as "your {site} card" it reads "your Kuul Card card"
    # for any brand whose name already contains the word.
    templates = {
        "renewal_30": (
            f"{site}: Hi {name}, your card expires on {expires}. Renew early and "
            f"the year is added to that date, not today: {link}"
        ),
        "renewal_7": (
            f"{site}: Hi {name}, 7 days left on your card (expires {expires}). "
            f"Renew here to keep it live: {link}"
        ),
        "renewal_1": (
            f"{site}: Hi {name}, your card expires tomorrow ({expires}). Renew "
            f"now and nothing goes offline: {link}"
        ),
        "expired": (
            f"{site}: Hi {name}, your card is now offline. Your details are "
            f"saved - renewing puts the same link back: {link}"
        ),
    }
    body = templates[kind]

    # Templates are written to fit one 160-character segment. A brand name or
    # domain longer than the defaults can push them over, so this is asserted
    # in development rather than discovered on the bill.
    if current_app.debug and len(body) > 160:
        current_app.logger.warning(
            "Reminder template '%s' is %d characters and will bill as 2 segments.",
            kind,
            len(body),
        )
    return body


def _already_sent(subscription_id, kind):
    return (
        Notification.query.filter_by(subscription_id=subscription_id, kind=kind).first()
        is not None
    )


def _record(user, subscription, kind, destination, body, status,
            provider=None, reference=None, error=None):
    notification = Notification(
        user_id=user.id,
        subscription_id=subscription.id if subscription else None,
        kind=kind,
        channel="sms",
        destination=destination,
        body=body,
        status=status,
        provider=provider,
        provider_ref=reference,
        error=(error or "")[:255] or None,
    )
    db.session.add(notification)
    db.session.commit()
    return notification


def send_renewal_reminders(dry_run=False):
    """Warn subscribers before their card goes dark, and once after.

    Returns a summary dict so the CLI can print something useful.
    """
    now = utcnow()
    naive_now = now.replace(tzinfo=None)
    grace = current_app.config["GRACE_PERIOD_DAYS"]

    summary = {"sent": 0, "failed": 0, "skipped": 0, "no_number": 0, "messages": []}

    for subscription in Subscription.query.filter(Subscription.cancelled_at.is_(None)).all():
        user = subscription.user
        if user is None or user.is_admin or user.is_suspended:
            continue

        days_left = (subscription.expires_at - naive_now).days
        kind = None

        for threshold, candidate in SCHEDULE:
            # A window, not an equality test. If cron misses a night, the
            # reminder still goes out the following day rather than never.
            if 0 <= days_left <= threshold:
                kind = candidate
                break

        # The card actually goes dark at the end of the grace period, so the
        # 'gone offline' message belongs there, not on the expiry date.
        if days_left < 0 and abs(days_left) >= grace:
            kind = "expired"

        if kind is None:
            continue

        if _already_sent(subscription.id, kind):
            summary["skipped"] += 1
            continue

        # Only the most urgent unsent reminder goes out per run. Without this,
        # a subscriber added at 5 days remaining gets the 30-day and 7-day
        # messages in the same minute.
        number = _recipient(user)
        if not number:
            summary["no_number"] += 1
            continue

        body = _message(kind, user, subscription)
        summary["messages"].append((user.email, kind, number, body))

        if dry_run:
            continue

        try:
            provider, reference, _ = sms.send(number, body)
            _record(user, subscription, kind, number, body, "sent", provider, reference)
            summary["sent"] += 1
        except SmsError as exc:
            # Failures are recorded too, which means a failed send is not
            # retried forever — but it is visible, which matters more.
            _record(user, subscription, kind, number, body, "failed", error=str(exc))
            summary["failed"] += 1

    return summary


def reconcile_payments(older_than_minutes=60, abandon_after_hours=24):
    """Rescue payments whose webhook never arrived.

    This is not optional. Webhooks get dropped: the server restarts mid-request,
    the network hiccups, a deploy takes the endpoint down for nine seconds.
    When that happens the customer has paid, the payment sits on 'pending'
    forever, and their card stays dark until they phone you angry.

    This job asks the gateway directly about every stale pending payment and
    settles it. Run it every fifteen minutes.
    """
    from ..blueprints.billing import _activate

    now = utcnow()
    cutoff = (now - timedelta(minutes=older_than_minutes)).replace(tzinfo=None)
    abandon_cutoff = (now - timedelta(hours=abandon_after_hours)).replace(tzinfo=None)

    summary = {"checked": 0, "recovered": 0, "failed": 0, "abandoned": 0, "unresolved": 0}

    stale = Payment.query.filter(
        Payment.status == "pending", Payment.created_at <= cutoff
    ).all()

    for payment in stale:
        summary["checked"] += 1
        try:
            data = paystack.verify(payment.reference)
        except PaymentError:
            # The gateway is unreachable right now. Leave it pending; the next
            # run will try again. Never mark a payment failed because we could
            # not ask — that is how a paying customer loses their card.
            summary["unresolved"] += 1
            continue

        status = (data.get("status") or "").lower()

        if status == "success":
            payment.gateway_fee_minor = data.get("fees") or 0
            _activate(payment)
            summary["recovered"] += 1
            _notify_recovered(payment)
        elif status in ("failed", "reversed"):
            payment.status = "failed"
            db.session.commit()
            summary["failed"] += 1
        elif payment.created_at <= abandon_cutoff:
            # A mobile money prompt nobody ever approved. Closed off so it
            # stops appearing on the finance page as an open item.
            payment.status = "abandoned"
            db.session.commit()
            summary["abandoned"] += 1
        else:
            summary["unresolved"] += 1

    return summary


def _notify_recovered(payment):
    """Tell someone their card is live after a rescued payment.

    Worth the message. From the customer's side their payment appeared to
    fail, and they may already be composing a complaint.
    """
    number = _recipient(payment.user)
    if not number:
        return
    site = current_app.config["SITE_NAME"]
    body = (
        f"{site}: Hi {_first_name(payment.user)}, your payment has cleared and "
        f"your card is live again. Sorry for the delay."
    )
    try:
        provider, reference, _ = sms.send(number, body)
        # subscription=None deliberately. The once-only unique constraint is
        # keyed on (subscription_id, kind) and exists to stop duplicate
        # *reminders*. Transactional messages like this one can legitimately
        # recur, and a NULL subscription_id sits outside that constraint.
        _record(payment.user, None, "recovered", number, body, "sent", provider, reference)
    except SmsError:
        pass  # the card is live either way; a missed courtesy text is not fatal


def notify_new_lead(lead):
    """Text the card owner when a visitor sends their details back.

    Immediacy is the point. The visitor is standing in front of them right
    now; a notification that arrives tomorrow is worth a fraction of one that
    arrives before the conversation ends.
    """
    user = lead.profile.user
    if not user.is_subscription_active:
        return
    number = _recipient(user)
    if not number:
        return

    site = current_app.config["SITE_NAME"]
    contact = lead.phone or lead.email or "no contact given"
    body = f"{site}: {lead.name} shared their details from your card. {contact}"

    try:
        provider, reference, _ = sms.send(number, body)
        # subscription=None for the same reason as above: leads recur, and
        # this message must never be suppressed as a duplicate reminder.
        _record(user, None, "lead", number, body, "sent", provider, reference)
    except SmsError as exc:
        current_app.logger.warning("Lead notification failed: %s", exc)
