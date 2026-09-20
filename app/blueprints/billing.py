"""Subscription purchase and renewal.

The flow, stated once so the code below reads clearly:

    checkout  ->  user picks plan + channel
              ->  a Payment row is written with status 'pending' BEFORE the
                  gateway is called, so nothing can succeed at the gateway
                  without a local record to attach it to
    card      ->  redirect to the hosted page, return to /callback, verify
    momo      ->  charge created, user approves on the handset, /pending
                  polls until the webhook lands
    webhook   ->  authoritative. Activates the subscription.
"""

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from ..extensions import csrf, db
from ..forms import CheckoutForm, OtpForm
from ..models import Payment, Subscription, utcnow
from ..services import paystack
from ..services.paystack import MOMO_PROVIDERS, PaymentError

bp = Blueprint("billing", __name__, url_prefix="/billing")


def _verify_payment(payment, gateway_data):
    """Validate a successful gateway response against our local payment.

    A gateway response is only acceptable when its reference, amount, and
    currency exactly match the payment we created.
    """
    if gateway_data.get("status") != "success":
        return False

    reference = str(gateway_data.get("reference") or "")
    amount = gateway_data.get("amount")
    currency = str(gateway_data.get("currency") or "").upper()

    if reference != payment.reference:
        current_app.logger.warning(
            "Payment reference mismatch: local=%s gateway=%s",
            payment.reference,
            reference,
        )
        return False

    try:
        amount = int(amount)
    except (TypeError, ValueError):
        current_app.logger.warning(
            "Invalid payment amount for reference %s: %r",
            payment.reference,
            amount,
        )
        return False

    if amount != payment.amount_minor:
        current_app.logger.warning(
            "Payment amount mismatch for %s: local=%s gateway=%s",
            payment.reference,
            payment.amount_minor,
            amount,
        )
        return False

    if currency != payment.currency.upper():
        current_app.logger.warning(
            "Payment currency mismatch for %s: local=%s gateway=%s",
            payment.reference,
            payment.currency,
            currency,
        )
        return False

    return True


def _activate(payment):
    """Turn a successful payment into subscription time.

    Idempotent on purpose. Paystack retries webhooks, and a customer who
    refreshes the callback page triggers a second verify. Neither may grant
    a second year.
    """
    if payment.status == "success" and payment.subscription_id:
        return payment.subscription

    plan = current_app.config["PLANS"][payment.plan]
    subscription = Subscription.start_or_extend(payment.user, payment.plan, plan["months"])
    db.session.add(subscription)
    db.session.flush()

    payment.status = "success"
    payment.paid_at = utcnow()
    payment.subscription_id = subscription.id

    if payment.user.profile and not payment.user.profile.is_published:
        payment.user.profile.is_published = True

    db.session.commit()
    return subscription


@bp.route("/", methods=["GET", "POST"])
@login_required
def checkout():
    plans = current_app.config["PLANS"]
    form = CheckoutForm()
    form.plan.choices = [(key, value["label"]) for key, value in plans.items()]
    if not form.plan.data:
        form.plan.data = "professional"

    if form.validate_on_submit():
        plan_key = form.plan.data
        if plan_key not in plans:
            abort(400)
        plan = plans[plan_key]

        payment = Payment(
            user=current_user,
            reference=paystack.new_reference(),
            plan=plan_key,
            amount_minor=plan["amount_minor"],
            currency=current_app.config["CURRENCY"],
            channel=form.channel.data,
            momo_provider=form.momo_provider.data if form.channel.data == "mobile_money" else None,
            momo_phone=form.momo_phone.data if form.channel.data == "mobile_money" else None,
            gateway=current_app.config["PAYMENT_PROVIDER"],
        )
        db.session.add(payment)
        db.session.commit()

        metadata = {
            "user_id": current_user.id,
            "plan": plan_key,
            "custom_fields": [
                {
                    "display_name": "Plan",
                    "variable_name": "plan",
                    "value": plan["label"],
                }
            ],
        }

        try:
            if form.channel.data == "card":
                data = paystack.initialise_card(
                    email=current_user.email,
                    amount_minor=plan["amount_minor"],
                    reference=payment.reference,
                    callback_url=current_app.config["SITE_URL"].rstrip("/")
                    + url_for("billing.callback"),
                    metadata=metadata,
                )
                return redirect(data["authorization_url"])

            data = paystack.charge_mobile_money(
                email=current_user.email,
                amount_minor=plan["amount_minor"],
                reference=payment.reference,
                phone=form.momo_phone.data,
                provider=form.momo_provider.data,
                metadata=metadata,
            )
            status = data.get("status")

            payment.raw_response = str(data)
            db.session.commit()

            if status == "send_otp":
                return redirect(url_for("billing.otp", reference=payment.reference))

            if status == "pay_offline":
                return redirect(url_for("billing.pending", reference=payment.reference))

            payment.status = "failed"
            db.session.commit()
            flash("The Mobile Money payment could not be started.", "error")
            return redirect(url_for("billing.checkout"))

        except PaymentError as exc:
            payment.status = "failed"
            db.session.commit()
            flash(str(exc), "error")
            return redirect(url_for("billing.checkout"))

    return render_template(
        "billing/checkout.html",
        form=form,
        plans=plans,
        currency=current_app.config["CURRENCY"],
        subscription=current_user.current_subscription,
        days_left=current_user.days_to_expiry,
        providers=MOMO_PROVIDERS,
        sandbox=current_app.config["PAYMENT_SANDBOX"],
    )


@bp.route("/pending/<reference>")
@login_required
def pending(reference):
    payment = Payment.query.filter_by(reference=reference).first_or_404()
    if payment.user_id != current_user.id:
        abort(404)
    return render_template(
        "billing/pending.html",
        payment=payment,
        provider_label=MOMO_PROVIDERS.get(payment.momo_provider, "your network"),
    )

@bp.route("/otp/<reference>", methods=["GET", "POST"])
@login_required
def otp(reference):
    payment = Payment.query.filter_by(reference=reference).first_or_404()
    if payment.user_id != current_user.id:
        abort(404)

    if payment.status == "success":
        return redirect(url_for("dashboard.index"))

    form = OtpForm()

    if form.validate_on_submit():
        try:
            data = paystack.submit_otp(form.otp.data, payment.reference)
            payment.raw_response = str(data)
            db.session.commit()
            flash("OTP submitted. Waiting for payment confirmation.", "success")
            return redirect(
                url_for("billing.pending", reference=payment.reference)
            )
        except PaymentError as exc:
            flash(str(exc), "error")

    return render_template(
        "billing/otp.html",
        form=form,
        payment=payment,
    )

@bp.route("/status/<reference>")
@login_required
def status(reference):
    """Polled by the pending page. Returns JSON, not HTML."""
    payment = Payment.query.filter_by(reference=reference).first_or_404()
    if payment.user_id != current_user.id:
        abort(404)

    if payment.status == "pending":
        try:
            data = paystack.verify(
                payment.reference,
                expected_amount=payment.amount_minor,
                expected_currency=payment.currency,
            )
            if _verify_payment(payment, data):
                payment.gateway_fee_minor = data.get("fees") or 0
                _activate(payment)
        except PaymentError:
            pass  # keep polling; the webhook is still the safety net

    return jsonify(
        {
            "reference": payment.reference,
            "status": payment.status,
            "redirect": url_for("dashboard.index") if payment.status == "success" else None,
        }
    )


@bp.route("/callback")
@login_required
def callback():
    """Where the hosted card page returns the customer."""
    reference = request.args.get("reference")
    if not reference:
        flash("No payment reference came back from the gateway.", "error")
        return redirect(url_for("billing.checkout"))

    payment = Payment.query.filter_by(reference=reference).first_or_404()
    if payment.user_id != current_user.id:
        abort(404)

    try:
        data = paystack.verify(
            reference,
            expected_amount=payment.amount_minor,
            expected_currency=payment.currency,
        )
    except PaymentError as exc:
        flash(
            f"{exc} Your card was not charged twice — check again shortly.",
            "warning",
        )
        return redirect(url_for("dashboard.index"))

    if _verify_payment(payment, data):
        payment.gateway_fee_minor = data.get("fees") or 0
        subscription = _activate(payment)
        flash(
            f"Payment received. Your card is live until "
            f"{subscription.expires_at.strftime('%d %B %Y')}.",
            "success",
        )
        return redirect(url_for("dashboard.share"))

    payment.status = "failed"
    db.session.commit()
    flash("That payment did not go through. Nothing was charged.", "error")
    return redirect(url_for("billing.checkout"))

@bp.route("/webhook", methods=["POST"])
@csrf.exempt
def webhook():
    """Paystack's server-to-server notification. The source of truth.

    CSRF exemption is correct here: the request does not come from a browser
    session. The HMAC signature is what authenticates it instead, and an
    unsigned request is dropped without touching the database.
    """
    raw = request.get_data()
    signature = request.headers.get("x-paystack-signature", "")

    if not current_app.config["PAYMENT_SANDBOX"]:
        if not paystack.signature_is_valid(raw, signature):
            abort(401)

    try:
        event = paystack.parse_event(raw)
    except PaymentError:
        abort(400)

    if event.get("event") not in ("charge.success", "transaction.success"):
        return "", 200

    data = event.get("data", {})
    reference = data.get("reference")
    payment = Payment.query.filter_by(reference=reference).first()
    if payment is None:
        # Not ours. Acknowledge anyway, or Paystack retries for hours.
        return "", 200

    # Re-verify rather than trusting the payload. A replayed webhook body
    # with a forged amount is cheap to attempt and free to defend against.
    try:
        confirmed = paystack.verify(
            reference,
            expected_amount=payment.amount_minor,
            expected_currency=payment.currency,
        )
    except PaymentError:
        return "", 200

    if _verify_payment(payment, confirmed):
        payment.gateway_fee_minor = confirmed.get("fees") or data.get("fees") or 0
        payment.raw_response = str(data)[:5000]
        _activate(payment)

    return "", 200


@bp.route("/receipts")
@login_required
def receipts():
    return render_template(
        "billing/receipts.html",
        payments=current_user.payments,
        currency=current_app.config["CURRENCY"],
    )
