"""Public marketing pages."""

from types import SimpleNamespace

from flask import Blueprint, current_app, render_template

from ..models import GalleryImage


bp = Blueprint("main", __name__)


def _demo_profile():
    """AD Smart Business Cards sample card shown on the homepage."""

    support_phone = current_app.config.get(
        "SUPPORT_PHONE",
        "",
    )

    return SimpleNamespace(
        full_name="AD Smart Business Cards",
        job_title="Digital Business Cards",
        organisation="Ad graphics",
        phone=support_phone,
        whatsapp=support_phone,
        email="adgraphics1@gmail.com",
        website=current_app.config.get(
            "SITE_URL",
            "",
        ),
        location="Ghana",
        bio="Smart digital business cards with QR and NFC sharing.",
        avatar_filename=None,
        accent="purple",
        slug="ad-smart-business-cards",
        initials="AD",
        social_links=[
            SimpleNamespace(
                platform="instagram",
                url="https://instagram.com/Adgraphics__",
                label="Instagram",
            ),
        ],
    )


@bp.route("/")
def index():
    """
    Public homepage.

    Published gallery images are loaded automatically from the
    database and passed to the homepage template.
    """

    gallery_images = (
        GalleryImage.query
        .filter_by(is_published=True)
        .order_by(
            GalleryImage.display_order.asc(),
            GalleryImage.id.asc(),
        )
        .all()
    )

    return render_template(
        "main/index.html",
        plans=current_app.config["PLANS"],
        currency=current_app.config["CURRENCY"],
        usd_ghs_rate=current_app.config["USD_GHS_RATE"],
        demo_profile=_demo_profile(),
        gallery_images=gallery_images,
    )


@bp.route("/pricing")
def pricing():
    """Public pricing page."""

    return render_template(
        "main/pricing.html",
        plans=current_app.config["PLANS"],
        currency=current_app.config["CURRENCY"],
        usd_ghs_rate=current_app.config["USD_GHS_RATE"],
    )