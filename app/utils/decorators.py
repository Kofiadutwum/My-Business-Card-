"""Route guards."""

from functools import wraps

from flask import abort, flash, redirect, url_for
from flask_login import current_user


def admin_required(view):
    """Administrator-only routes.

    404 rather than 403 is returned on purpose: a 403 confirms the URL
    exists, which tells a prober exactly where the admin area lives.
    """

    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            abort(404)
        return view(*args, **kwargs)

    return wrapped


def active_subscription_required(view):
    """Editing is allowed while lapsed; publishing is not.

    Subscribers who let a card lapse should still be able to sign in and
    see their data, otherwise renewal feels like starting over.
    """

    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_subscription_active:
            flash("Renew your plan to publish this card again.", "warning")
            return redirect(url_for("billing.checkout"))
        return view(*args, **kwargs)

    return wrapped
