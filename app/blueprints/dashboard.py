"""The subscriber's own workspace."""

import os
import secrets
from datetime import timedelta

import csv
import io

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required
from werkzeug.utils import secure_filename

from ..extensions import db
from ..forms import ProfileForm, SocialLinkForm
from ..models import CardView, Lead, Profile, SocialLink, utcnow
from ..utils.analytics import daily_series
from ..utils.qr import qr_svg

bp = Blueprint("dashboard", __name__, url_prefix="/dashboard")


def _plan_limits():
    sub = current_user.current_subscription
    plans = current_app.config["PLANS"]
    if sub and sub.plan in plans:
        return plans[sub.plan]
    return plans["starter"]


def _card_url(profile):
    return current_app.config["SITE_URL"].rstrip("/") + url_for(
        "cards.show", slug=profile.slug
    )


def _save_avatar(file_storage):
    """Store an uploaded photo under a random filename.

    The original filename is never reused. A user-supplied name can collide
    with someone else's file or carry a path traversal attempt.
    """
    extension = file_storage.filename.rsplit(".", 1)[-1].lower()
    if extension not in current_app.config["ALLOWED_IMAGE_EXTENSIONS"]:
        return None
    filename = secure_filename(f"{secrets.token_hex(12)}.{extension}")
    folder = current_app.config["UPLOAD_FOLDER"]
    os.makedirs(folder, exist_ok=True)
    file_storage.save(os.path.join(folder, filename))
    return filename


@bp.route("/")
@login_required
def index():
    profile = current_user.profile
    if profile is None:
        return redirect(url_for("dashboard.edit_profile"))

    since = utcnow() - timedelta(days=30)
    recent = [v for v in profile.views if v.viewed_at and v.viewed_at >= since.replace(tzinfo=None)]
    series = daily_series(recent, since, 30)
    saves = sum(1 for v in recent if v.action == "vcf")

    return render_template(
        "dashboard/index.html",
        profile=profile,
        card_url=_card_url(profile),
        series=series,
        series_max=max(series.values()) if series else 0,
        views_30=len(recent),
        saves_30=saves,
        subscription=current_user.current_subscription,
        days_left=current_user.days_to_expiry,
        notice_days=current_app.config["RENEWAL_NOTICE_DAYS"],
    )

@bp.route("/profile", methods=["GET", "POST"])
@login_required
def edit_profile():
    profile = current_user.profile

    form = ProfileForm(
        obj=profile,
        original_slug=profile.slug if profile else None,
    )

    if form.validate_on_submit():
        if profile is None:
            profile = Profile(user=current_user)
            db.session.add(profile)

        form.populate_obj(profile)

        if form.avatar.data:
            filename = _save_avatar(form.avatar.data)
            if filename:
                profile.avatar_filename = filename

        form.avatar.data = None

        db.session.commit()

        flash("Card saved.", "success")
        return redirect(url_for("dashboard.index"))

    return render_template(
        "dashboard/profile.html",
        form=form,
        profile=profile,
        card_url=_card_url(profile) if profile else "",
    )

@bp.route("/links", methods=["GET", "POST"])
@login_required
def links():
    profile = current_user.profile
    if profile is None:
        return redirect(url_for("dashboard.edit_profile"))
    form = SocialLinkForm()
    limits = _plan_limits()

    if form.validate_on_submit():
        if len(profile.social_links) >= limits["max_social_links"]:
            flash(
                f"The {limits['label']} plan holds {limits['max_social_links']} links. "
                "Upgrade to add more.",
                "warning",
            )
        else:
            db.session.add(
                SocialLink(
                    profile=profile,
                    platform=form.platform.data,
                    url=form.url.data.strip(),
                    position=len(profile.social_links),
                )
            )
            db.session.commit()
            flash("Link added.", "success")
        return redirect(url_for("dashboard.links"))

    return render_template(
        "dashboard/links.html",
        form=form,
        profile=profile,
        limit=limits["max_social_links"],
        used=len(profile.social_links),
    )


@bp.route("/links/<int:link_id>/delete", methods=["POST"])
@login_required
def delete_link(link_id):
    link = db.session.get(SocialLink, link_id)
    if link is None or link.profile.user_id != current_user.id:
        abort(404)
    db.session.delete(link)
    db.session.commit()
    flash("Link removed.", "info")
    return redirect(url_for("dashboard.links"))


@bp.route("/share")
@login_required
def share():
    profile = current_user.profile
    if profile is None:
        return redirect(url_for("dashboard.edit_profile"))
    url = _card_url(profile)
    return render_template(
        "dashboard/share.html",
        profile=profile,
        card_url=url,
        qr=qr_svg(url, scale=7),
        vcf_url=current_app.config["SITE_URL"].rstrip("/")
        + url_for("cards.vcf", slug=profile.slug),
        is_live=profile.is_live,
    )


@bp.route("/analytics")
@login_required
def analytics():
    profile = current_user.profile
    if profile is None:
        return redirect(url_for("dashboard.edit_profile"))
    since = utcnow() - timedelta(days=90)
    rows = [v for v in profile.views if v.viewed_at and v.viewed_at >= since.replace(tzinfo=None)]
    series = daily_series(rows, since, 90)

    by_source = {}
    for view in rows:
        by_source[view.source] = by_source.get(view.source, 0) + 1

    unique = len({v.visitor_hash for v in rows if v.visitor_hash})

    return render_template(
        "dashboard/analytics.html",
        profile=profile,
        series=series,
        series_max=max(series.values()) if series else 0,
        total=len(rows),
        unique=unique,
        saves=sum(1 for v in rows if v.action == "vcf"),
        by_source=by_source,
    )


@bp.route("/leads")
@login_required
def leads():
    """People who sent their details back through the card."""
    profile = current_user.profile
    if profile is None:
        # Administrators and half-finished signups have no card. Send them to
        # build one rather than crashing on a missing relationship.
        return redirect(url_for("dashboard.edit_profile"))
    rows = sorted(profile.leads, key=lambda lead: lead.created_at, reverse=True)

    since = (utcnow() - timedelta(days=30)).replace(tzinfo=None)
    recent = [lead for lead in rows if lead.created_at >= since]

    by_source = {}
    for lead in rows:
        by_source[lead.source] = by_source.get(lead.source, 0) + 1

    unread = [lead for lead in rows if not lead.is_read]

    # Opening the page is what marks them read. A separate "mark as read"
    # button is one more thing to click and nobody ever clicks it.
    if unread:
        for lead in unread:
            lead.is_read = True
        db.session.commit()

    return render_template(
        "dashboard/leads.html",
        leads=rows,
        recent_count=len(recent),
        unread_count=len(unread),
        by_source=by_source,
        total=len(rows),
    )


@bp.route("/leads/export.csv")
@login_required
def export_leads():
    """Download every lead as CSV.

    Their contacts, not ours. Anyone should be able to take this data to a
    spreadsheet or a CRM without asking us for it — and without an export
    route, a lapsed subscriber loses access to contacts they collected.
    """
    profile = current_user.profile
    if profile is None:
        return redirect(url_for("dashboard.edit_profile"))
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["Date", "Name", "Phone", "Email", "Organisation", "Note", "Came via"]
    )
    for lead in sorted(profile.leads, key=lambda row: row.created_at, reverse=True):
        writer.writerow(
            [
                lead.created_at.strftime("%Y-%m-%d %H:%M"),
                lead.name,
                lead.phone or "",
                lead.email or "",
                lead.organisation or "",
                (lead.note or "").replace("\n", " "),
                lead.source,
            ]
        )

    filename = f"{profile.slug}-contacts.csv"
    return Response(
        buffer.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@bp.route("/leads/<int:lead_id>/delete", methods=["POST"])
@login_required
def delete_lead(lead_id):
    lead = db.session.get(Lead, lead_id)
    if lead is None or lead.profile.user_id != current_user.id:
        abort(404)
    db.session.delete(lead)
    db.session.commit()
    flash("Contact removed.", "info")
    return redirect(url_for("dashboard.leads"))
