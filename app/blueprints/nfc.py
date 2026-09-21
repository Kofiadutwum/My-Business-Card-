"""Physical NFC card ordering and payments."""

import os
import secrets

from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required
from werkzeug.utils import secure_filename

from ..extensions import db
from ..forms import NFCOrderForm, OtpForm
from ..models import NFCOrder, utcnow
from ..services import email as email_service
from ..services import paystack


bp = Blueprint("nfc", __name__, url_prefix="/nfc")


def _save_nfc_logo(file_storage):
    """Save an NFC order logo/photo with a random filename."""

    if not file_storage or not file_storage.filename:
        return None

    extension = file_storage.filename.rsplit(".", 1)[-1].lower()

    if extension not in current_app.config["ALLOWED_IMAGE_EXTENSIONS"]:
        return None

    filename = secure_filename(
        f"nfc-{secrets.token_hex(12)}.{extension}"
    )

    folder = os.path.join(
        current_app.config["UPLOAD_FOLDER"],
        "nfc",
    )

    os.makedirs(folder, exist_ok=True)

    file_storage.save(
        os.path.join(folder, filename)
    )

    return filename


def _new_order_number():
    """Generate a human-friendly NFC order number."""

    while True:
        number = f"NFC-{secrets.token_hex(4).upper()}"

        if not NFCOrder.query.filter_by(
            order_number=number
        ).first():
            return number


def _new_payment_reference():
    """Generate a unique Paystack reference for an NFC order."""

    while True:
        reference = f"NFC-{secrets.token_hex(10).upper()}"

        if not NFCOrder.query.filter_by(
            payment_reference=reference
        ).first():
            return reference


def _calculate_order_amount(form):
    """
    Calculate the complete NFC order amount server-side.

    The browser is never trusted for pricing.
    """

    order_type = form.order_type.data
    quantity = int(form.quantity.data)

    replacement = order_type == "replacement"

    nfc_price_minor, discount_minor = (
        NFCOrder.calculate_nfc_price(
            quantity,
            replacement=replacement,
        )
    )

    digital_plan = None
    digital_plan_price_minor = 0

    if order_type == "digital_and_nfc":
        plans = current_app.config["PLANS"]

        if form.plan.data not in plans:
            raise ValueError(
                "Please select a valid digital card plan."
            )

        digital_plan = form.plan.data
        digital_plan_price_minor = plans[
            digital_plan
        ]["amount_minor"]

    total_amount_minor = (
        nfc_price_minor
        + digital_plan_price_minor
    )

    return {
        "quantity": quantity,
        "nfc_price_minor": nfc_price_minor,
        "discount_minor": discount_minor,
        "digital_plan": digital_plan,
        "digital_plan_price_minor": digital_plan_price_minor,
        "total_amount_minor": total_amount_minor,
    }


def _create_order(form, profile):
    """Create an NFC order using server-side pricing."""

    pricing = _calculate_order_amount(form)

    logo_filename = None

    if form.logo.data:
        logo_filename = _save_nfc_logo(
            form.logo.data
        )

    nfc_order = NFCOrder(
        user=current_user,
        profile=profile,
        order_number=_new_order_number(),
        order_type=form.order_type.data,
        quantity=pricing["quantity"],
        nfc_price_minor=pricing["nfc_price_minor"],
        discount_minor=pricing["discount_minor"],
        digital_plan=pricing["digital_plan"],
        digital_plan_price_minor=(
            pricing["digital_plan_price_minor"]
        ),
        total_amount_minor=(
            pricing["total_amount_minor"]
        ),
        currency=current_app.config["CURRENCY"],
        full_name=form.full_name.data.strip(),
        position=(form.position.data or "").strip(),
        email=form.email.data.strip().lower(),
        phone=form.phone.data.strip(),
        delivery_location=(
            form.delivery_location.data.strip()
        ),
        logo_filename=logo_filename,
        design_instructions=(
            form.design_instructions.data or ""
        ).strip(),
        replacement_reason=(
            form.replacement_reason.data or None
        ),
        payment_reference=_new_payment_reference(),
        payment_status="pending",
        status="pending",
        created_at=utcnow(),
        updated_at=utcnow(),
    )

    db.session.add(nfc_order)
    db.session.commit()

    return nfc_order


def _money(amount_minor, currency=None):
    """Format a minor-unit amount for an email."""

    if amount_minor is None:
        amount_minor = 0

    currency = currency or current_app.config["CURRENCY"]

    return f"{currency} {amount_minor / 100:,.2f}"


def _display_order_type(order_type):
    """Return a readable NFC order type."""

    labels = {
        "digital_and_nfc": "Digital Card + NFC",
        "nfc_only": "NFC Cards Only",
        "replacement": "Replacement",
    }

    return labels.get(
        order_type,
        order_type or "N/A",
    )


def _display_plan(plan):
    """Return a readable digital plan name."""

    if not plan:
        return "None"

    plans = current_app.config.get("PLANS", {})

    plan_data = plans.get(plan)

    if plan_data:
        return plan_data.get(
            "label",
            plan.title(),
        )

    return plan.replace("_", " ").title()


def _display_replacement_reason(reason):
    """Return a readable replacement reason."""

    if not reason:
        return "N/A"

    labels = {
        "lost": "Lost",
        "damaged": "Damaged",
        "change_of_design": "Change of design",
        "nfc_not_working": "NFC not working",
        "other": "Other",
    }

    return labels.get(
        reason,
        reason.replace("_", " ").title(),
    )


def _admin_notification_body(nfc_order):
    """Build the admin email body for a paid NFC order."""

    site_url = current_app.config.get(
        "SITE_URL",
        "",
    ).rstrip("/")

    digital_card_url = None

    if nfc_order.profile and nfc_order.profile.slug:
        if site_url:
            digital_card_url = (
                f"{site_url}/c/{nfc_order.profile.slug}"
            )
        else:
            try:
                digital_card_url = url_for(
                    "cards.show",
                    slug=nfc_order.profile.slug,
                    _external=True,
                )
            except Exception:
                digital_card_url = None

    try:
        admin_order_url = url_for(
            "admin.nfc_order_detail",
            order_id=nfc_order.id,
            _external=True,
        )
    except Exception:
        admin_order_url = None

    paid_at = (
        nfc_order.paid_at.strftime(
            "%d %B %Y, %I:%M %p"
        )
        if nfc_order.paid_at
        else "N/A"
    )

    logo_filename = (
        nfc_order.logo_filename
        if nfc_order.logo_filename
        else "No logo/picture uploaded"
    )

    lines = [
        "AD SMART BUSINESS CARDS",
        "NEW NFC ORDER — PAYMENT SUCCESSFUL",
        "",
        "ORDER INFORMATION",
        "-----------------",
        f"Order number: {nfc_order.order_number}",
        f"Order type: {_display_order_type(nfc_order.order_type)}",
        f"NFC quantity: {nfc_order.quantity}",
        f"Payment status: {nfc_order.payment_status}",
        f"Order status: {nfc_order.status}",
        f"Payment reference: {nfc_order.payment_reference}",
        f"Paid at: {paid_at}",
        "",
        "CUSTOMER INFORMATION",
        "--------------------",
        f"Full name: {nfc_order.full_name}",
        f"Position / Job title: {nfc_order.position or 'N/A'}",
        f"Email: {nfc_order.email}",
        f"Phone: {nfc_order.phone}",
        f"Delivery location: {nfc_order.delivery_location}",
        "",
        "PRODUCT & PRICING",
        "-----------------",
        f"Digital plan: {_display_plan(nfc_order.digital_plan)}",
        f"Digital plan price: {_money(nfc_order.digital_plan_price_minor, nfc_order.currency)}",
        f"NFC card price: {_money(nfc_order.nfc_price_minor, nfc_order.currency)}",
        f"NFC discount: {_money(nfc_order.discount_minor, nfc_order.currency)}",
        f"TOTAL PAID: {_money(nfc_order.total_amount_minor, nfc_order.currency)}",
        "",
        "DESIGN & PRODUCTION",
        "-------------------",
        f"Logo/picture file: {logo_filename}",
        f"Replacement reason: {_display_replacement_reason(nfc_order.replacement_reason)}",
        "",
        "Design instructions:",
        nfc_order.design_instructions
        or "No special design instructions provided.",
        "",
    ]

    if digital_card_url:
        lines.extend(
            [
                "DIGITAL CARD",
                "------------",
                f"Digital card URL: {digital_card_url}",
                "",
            ]
        )

    if admin_order_url:
        lines.extend(
            [
                "ADMIN ORDER",
                "-----------",
                f"View order: {admin_order_url}",
                "",
            ]
        )

    lines.extend(
        [
            "This order was paid successfully.",
            "Please review the order and begin the design/production process.",
            "",
            "AD Smart Business Cards",
            current_app.config.get(
                "SUPPORT_EMAIL",
                "",
            ),
            current_app.config.get(
                "SUPPORT_PHONE",
                "",
            ),
        ]
    )

    return "\n".join(lines)


def _notify_admin(nfc_order):
    """
    Send the paid NFC order notification to the administrator.

    The notification is deliberately independent from payment success.
    If email fails, the order remains paid and the timestamp stays empty,
    allowing the notification to be retried later.
    """

    if nfc_order.payment_status != "success":
        return False

    if nfc_order.admin_notified_at:
        return True

    recipient = current_app.config.get(
        "NFC_ADMIN_EMAIL",
        "adgraphic881@gmail.com",
    )

    subject = (
        f"AD Smart NFC Order Paid — "
        f"{nfc_order.order_number}"
    )

    body = _admin_notification_body(
        nfc_order
    )

    try:
        email_service.send_email(
            recipient=recipient,
            subject=subject,
            body=body,
        )

        nfc_order.admin_notified_at = utcnow()
        nfc_order.updated_at = utcnow()

        db.session.commit()

        current_app.logger.info(
            "NFC admin notification sent successfully: %s",
            nfc_order.order_number,
        )

        return True

    except Exception:
        db.session.rollback()

        current_app.logger.exception(
            "NFC payment succeeded but admin email notification failed: %s",
            nfc_order.order_number,
        )

        return False


def _mark_paid(nfc_order, gateway_data=None):
    """
    Mark an NFC order as paid.

    This operation is intentionally idempotent so that
    Paystack callbacks/webhooks cannot process the same
    payment twice.

    Admin notification is attempted after payment is confirmed.
    Failure to send the email never makes the payment unsuccessful.
    """

    if nfc_order.payment_status != "success":
        nfc_order.payment_status = "success"
        nfc_order.status = "design_pending"
        nfc_order.paid_at = utcnow()
        nfc_order.updated_at = utcnow()

        db.session.commit()

    # If the payment is already successful but the notification
    # was never sent, this also acts as a retry mechanism.
    _notify_admin(nfc_order)

    return True


def _verify_nfc_payment(nfc_order):
    """Verify an NFC payment directly with Paystack."""

    if not nfc_order.payment_reference:
        return False

    try:
        gateway_data = paystack.verify(
            nfc_order.payment_reference,
            expected_amount=(
                nfc_order.total_amount_minor
            ),
            expected_currency=nfc_order.currency,
        )
    except Exception:
        return False

    if not gateway_data:
        return False

    status = gateway_data.get("status")

    if status != "success":
        return False

    return _mark_paid(
        nfc_order,
        gateway_data,
    )


def _send_to_paystack(nfc_order, form):
    """
    Start the appropriate Paystack payment.

    Card payments are sent to Paystack's hosted checkout.

    Mobile Money can return several different states:
        send_otp    -> send the customer to our OTP page.
        pay_offline -> customer approves the request on their phone.
        pending     -> continue waiting.
        processing  -> continue waiting.
        ongoing     -> continue waiting.
        success     -> verify immediately.
    """

    metadata = {
        "payment_type": "nfc_order",
        "nfc_order_id": nfc_order.id,
        "nfc_order_number": nfc_order.order_number,
        "order_type": nfc_order.order_type,
        "quantity": nfc_order.quantity,
        "digital_plan": nfc_order.digital_plan or "",
    }

    amount_minor = nfc_order.total_amount_minor
    reference = nfc_order.payment_reference

    callback_url = url_for(
        "nfc.callback",
        _external=True,
    )

    channel = form.channel.data

    # --------------------------------------------------------------
    # CARD PAYMENT
    # --------------------------------------------------------------
    if channel == "card":
        response = paystack.initialise_card(
            nfc_order.email,
            amount_minor,
            reference,
            callback_url,
            metadata=metadata,
        )

        authorization_url = (
            response.get("authorization_url")
            if response
            else None
        )

        if not authorization_url:
            raise paystack.PaymentError(
                "Paystack did not return a payment URL."
            )

        return redirect(authorization_url)

    # --------------------------------------------------------------
    # MOBILE MONEY PAYMENT
    # --------------------------------------------------------------
    if channel == "mobile_money":
        provider = form.momo_provider.data

        if not provider:
            raise paystack.PaymentError(
                "Please select your mobile money network."
            )

        response = paystack.charge_mobile_money(
            nfc_order.email,
            amount_minor,
            reference,
            form.momo_phone.data,
            provider,
            metadata=metadata,
        )

        gateway_status = (
            response.get("status")
            if response
            else None
        )

        # Paystack requires an OTP from the customer.
        if gateway_status == "send_otp":
            return redirect(
                url_for(
                    "nfc.payment_otp",
                    order_number=nfc_order.order_number,
                )
            )

        # Customer must approve the payment on their phone.
        if gateway_status in {
            "pay_offline",
            "pending",
            "processing",
            "ongoing",
        }:
            return redirect(
                url_for(
                    "nfc.payment_status",
                    order_number=nfc_order.order_number,
                )
            )

        # Some payments can complete immediately.
        if gateway_status == "success":
            _verify_nfc_payment(nfc_order)

            return redirect(
                url_for(
                    "nfc.payment_status",
                    order_number=nfc_order.order_number,
                )
            )

        gateway_message = (
            response.get("display_text")
            or response.get("message")
            or "Paystack could not start the mobile money payment."
        )

        raise paystack.PaymentError(
            gateway_message
        )

    raise paystack.PaymentError(
        "Unsupported payment method."
    )


@bp.route("/order", methods=["GET", "POST"])
@login_required
def order():
    """Create and pay for a physical NFC card order."""

    profile = current_user.profile

    if profile is None:
        flash(
            "Please create your digital business card before ordering NFC cards.",
            "warning",
        )

        return redirect(
            url_for(
                "dashboard.edit_profile"
            )
        )

    form = NFCOrderForm()

    if form.validate_on_submit():
        try:
            nfc_order = _create_order(
                form,
                profile,
            )

            return _send_to_paystack(
                nfc_order,
                form,
            )

        except ValueError as exc:
            db.session.rollback()

            flash(
                str(exc),
                "error",
            )

        except paystack.PaymentError as exc:
            db.session.rollback()

            flash(
                f"Payment could not be started: {exc}",
                "error",
            )

        except Exception:
            db.session.rollback()

            current_app.logger.exception(
                "NFC order/payment error"
            )

            flash(
                "We could not start your NFC payment. Please try again.",
                "error",
            )

    return render_template(
        "nfc/order.html",
        form=form,
        profile=profile,
        plans=current_app.config["PLANS"],
        currency=current_app.config["CURRENCY"],
        nfc_prices=NFCOrder.NFC_PRICES_MINOR,
        replacement_discount=(
            NFCOrder.REPLACEMENT_DISCOUNT
        ),
    )


@bp.route(
    "/payment/<order_number>/otp",
    methods=["GET", "POST"],
)
@login_required
def payment_otp(order_number):
    """Collect an OTP required by Paystack for an NFC payment."""

    nfc_order = NFCOrder.query.filter_by(
        order_number=order_number,
        user_id=current_user.id,
    ).first_or_404()

    if nfc_order.payment_status == "success":
        return redirect(
            url_for(
                "nfc.payment_status",
                order_number=nfc_order.order_number,
            )
        )

    form = OtpForm()

    if form.validate_on_submit():
        try:
            data = paystack.submit_otp(
                form.otp.data,
                nfc_order.payment_reference,
            )

            current_app.logger.info(
                "NFC OTP submitted for %s: %s",
                nfc_order.order_number,
                data.get("status")
                if data
                else None,
            )

            # Paystack's OTP response is not treated as final
            # confirmation. Verify through the normal payment path.
            _verify_nfc_payment(nfc_order)

            if nfc_order.payment_status == "success":
                flash(
                    "Payment successful. Your NFC order has been received.",
                    "success",
                )
            else:
                flash(
                    "OTP submitted. Waiting for payment confirmation.",
                    "success",
                )

            return redirect(
                url_for(
                    "nfc.payment_status",
                    order_number=nfc_order.order_number,
                )
            )

        except paystack.PaymentError as exc:
            flash(
                str(exc),
                "error",
            )

    provider_label = "your mobile network"

    return render_template(
        "nfc/otp.html",
        form=form,
        nfc_order=nfc_order,
        provider_label=provider_label,
    )


@bp.route(
    "/payment/<order_number>/status",
    methods=["GET"],
)
@login_required
def payment_status_json(order_number):
    """Return the current NFC payment status as JSON."""

    nfc_order = NFCOrder.query.filter_by(
        order_number=order_number,
        user_id=current_user.id,
    ).first_or_404()

    if nfc_order.payment_status == "pending":
        _verify_nfc_payment(nfc_order)

    return jsonify(
        {
            "order_number": nfc_order.order_number,
            "reference": nfc_order.payment_reference,
            "status": nfc_order.payment_status,
            "redirect": (
                url_for(
                    "nfc.payment_status",
                    order_number=nfc_order.order_number,
                )
                if nfc_order.payment_status != "pending"
                else None
            ),
        }
    )


@bp.route(
    "/callback",
    methods=["GET"],
)
@login_required
def callback():
    """Handle Paystack's hosted-card callback."""

    reference = (
        request.args.get("reference")
        or request.args.get("trxref")
    )

    if not reference:
        flash(
            "No payment reference was returned.",
            "error",
        )

        return redirect(
            url_for("nfc.order")
        )

    nfc_order = NFCOrder.query.filter_by(
        payment_reference=reference,
        user_id=current_user.id,
    ).first()

    if nfc_order is None:
        flash(
            "NFC order payment could not be found.",
            "error",
        )

        return redirect(
            url_for("nfc.order")
        )

    if _verify_nfc_payment(nfc_order):
        flash(
            "Payment successful. Your NFC order has been received.",
            "success",
        )

    else:
        flash(
            "Payment has not yet been confirmed. "
            "Please check the payment status.",
            "warning",
        )

    return redirect(
        url_for(
            "nfc.payment_status",
            order_number=nfc_order.order_number,
        )
    )


@bp.route(
    "/payment/<order_number>",
    methods=["GET"],
)
@login_required
def payment_status(order_number):
    """Display and refresh the status of an NFC payment."""

    nfc_order = NFCOrder.query.filter_by(
        order_number=order_number,
        user_id=current_user.id,
    ).first_or_404()

    if nfc_order.payment_status == "pending":
        _verify_nfc_payment(nfc_order)

    elif (
        nfc_order.payment_status == "success"
        and not nfc_order.admin_notified_at
    ):
        # Retry an admin notification that may have failed previously.
        _notify_admin(nfc_order)

    return render_template(
        "nfc/payment_status.html",
        nfc_order=nfc_order,
        currency=nfc_order.currency,
    )