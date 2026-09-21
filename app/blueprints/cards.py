"""Public-facing card routes.

These are the pages recipients use when they open a digital business card.
"""

from datetime import timedelta

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

from ..extensions import db
from ..forms import LeadForm
from ..models import CardView, Lead, Profile, utcnow
from ..services.jobs import notify_new_lead
from ..utils.analytics import visitor_hash
from ..utils.qr import qr_png_bytes, qr_svg
from ..utils.vcard import build_vcard, vcard_filename

bp = Blueprint("cards", __name__)


def _record(profile, action="view"):
    """Record a card interaction without breaking the card if analytics fail."""
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
    """Find a public profile by slug."""
    profile = Profile.query.filter_by(slug=slug.lower()).first()

    if profile is None:
        abort(404)

    return profile


def _card_url(profile, source=None):
    """Build the public card URL.

    source can be:
        None -> normal/direct visit
        qr   -> QR-code visit
        nfc  -> NFC-tag visit
    """
    base_url = (
        current_app.config["SITE_URL"].rstrip("/")
        + url_for("cards.show", slug=profile.slug)
    )

    if source in ("qr", "nfc"):
        return f"{base_url}?s={source}"

    return base_url


@bp.route("/c/<slug>")
def show(slug):
    """Display the public digital business card."""
    profile = _lookup(slug)

    if not profile.is_live:
        return (
            render_template("cards/inactive.html", profile=profile),
            410,
        )

    _record(profile, "view")

    card_url = _card_url(profile)
    qr_url = _card_url(profile, "qr")

    return render_template(
        "cards/card.html",
        profile=profile,
        card_url=card_url,
        qr=qr_svg(qr_url, scale=5),
        vcf_url=url_for("cards.vcf", slug=profile.slug),
        lead_form=LeadForm(),
    )


@bp.route("/c/<slug>/card.vcf")
def vcf(slug):
    """Generate and return the contact as a vCard file."""
    profile = _lookup(slug)

    if not profile.is_live:
        abort(410)

    _record(profile, "vcf")

    photo_url = None

    if profile.avatar_filename:
        photo_url = (
            current_app.config["SITE_URL"].rstrip("/")
            + url_for(
                "static",
                filename=f"img/avatars/{profile.avatar_filename}",
            )
        )

    card_url = _card_url(profile)

    payload = build_vcard(
        profile,
        card_url=card_url,
        photo_url=photo_url,
    )

    return Response(
        payload,
        mimetype="text/vcard",
        headers={
            "Content-Disposition": (
                f'inline; filename="{vcard_filename(profile)}"'
            ),
            "Cache-Control": "no-store",
        },
    )


@bp.route("/c/<slug>/qr.png")
def qr_png(slug):
    """Return the QR code image for the public card."""
    profile = _lookup(slug)

    if not profile.is_live:
        abort(410)

    qr_url = _card_url(profile, "qr")

    return Response(
        qr_png_bytes(qr_url, scale=12),
        mimetype="image/png",
        headers={
            "Content-Disposition": (
                f'inline; filename="{profile.slug}-qr.png"'
            )
        },
    )


@bp.route("/c/<slug>/connect", methods=["POST"])
def connect(slug):
    """Receive a visitor's contact details."""
    profile = _lookup(slug)

    if not profile.is_live:
        abort(410)

    form = LeadForm()

    if form.is_bot:
        flash(
            "Thank you. Your details have been sent.",
            "success",
        )
        return redirect(
            url_for("cards.show", slug=profile.slug)
        )

    if not form.validate_on_submit():
        card_url = _card_url(profile)
        qr_url = _card_url(profile, "qr")

        return (
            render_template(
                "cards/card.html",
                profile=profile,
                card_url=card_url,
                qr=qr_svg(qr_url, scale=5),
                vcf_url=url_for(
                    "cards.vcf",
                    slug=profile.slug,
                ),
                lead_form=form,
                open_form=True,
            ),
            400,
        )

    fingerprint = visitor_hash()

    since = (
        utcnow() - timedelta(hours=1)
    ).replace(tzinfo=None)

    recent = Lead.query.filter(
        Lead.profile_id == profile.id,
        Lead.visitor_hash == fingerprint,
        Lead.created_at >= since,
    ).count()

    if recent >= 3:
        flash(
            "You have already sent your details. Give it an hour.",
            "warning",
        )
        return redirect(
            url_for("cards.show", slug=profile.slug)
        )

    source = request.args.get("s", "link")

    if source not in ("link", "qr", "nfc"):
        source = "link"

    lead = Lead(
        profile=profile,
        name=form.name.data.strip(),
        phone=form.phone.data or None,
        email=(form.email.data or "").strip().lower() or None,
        organisation=(
            form.organisation.data or ""
        ).strip() or None,
        note=(
            form.note.data or ""
        ).strip() or None,
        source=source,
        visitor_hash=fingerprint,
    )

    db.session.add(lead)
    db.session.commit()

    try:
        notify_new_lead(lead)
    except Exception:
        current_app.logger.exception(
            "Lead notification failed"
        )

    first_name = profile.full_name.split()[0]

    flash(
        f"Thank you. {first_name} has your details.",
        "success",
    )

    return redirect(
        url_for("cards.show", slug=profile.slug)
    )