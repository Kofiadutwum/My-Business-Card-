"""Administrator area: who the clients are, and what the business earns."""

from collections import OrderedDict
from datetime import timedelta
import os
import uuid

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)
from werkzeug.utils import secure_filename
from sqlalchemy import func

from ..extensions import db
from ..models import (
    CardView,
    GalleryImage,
    Payment,
    Profile,
    Subscription,
    User,
    utcnow,
)
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

# ---------------------------------------------------------------------------
# Homepage gallery
# ---------------------------------------------------------------------------

ALLOWED_GALLERY_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}


def _gallery_upload_folder():
    """Return the persistent folder used for homepage gallery images."""
    folder = os.path.join(current_app.config["UPLOAD_FOLDER"], "gallery")
    os.makedirs(folder, exist_ok=True)
    return folder


def _gallery_extension(filename):
    """Return a safe lowercase image extension, or None."""
    filename = secure_filename(filename or "")
    if "." not in filename:
        return None

    extension = filename.rsplit(".", 1)[1].lower()

    if extension not in ALLOWED_GALLERY_EXTENSIONS:
        return None

    return extension


@bp.route("/gallery", methods=["GET", "POST"])
@admin_required
def gallery():
    if request.method == "POST":
        image = request.files.get("image")

        if not image or not image.filename:
            flash("Please select an image to upload.", "error")
            return redirect(url_for("admin.gallery"))

        extension = _gallery_extension(image.filename)

        if not extension:
            flash("Only JPG, JPEG, PNG and WebP images are allowed.", "error")
            return redirect(url_for("admin.gallery"))

        original_name = secure_filename(image.filename)
        filename = f"{uuid.uuid4().hex}.{extension}"

        upload_folder = _gallery_upload_folder()
        image.save(os.path.join(upload_folder, filename))

        try:
            display_order = int(request.form.get("display_order", 0))
        except (TypeError, ValueError):
            display_order = 0

        title = (request.form.get("title") or "").strip()
        description = (request.form.get("description") or "").strip()

        gallery_image = GalleryImage(
            filename=filename,
            title=title or None,
            description=description or None,
            display_order=display_order,
            is_published=True,
        )

        db.session.add(gallery_image)
        db.session.commit()

        flash(f"{original_name} was added to the homepage gallery.", "success")
        return redirect(url_for("admin.gallery"))

    images = GalleryImage.query.order_by(
        GalleryImage.display_order.asc(),
        GalleryImage.id.asc(),
    ).all()

    return render_template(
        "admin/gallery.html",
        images=images,
    )


@bp.route("/gallery/<int:image_id>/toggle", methods=["POST"])
@admin_required
def toggle_gallery_image(image_id):
    image = db.session.get(GalleryImage, image_id)

    if image is None:
        abort(404)

    image.is_published = not image.is_published
    db.session.commit()

    state = "published" if image.is_published else "hidden"
    flash(f"Gallery image is now {state}.", "info")

    return redirect(url_for("admin.gallery"))


@bp.route("/gallery/<int:image_id>/delete", methods=["POST"])
@admin_required
def delete_gallery_image(image_id):
    image = db.session.get(GalleryImage, image_id)

    if image is None:
        abort(404)

    upload_folder = _gallery_upload_folder()
    file_path = os.path.join(upload_folder, image.filename)

    if os.path.isfile(file_path):
        os.remove(file_path)

    db.session.delete(image)
    db.session.commit()

    flash("Gallery image deleted.", "success")

    return redirect(url_for("admin.gallery"))


@bp.route("/gallery/image/<filename>")
def gallery_image(filename):
    """Serve a gallery image from the dedicated upload folder."""
    filename = secure_filename(filename)

    if not filename:
        abort(404)

    return send_from_directory(
        _gallery_upload_folder(),
        filename,
    )