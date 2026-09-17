"""Public marketing pages."""

from types import SimpleNamespace

from flask import Blueprint, current_app, render_template

bp = Blueprint("main", __name__)


def _demo_profile():
    """A stand-in card for the hero.

    Built in memory rather than seeded into the database so that the landing
    page renders on a completely empty install, which is the first thing you
    will see after cloning this.
    """
    return SimpleNamespace(
        full_name="Ama Serwaa Boateng",
        job_title="Procurement Lead",
        organisation="Adinkra Logistics, Accra",
        phone="+233244000000",
        whatsapp="+233244000000",
        email="ama@adinkralogistics.com",
        website="https://adinkralogistics.com",
        location="Accra",
        bio="Freight and customs clearing across the Tema corridor.",
        avatar_filename=None,
        accent="purple",
        slug="ama-boateng",
        initials="AB",
        social_links=[
            SimpleNamespace(platform="linkedin", url="#", label="LinkedIn"),
            SimpleNamespace(platform="instagram", url="#", label="Instagram"),
            SimpleNamespace(platform="x", url="#", label="X"),
        ],
    )


@bp.route("/")
def index():
    return render_template(
        "main/index.html",
        plans=current_app.config["PLANS"],
        currency=current_app.config["CURRENCY"],
        demo_profile=_demo_profile(),
    )


@bp.route("/pricing")
def pricing():
    return render_template(
        "main/pricing.html",
        plans=current_app.config["PLANS"],
        currency=current_app.config["CURRENCY"],
    )
