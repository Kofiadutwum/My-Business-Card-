"""Registration, sign-in, sign-out."""

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from ..extensions import db
from ..forms import LoginForm, PasswordChangeForm, RegisterForm
from ..models import Profile, User, utcnow

bp = Blueprint("auth", __name__, url_prefix="/auth")


def _safe_next(target):
    """Only follow a ?next= that points back at this site.

    An unchecked next parameter is an open redirect, and open redirects are
    the oldest phishing assist in the book.
    """
    if not target:
        return None
    if target.startswith("/") and not target.startswith("//"):
        return target
    return None


def _suggest_slug(full_name):
    base = "".join(ch.lower() if ch.isalnum() else "-" for ch in full_name).strip("-")
    base = "-".join(part for part in base.split("-") if part)[:28] or "card"
    candidate, counter = base, 1
    while Profile.query.filter_by(slug=candidate).first():
        counter += 1
        candidate = f"{base}-{counter}"
    return candidate


@bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    form = RegisterForm()
    if form.validate_on_submit():
        email = form.email.data.lower().strip()
        if User.query.filter_by(email=email).first():
            flash("That email already has an account. Sign in instead.", "warning")
            return redirect(url_for("auth.login"))

        user = User(email=email)
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.flush()

        profile = Profile(
            user=user,
            full_name=form.full_name.data.strip(),
            email=email,
            slug=_suggest_slug(form.full_name.data.strip()),
            is_published=False,
        )
        db.session.add(profile)
        db.session.commit()

        login_user(user)
        flash("Account created. Build your card, then choose a plan.", "success")
        return redirect(url_for("dashboard.edit_profile"))

    return render_template("auth/register.html", form=form)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data.lower().strip()).first()
        if user is None or not user.check_password(form.password.data):
            # One message for both cases. Saying "no such account" tells an
            # attacker which addresses are registered.
            flash("That email and password do not match.", "error")
            return render_template("auth/login.html", form=form), 401
        if user.is_suspended:
            flash("This account is suspended. Contact support.", "error")
            return render_template("auth/login.html", form=form), 403

        login_user(user, remember=form.remember.data)
        user.last_login_at = utcnow()
        db.session.commit()

        destination = _safe_next(request.args.get("next"))
        if destination:
            return redirect(destination)
        if user.is_admin:
            return redirect(url_for("admin.dashboard"))
        return redirect(url_for("dashboard.index"))

    return render_template("auth/login.html", form=form)


@bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Signed out.", "info")
    return redirect(url_for("main.index"))


@bp.route("/password", methods=["GET", "POST"])
@login_required
def change_password():
    form = PasswordChangeForm()
    if form.validate_on_submit():
        if not current_user.check_password(form.current_password.data):
            flash("That current password is wrong.", "error")
        else:
            current_user.set_password(form.password.data)
            db.session.commit()
            flash("Password updated.", "success")
            return redirect(url_for("dashboard.index"))
    return render_template("auth/password.html", form=form)
