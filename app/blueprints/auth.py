"""Registration, sign-in, account recovery, and Google authentication."""

import json
import os
import secrets
from datetime import timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import (
    current_user,
    login_required,
    login_user,
    logout_user,
)

from ..extensions import db
from ..forms import (
    LoginForm,
    PasswordChangeForm,
    PasswordRecoveryRequestForm,
    PasswordResetForm,
    RegisterForm,
)
from ..models import (
    PasswordResetOTP,
    Profile,
    User,
    utcnow,
)
from ..services.sms import SmsError, normalise, send


bp = Blueprint("auth", __name__, url_prefix="/auth")


GOOGLE_AUTHORIZATION_URL = (
    "https://accounts.google.com/o/oauth2/v2/auth"
)

GOOGLE_TOKEN_URL = (
    "https://oauth2.googleapis.com/token"
)

GOOGLE_USERINFO_URL = (
    "https://openidconnect.googleapis.com/v1/userinfo"
)

PASSWORD_RESET_OTP_LENGTH = 6
PASSWORD_RESET_OTP_COOLDOWN_SECONDS = 60
PASSWORD_RESET_OTP_EXPIRY_MINUTES = 10


def _safe_next(target):
    """Only follow a relative URL belonging to this application."""
    if not target:
        return None

    if target.startswith("/") and not target.startswith("//"):
        return target

    return None


def _suggest_slug(full_name):
    base = "".join(
        ch.lower() if ch.isalnum() else "-"
        for ch in full_name
    ).strip("-")

    base = "-".join(
        part for part in base.split("-") if part
    )[:28] or "card"

    candidate = base
    counter = 1

    while Profile.query.filter_by(slug=candidate).first():
        counter += 1
        candidate = f"{base}-{counter}"

    return candidate


def _login_destination(user):
    """Return the normal post-login destination."""
    destination = _safe_next(request.args.get("next"))

    if destination:
        return destination

    if user.is_admin:
        return url_for("admin.dashboard")

    return url_for("dashboard.index")


def _finish_login(user, remember=False):
    """Log a user in and update the last-login timestamp."""
    login_user(user, remember=remember)

    user.last_login_at = utcnow()

    db.session.commit()


def _google_configured():
    """Return True when Google OAuth credentials are configured."""
    return bool(
        os.environ.get("GOOGLE_CLIENT_ID")
        and os.environ.get("GOOGLE_CLIENT_SECRET")
    )


def _google_redirect_uri():
    """Return the callback URL registered with Google."""
    return url_for(
        "auth.google_callback",
        _external=True,
    )


def _google_error(message):
    """Show a Google authentication error and return to login."""
    flash(message, "error")
    return redirect(url_for("auth.login"))


def _google_exchange_code(code):
    """Exchange Google's authorization code for an access token."""
    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")

    payload = urlencode(
        {
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": _google_redirect_uri(),
            "grant_type": "authorization_code",
        }
    ).encode("utf-8")

    request_object = Request(
        GOOGLE_TOKEN_URL,
        data=payload,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )

    try:
        with urlopen(request_object, timeout=15) as response:
            data = json.loads(
                response.read().decode("utf-8")
            )
    except HTTPError:
        return None, "Google token exchange failed."
    except URLError:
        return None, "Google could not be reached. Try again."
    except (ValueError, OSError):
        return None, "Google returned an invalid response."

    access_token = data.get("access_token")

    if not access_token:
        return None, "Google did not return an access token."

    return access_token, None


def _google_userinfo(access_token):
    """Retrieve the authenticated user's Google profile."""
    request_object = Request(
        GOOGLE_USERINFO_URL,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        },
        method="GET",
    )

    try:
        with urlopen(request_object, timeout=15) as response:
            data = json.loads(
                response.read().decode("utf-8")
            )
    except HTTPError:
        return None, "Google account information could not be retrieved."
    except URLError:
        return None, "Google could not be reached. Try again."
    except (ValueError, OSError):
        return None, "Google returned an invalid response."

    return data, None


def _get_google_user(code):
    """Exchange the OAuth code and retrieve the Google user."""
    access_token, error = _google_exchange_code(code)

    if error:
        return None, error

    google_user, error = _google_userinfo(access_token)

    if error:
        return None, error

    google_sub = str(
        google_user.get("sub") or ""
    ).strip()

    email = str(
        google_user.get("email") or ""
    ).strip().lower()

    email_verified = google_user.get("email_verified")

    if not google_sub:
        return None, "Google did not provide a valid account ID."

    if not email:
        return None, "Google did not provide an email address."

    if email_verified is not True:
        return None, "Your Google email address must be verified."

    return {
        "sub": google_sub,
        "email": email,
        "given_name": str(
            google_user.get("given_name") or ""
        ).strip(),
        "family_name": str(
            google_user.get("family_name") or ""
        ).strip(),
        "name": str(
            google_user.get("name") or ""
        ).strip(),
    }, None


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------

@bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    form = RegisterForm()

    if form.validate_on_submit():
        email = form.email.data.lower().strip()

        try:
            phone = normalise(form.phone.data)
        except SmsError:
            flash(
                "Enter a valid Ghanaian phone number.",
                "error",
            )
            return render_template(
                "auth/register.html",
                form=form,
            ), 400

        if User.query.filter_by(email=email).first():
            flash(
                "That email already has an account. Sign in instead.",
                "warning",
            )
            return redirect(url_for("auth.login"))

        if User.query.filter_by(phone=phone).first():
            flash(
                "That phone number is already linked to an account. "
                "Sign in or recover your account instead.",
                "warning",
            )
            return redirect(url_for("auth.login"))

        user = User(
            email=email,
            phone=phone,
        )

        user.set_password(form.password.data)

        db.session.add(user)
        db.session.flush()

        profile = Profile(
            user=user,
            full_name=form.full_name.data.strip(),
            email=email,
            slug=_suggest_slug(
                form.full_name.data.strip()
            ),
            is_published=False,
        )

        db.session.add(profile)

        db.session.commit()

        login_user(user)

        flash(
            "Account created. Build your card, then choose a plan.",
            "success",
        )

        return redirect(
            url_for("dashboard.edit_profile")
        )

    return render_template(
        "auth/register.html",
        form=form,
    )


# --------------------------------------------------------------------------
# Normal login
# --------------------------------------------------------------------------

@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    form = LoginForm()

    if form.validate_on_submit():
        user = User.query.filter_by(
            email=form.email.data.lower().strip()
        ).first()

        if user is None or not user.check_password(
            form.password.data
        ):
            flash(
                "That email and password do not match.",
                "error",
            )

            return render_template(
                "auth/login.html",
                form=form,
            ), 401

        if user.is_suspended:
            flash(
                "This account is suspended. Contact support.",
                "error",
            )

            return render_template(
                "auth/login.html",
                form=form,
            ), 403

        _finish_login(
            user,
            remember=form.remember.data,
        )

        return redirect(
            _login_destination(user)
        )

    return render_template(
        "auth/login.html",
        form=form,
    )


# --------------------------------------------------------------------------
# Password recovery by phone
# --------------------------------------------------------------------------

def _generate_recovery_code():
    """Generate a six-digit numeric recovery code."""
    return f"{secrets.randbelow(1_000_000):0{PASSWORD_RESET_OTP_LENGTH}d}"


def _recovery_request_message(code):
    """Build the password-recovery SMS."""
    return (
        f"AD Smart Business Cards: your password recovery code is "
        f"{code}. It expires in {PASSWORD_RESET_OTP_EXPIRY_MINUTES} "
        "minutes. If you did not request this, ignore this message."
    )


def _latest_recovery_otp(user):
    """Return the most recent recovery OTP for a user."""
    return (
        PasswordResetOTP.query
        .filter_by(user_id=user.id)
        .order_by(
            PasswordResetOTP.created_at.desc()
        )
        .first()
    )


def _recovery_cooldown_active(otp):
    """Prevent repeated OTP requests within the cooldown period."""
    if otp is None:
        return False

    created_at = otp.created_at

    if created_at is None:
        return False

    elapsed = utcnow() - created_at

    return elapsed < timedelta(
        seconds=PASSWORD_RESET_OTP_COOLDOWN_SECONDS
    )


@bp.route(
    "/forgot-password",
    methods=["GET", "POST"],
)
def forgot_password():
    """Request a password-reset OTP using a phone number."""
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    form = PasswordRecoveryRequestForm()

    if form.validate_on_submit():
        try:
            phone = normalise(form.phone.data)
        except SmsError:
            phone = None

        user = None

        if phone:
            user = User.query.filter_by(
                phone=phone
            ).first()

        # Always use the same outward response so callers
        # cannot discover whether a phone number is registered.
        generic_message = (
            "If an account is associated with that phone number, "
            "a verification code has been sent."
        )

        if user is None:
            flash(generic_message, "info")
            session["password_recovery_phone"] = phone

            return redirect(
                url_for("auth.verify_recovery")
            )

        if user.is_suspended:
            flash(generic_message, "info")
            session["password_recovery_phone"] = phone

            return redirect(
                url_for("auth.verify_recovery")
            )

        latest = _latest_recovery_otp(user)

        if _recovery_cooldown_active(latest):
            flash(
                "A verification code was recently sent. "
                "Please wait before requesting another.",
                "warning",
            )

            session["password_recovery_phone"] = phone

            return redirect(
                url_for("auth.verify_recovery")
            )

        code = _generate_recovery_code()

        otp = PasswordResetOTP(
            user=user,
            phone=phone,
        )

        otp.set_otp(code)

        db.session.add(otp)
        db.session.flush()

        message = _recovery_request_message(code)

        try:
            send(phone, message)
        except SmsError:
            db.session.rollback()

            flash(
                "We could not send the verification code. "
                "Please try again.",
                "error",
            )

            return render_template(
                "auth/forgot_password.html",
                form=form,
            )

        db.session.commit()

        session["password_recovery_phone"] = phone

        flash(
            generic_message,
            "success",
        )

        return redirect(
            url_for("auth.verify_recovery")
        )

    return render_template(
        "auth/forgot_password.html",
        form=form,
    )


@bp.route(
    "/forgot-password/verify",
    methods=["GET", "POST"],
)
def verify_recovery():
    """Verify the phone recovery OTP and set a new password."""
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    phone = session.get("password_recovery_phone")

    if not phone:
        flash(
            "Start account recovery by entering your phone number.",
            "warning",
        )

        return redirect(
            url_for("auth.forgot_password")
        )

    form = PasswordResetForm()

    if form.validate_on_submit():
        user = User.query.filter_by(
            phone=phone
        ).first()

        if user is None or user.is_suspended:
            flash(
                "The verification code is invalid or has expired.",
                "error",
            )

            return render_template(
                "auth/verify_recovery.html",
                form=form,
            ), 400

        otp = (
            PasswordResetOTP.query
            .filter_by(
                user_id=user.id,
                phone=phone,
            )
            .order_by(
                PasswordResetOTP.created_at.desc()
            )
            .first()
        )

        if otp is None or not otp.check_otp(
            form.otp.data.strip()
        ):
            db.session.commit()

            flash(
                "The verification code is invalid or has expired.",
                "error",
            )

            return render_template(
                "auth/verify_recovery.html",
                form=form,
            ), 400

        otp.mark_used()

        user.set_password(
            form.password.data
        )

        db.session.commit()

        session.pop(
            "password_recovery_phone",
            None,
        )

        login_user(user)

        flash(
            "Your password has been reset successfully.",
            "success",
        )

        return redirect(
            url_for("dashboard.index")
        )

    return render_template(
        "auth/verify_recovery.html",
        form=form,
    )


# --------------------------------------------------------------------------
# Google OAuth
# --------------------------------------------------------------------------

@bp.route("/google")
def google_login():
    """Start Google OAuth sign-in."""
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    if not _google_configured():
        flash(
            "Google sign-in is not configured yet.",
            "warning",
        )

        return redirect(url_for("auth.login"))

    state = secrets.token_urlsafe(32)

    session["google_oauth_state"] = state

    next_destination = _safe_next(
        request.args.get("next")
    )

    if next_destination:
        session["google_oauth_next"] = next_destination
    else:
        session.pop("google_oauth_next", None)

    params = {
        "client_id": os.environ["GOOGLE_CLIENT_ID"],
        "redirect_uri": _google_redirect_uri(),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",
        "access_type": "offline",
    }

    authorization_url = (
        GOOGLE_AUTHORIZATION_URL
        + "?"
        + urlencode(params)
    )

    return redirect(authorization_url)


@bp.route("/google/callback")
def google_callback():
    """Handle Google's OAuth callback."""
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    if not _google_configured():
        return _google_error(
            "Google sign-in is not configured yet."
        )

    error = request.args.get("error")

    if error:
        session.pop("google_oauth_state", None)
        session.pop("google_oauth_next", None)

        if error == "access_denied":
            return _google_error(
                "Google sign-in was cancelled."
            )

        return _google_error(
            "Google sign-in was not completed."
        )

    received_state = request.args.get("state")

    expected_state = session.pop(
        "google_oauth_state",
        None,
    )

    if (
        not received_state
        or not expected_state
        or not secrets.compare_digest(
            received_state,
            expected_state,
        )
    ):
        session.pop("google_oauth_next", None)

        return _google_error(
            "Your Google sign-in session expired. Try again."
        )

    code = request.args.get("code")

    if not code:
        session.pop("google_oauth_next", None)

        return _google_error(
            "Google did not return a sign-in code."
        )

    google_user, error = _get_google_user(code)

    if error:
        session.pop("google_oauth_next", None)

        return _google_error(error)

    google_sub = google_user["sub"]
    email = google_user["email"]

    user = User.query.filter_by(
        google_sub=google_sub
    ).first()

    if user is None:
        user = User.query.filter_by(
            email=email
        ).first()

        if user is not None:
            user.google_sub = google_sub

    if user is None:
        full_name = (
            google_user["name"]
            or " ".join(
                part
                for part in (
                    google_user["given_name"],
                    google_user["family_name"],
                )
                if part
            )
            or "Card User"
        )

        user = User(
            email=email,
            google_sub=google_sub,
        )

        user.set_password(
            secrets.token_urlsafe(32)
        )

        db.session.add(user)
        db.session.flush()

        profile = Profile(
            user=user,
            full_name=full_name,
            email=email,
            slug=_suggest_slug(full_name),
            is_published=False,
        )

        db.session.add(profile)

    else:
        if user.is_suspended:
            session.pop("google_oauth_next", None)

            return _google_error(
                "This account is suspended. Contact support."
            )

        profile = Profile.query.filter_by(
            user_id=user.id
        ).first()

        if profile is not None and not profile.email:
            profile.email = email

    if user.is_suspended:
        session.pop("google_oauth_next", None)

        return _google_error(
            "This account is suspended. Contact support."
        )

    db.session.commit()

    _finish_login(user)

    next_destination = session.pop(
        "google_oauth_next",
        None,
    )

    flash(
        "Signed in with Google.",
        "success",
    )

    if next_destination:
        return redirect(next_destination)

    return redirect(
        _login_destination(user)
    )


# --------------------------------------------------------------------------
# Logout
# --------------------------------------------------------------------------

@bp.route("/logout")
@login_required
def logout():
    logout_user()

    flash(
        "Signed out.",
        "info",
    )

    return redirect(
        url_for("main.index")
    )


# --------------------------------------------------------------------------
# Password change for logged-in users
# --------------------------------------------------------------------------

@bp.route("/password", methods=["GET", "POST"])
@login_required
def change_password():
    form = PasswordChangeForm()

    if form.validate_on_submit():
        if not current_user.check_password(
            form.current_password.data
        ):
            flash(
                "That current password is wrong.",
                "error",
            )
        else:
            current_user.set_password(
                form.password.data
            )

            db.session.commit()

            flash(
                "Password updated.",
                "success",
            )

            return redirect(
                url_for("dashboard.index")
            )

    return render_template(
        "auth/password.html",
        form=form,
    )