"""Public-facing card routes.

These are the only pages a recipient ever sees, and they are the pages that
carry the subscription rule: a card whose plan has lapsed stops resolving.
"""

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    render_template,
    request,
    url_for,
)

from datetime import timedelta

from flask import flash, redirect, url_for

from ..extensions import db
from ..forms import LeadForm
from ..models import CardView, Lead, Profile, utcnow
from ..services.jobs import notify_new_lead
from ..utils.analytics import visitor_hash
from ..utils.qr import qr_png_bytes, qr_svg
from ..utils.vcard import build_vcard, vcard_filename

bp = Blueprint("cards", __name__)


def _record(profile, action="view"):
    """Log the open. Failure here must never break the card.

    Analytics are secondary to the card rendering. If the write fails the
    visitor should still get the contact details, so the exception is
    swallowed deliberately rather than allowed to 500 the page.
    """
    source = request.args.get("s", "link")
    if source not in ("link", "qr", "nfc"):
        source = "link"
    try:
        db.session.add(
            CardView(
                profile=profile,
                source=source,
                action=action,
                visitor_hash=visitor_hash(),
                user_agent=(request.headers.get("User-Agent") or "")[:255],
            )
        )
        db.session.commit()
    except Exception:
        db.session.rollback()


def _lookup(slug):
    profile = Profile.query.filter_by(slug=slug.lower()).first()
    if profile is None:
        abort(404)
    return profile


@bp.route("/c/<slug>")
def show(slug):
    profile = _lookup(slug)

    if not profile.is_live:
        # 410 Gone, not 404. The card existed and may return after renewal,
        # and the status code tells crawlers exactly that.
        return (
            render_template("cards/inactive.html", profile=profile),
            410,
        )

    _record(profile, "view")
    card_url = current_app.config["SITE_URL"].rstrip("/") + url_for("cards.show", slug=profile.slug)

    return render_template(
        "cards/card.html",
        profile=profile,
        card_url=card_url,
        qr=qr_svg(card_url, scale=5),
        vcf_url=url_for("cards.vcf", slug=profile.slug),
        lead_form=LeadForm(),
    )


@bp.route("/c/<slug>/card.vcf")
def vcf(slug):
    profile = _lookup(slug)
    if not profile.is_live:
        abort(410)

    _record(profile, "vcf")

    photo_url = None
    if profile.avatar_filename:
        photo_url = current_app.config["SITE_URL"].rstrip("/") + url_for(
            "static", filename=f"img/avatars/{profile.avatar_filename}"
        )

    card_url = current_app.config["SITE_URL"].rstrip("/") + url_for("cards.show", slug=profile.slug)
    payload = build_vcard(profile, card_url=card_url, photo_url=photo_url)

    return Response(
        payload,
        mimetype="text/vcard; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{vcard_filename(profile)}"',
            "Cache-Control": "no-store",
        },
    )


@bp.route("/c/<slug>/qr.png")
def qr_png(slug):
    profile = _lookup(slug)
    if not profile.is_live:
        abort(410)
    card_url = current_app.config["SITE_URL"].rstrip("/") + url_for(
        "cards.show", slug=profile.slug
    )
    return Response(
        qr_png_bytes(card_url + "?s=qr", scale=12),
        mimetype="image/png",
        headers={"Content-Disposition": f'inline; filename="{profile.slug}-qr.png"'},
    )


@bp.route("/c/<slug>/connect", methods=["POST"])
def connect(slug):
    """A visitor sends their own details back to the card owner.

    This is the exchange a paper card cannot do. You hand someone a printed
    card and hope they call; here they can push their number to you while you
    are still standing together.

    Three defences, in order of how often they matter:
      1. A honeypot field, which catches naive bots for free.
      2. A per-visitor rate limit, so one person cannot flood an inbox.
      3. Length caps in the form, so nobody stores an essay.
    """
    profile = _lookup(slug)
    if not profile.is_live:
        abort(410)

    form = LeadForm()

    if form.is_bot:
        # Answer as though it worked. A bot told it failed simply retries
        # with the honeypot left empty.
        flash("Thank you. Your details have been sent.", "success")
        return redirect(url_for("cards.show", slug=profile.slug))

    if not form.validate_on_submit():
        card_url = current_app.config["SITE_URL"].rstrip("/") + url_for(
            "cards.show", slug=profile.slug
        )
        return (
            render_template(
                "cards/card.html",
                profile=profile,
                card_url=card_url,
                qr=qr_svg(card_url, scale=5),
                vcf_url=url_for("cards.vcf", slug=profile.slug),
                lead_form=form,
                open_form=True,
            ),
            400,
        )

    fingerprint = visitor_hash()
    since = (utcnow() - timedelta(hours=1)).replace(tzinfo=None)
    recent = Lead.query.filter(
        Lead.profile_id == profile.id,
        Lead.visitor_hash == fingerprint,
        Lead.created_at >= since,
    ).count()

    if recent >= 3:
        flash("You have already sent your details. Give it an hour.", "warning")
        return redirect(url_for("cards.show", slug=profile.slug))

    source = request.args.get("s", "link")
    lead = Lead(
        profile=profile,
        name=form.name.data.strip(),
        phone=form.phone.data or None,
        email=(form.email.data or "").strip().lower() or None,
        organisation=(form.organisation.data or "").strip() or None,
        note=(form.note.data or "").strip() or None,
        source=source if source in ("link", "qr", "nfc") else "link",
        visitor_hash=fingerprint,
    )
    db.session.add(lead)
    db.session.commit()

    # Notified after the commit, never before. A failing SMS gateway must not
    # cost the card owner the lead itself.
    try:
        notify_new_lead(lead)
    except Exception:  # noqa: BLE001 - the lead is saved; delivery is secondary
        current_app.logger.exception("Lead notification failed")

    flash(f"Thank you. {profile.full_name.split()[0]} has your details.", "success")
    return redirect(url_for("cards.show", slug=profile.slug))
