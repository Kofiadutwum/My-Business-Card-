"""Administrator area: who the clients are, and what the business earns."""

from collections import OrderedDict
from datetime import timedelta

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from sqlalchemy import func

from ..extensions import db
from ..models import CardView, Payment, Profile, Subscription, User, utcnow
from ..utils.analytics import daily_series, monthly_totals
from ..utils.decorators import admin_required

bp = Blueprint("admin", __name__, url_prefix="/admin")


def _money(minor):
    return minor / 100


@bp.route("/")
@admin_required
def dashboard():
    now = utcnow()
    naive_now = now.replace(tzinfo=None)

    users = User.query.filter_by(is_admin=False).all()
    subscriptions = Subscription.query.all()
    payments = Payment.query.filter_by(status="success").all()

    active = [s for s in subscriptions if s.status == "active"]
    grace = [s for s in subscriptions if s.status == "grace"]
    expired = [s for s in subscriptions if s.status == "expired"]

    # Expiring inside the notice window: the renewal chase list.
    notice = current_app.config["RENEWAL_NOTICE_DAYS"]
    expiring_soon = sorted(
        [
            s
            for s in active
            if (s.expires_at - naive_now).days <= notice and (s.expires_at - naive_now).days >= 0
        ],
        key=lambda s: s.expires_at,
    )

    gross = sum(p.amount_minor for p in payments)
    fees = sum(p.gateway_fee_minor or 0 for p in payments)

    # Signups over 30 days
    since = now - timedelta(days=30)
    recent_users = [u for u in users if u.created_at and u.created_at >= since.replace(tzinfo=None)]
    signup_series = daily_series(recent_users, since, 30, date_attr="created_at")

    revenue_series = monthly_totals(payments, months=12)

    # Channel split tells you what to optimise. If MoMo is 80% of volume,
    # a broken MoMo flow is a business outage, not an edge case.
    channel_split = {}
    for payment in payments:
        key = payment.momo_provider or payment.channel
        channel_split[key] = channel_split.get(key, 0) + payment.amount_minor

    plan_split = {}
    for subscription in active:
        plan_split[subscription.plan] = plan_split.get(subscription.plan, 0) + 1

    total_views = db.session.query(func.count(CardView.id)).scalar() or 0

    retention = round(len(active) / len(users) * 100, 1) if users else 0.0

    return render_template(
        "admin/dashboard.html",
        total_users=len(users),
        active_count=len(active),
        grace_count=len(grace),
        expired_count=len(expired),
        expiring_soon=expiring_soon[:10],
        gross=_money(gross),
        net=_money(gross - fees),
        fees=_money(fees),
        arpu=_money(gross / len(users)) if users else 0,
        signup_series=signup_series,
        signup_max=max(signup_series.values()) if signup_series else 0,
        revenue_series=revenue_series,
        revenue_max=max(revenue_series.values()) if revenue_series else 0,
        channel_split=channel_split,
        channel_total=sum(channel_split.values()) or 1,
        plan_split=plan_split,
        plans=current_app.config["PLANS"],
        total_views=total_views,
        retention=retention,
        currency=current_app.config["CURRENCY"],
    )


@bp.route("/clients")
@admin_required
def clients():
    query = User.query.filter_by(is_admin=False)

    search = (request.args.get("q") or "").strip()
    state = request.args.get("state", "all")

    if search:
        query = query.filter(User.email.ilike(f"%{search}%"))

    users = query.order_by(User.created_at.desc()).all()

    if state == "active":
        users = [u for u in users if u.is_subscription_active]
    elif state == "expired":
        users = [u for u in users if not u.is_subscription_active]

    rows = []
    for user in users:
        subscription = user.current_subscription
        rows.append(
            {
                "user": user,
                "profile": user.profile,
                "subscription": subscription,
                "status": subscription.status if subscription else "none",
                "spend": _money(
                    sum(p.amount_minor for p in user.payments if p.status == "success")
                ),
                "views": user.profile.view_count if user.profile else 0,
            }
        )

    return render_template(
        "admin/clients.html",
        rows=rows,
        search=search,
        state=state,
        currency=current_app.config["CURRENCY"],
    )


@bp.route("/clients/<int:user_id>")
@admin_required
def client_detail(user_id):
    user = db.session.get(User, user_id)
    if user is None or user.is_admin:
        abort(404)
    return render_template(
        "admin/client_detail.html",
        client=user,
        currency=current_app.config["CURRENCY"],
        plans=current_app.config["PLANS"],
    )


@bp.route("/clients/<int:user_id>/suspend", methods=["POST"])
@admin_required
def toggle_suspend(user_id):
    user = db.session.get(User, user_id)
    if user is None or user.is_admin:
        abort(404)
    user.is_suspended = not user.is_suspended
    db.session.commit()
    flash(
        f"{user.email} is now {'suspended' if user.is_suspended else 'active'}.",
        "info",
    )
    return redirect(url_for("admin.client_detail", user_id=user.id))


@bp.route("/finance")
@admin_required
def finance():
    payments = Payment.query.order_by(Payment.created_at.desc()).all()
    successful = [p for p in payments if p.status == "success"]

    gross = sum(p.amount_minor for p in successful)
    fees = sum(p.gateway_fee_minor or 0 for p in successful)

    by_month = OrderedDict()
    for payment in successful:
        if not payment.paid_at:
            continue
        key = payment.paid_at.strftime("%Y-%m")
        bucket = by_month.setdefault(
            key, {"gross": 0, "fees": 0, "count": 0, "card": 0, "momo": 0}
        )
        bucket["gross"] += payment.amount_minor
        bucket["fees"] += payment.gateway_fee_minor or 0
        bucket["count"] += 1
        if payment.channel == "card":
            bucket["card"] += payment.amount_minor
        else:
            bucket["momo"] += payment.amount_minor

    by_month = OrderedDict(sorted(by_month.items(), reverse=True))

    failed = [p for p in payments if p.status == "failed"]
    pending = [p for p in payments if p.status == "pending"]

    # Deferred revenue: cash collected for service not yet delivered. Annual
    # billing means most of today's receipts are still an obligation, and
    # treating the whole balance as earned is how subscription businesses
    # mislead themselves about how much money they actually have.
    deferred = 0
    naive_now = utcnow().replace(tzinfo=None)
    for subscription in Subscription.query.all():
        if subscription.status not in ("active", "grace"):
            continue
        plan = current_app.config["PLANS"].get(subscription.plan)
        if not plan:
            continue
        total_days = max((subscription.expires_at - subscription.starts_at).days, 1)
        remaining = max((subscription.expires_at - naive_now).days, 0)
        deferred += int(plan["amount_minor"] * remaining / total_days)

    return render_template(
        "admin/finance.html",
        payments=payments[:100],
        by_month=by_month,
        gross=_money(gross),
        fees=_money(fees),
        net=_money(gross - fees),
        deferred=_money(deferred),
        recognised=_money(gross - deferred),
        success_rate=round(len(successful) / len(payments) * 100, 1) if payments else 0,
        failed_count=len(failed),
        pending_count=len(pending),
        currency=current_app.config["CURRENCY"],
    )
