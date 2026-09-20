"""Paystack gateway adapter.

Everything the rest of the application knows about payments passes through
this module. Swapping to Hubtel or expressPay later means writing a second
adapter with the same four methods, not editing views.

The two channels behave very differently and the difference drives the design:

    Card  ->  Initialise a transaction, redirect the customer to Paystack's
              hosted page, they return to our callback. Synchronous enough
              to verify on return.

    MoMo  ->  Create a charge, then the customer approves on their handset,
              possibly minutes later, possibly on a different network. The
              browser is not part of that conversation. Paystack's own
              documentation is explicit that because payment is completed
              offline you need a webhook URL to receive the final status.

Which is why the webhook is the source of truth and the callback is only a
convenience. If you verify on callback alone, every customer who closes the
tab while waiting for the MoMo prompt pays you and stays deactivated.
"""

import hashlib
import hmac
import json
import secrets
import time

import requests
from flask import current_app

# Paystack's provider codes. 'vod' predates the Vodafone Ghana rebrand to
# Telecel; the API code is still vod even though customers say Telecel.
MOMO_PROVIDERS = {
    "mtn": "MTN MoMo",
    "atl": "AirtelTigo Money",
    "vod": "Telecel Cash",
}


class PaymentError(Exception):
    """Raised when the gateway refuses or a request fails."""


def new_reference(prefix="KC"):
    """Unique transaction reference.

    Paystack permits alphanumerics plus - . =, so the timestamp-and-token
    format below is safe and sorts chronologically when read by a human.
    """
    return f"{prefix}-{int(time.time())}-{secrets.token_hex(4).upper()}"


def _headers():
    return {
        "Authorization": f"Bearer {current_app.config['PAYSTACK_SECRET_KEY']}",
        "Content-Type": "application/json",
    }


def _sandbox():
    return current_app.config["PAYMENT_SANDBOX"] or not current_app.config[
        "PAYSTACK_SECRET_KEY"
    ]


def _post(path, payload):
    url = current_app.config["PAYSTACK_BASE_URL"] + path
    try:
        response = requests.post(url, headers=_headers(), json=payload, timeout=30)
    except requests.RequestException as exc:
        raise PaymentError(f"Could not reach the payment gateway: {exc}") from exc
    body = response.json() if response.content else {}
    if not body.get("status"):
        raise PaymentError(body.get("message", "The gateway declined this request."))
    return body


def _get(path):
    url = current_app.config["PAYSTACK_BASE_URL"] + path
    try:
        response = requests.get(url, headers=_headers(), timeout=30)
    except requests.RequestException as exc:
        raise PaymentError(f"Could not reach the payment gateway: {exc}") from exc
    body = response.json() if response.content else {}
    if not body.get("status"):
        raise PaymentError(body.get("message", "The gateway declined this request."))
    return body


# --------------------------------------------------------------------------
# Card
# --------------------------------------------------------------------------
def initialise_card(email, amount_minor, reference, callback_url, metadata=None):
    """Start a hosted card transaction. Returns the URL to redirect to."""
    if _sandbox():
        return {
            "authorization_url": f"{callback_url}?reference={reference}&simulated=1",
            "reference": reference,
            "simulated": True,
        }

    body = _post(
        "/transaction/initialize",
        {
            "email": email,
            "amount": amount_minor,
            "currency": current_app.config["CURRENCY"],
            "reference": reference,
            "callback_url": callback_url,
            "channels": ["card"],
            "metadata": metadata or {},
        },
    )
    return body["data"]


# --------------------------------------------------------------------------
# Mobile money
# --------------------------------------------------------------------------
def charge_mobile_money(email, amount_minor, reference, phone, provider, metadata=None):
    """Create a MoMo charge. The customer approves on their handset.

    Paystack replies with one of several statuses. The two that matter:
      pay_offline  -> the prompt has gone out; wait for the webhook
      send_otp     -> the customer must supply an OTP (some networks)
    """
    if provider not in MOMO_PROVIDERS:
        raise PaymentError("Choose MTN MoMo, Telecel Cash or AirtelTigo Money.")

    if _sandbox():
        return {
            "status": "pay_offline",
            "reference": reference,
            "display_text": "Approve the prompt on your phone to finish.",
            "simulated": True,
        }

    body = _post(
        "/charge",
        {
            "email": email,
            "amount": amount_minor,
            "currency": current_app.config["CURRENCY"],
            "reference": reference,
            "mobile_money": {"phone": phone, "provider": provider},
            "metadata": metadata or {},
        },
    )
    return body["data"]


def submit_otp(otp, reference):
    if _sandbox():
        return {"status": "success", "reference": reference, "simulated": True}
    return _post("/charge/submit_otp", {"otp": otp, "reference": reference})["data"]


# --------------------------------------------------------------------------
# Verification and webhooks
# --------------------------------------------------------------------------
def verify(reference, expected_amount=None, expected_currency=None):
    """Confirm a transaction's real status with the gateway.

    In sandbox mode, return the expected local values so the simulation
    follows the same validation path as a real transaction.
    """
    if _sandbox():
        outcome = current_app.config.get("PAYMENT_SANDBOX_OUTCOME", "success")

        if outcome not in {"success", "failed", "abandoned"}:
            outcome = "success"

        return {
            "status": outcome,
            "reference": reference,
            "amount": expected_amount,
            "currency": expected_currency or current_app.config["CURRENCY"],
            "channel": "simulated",
            "fees": 0,
            "simulated": True,
        }

    return _get(f"/transaction/verify/{reference}")["data"]


def signature_is_valid(raw_body, signature):
    """Verify the x-paystack-signature header (HMAC SHA512 of the raw body).

    Use the raw request bytes. Re-serialising the parsed JSON changes key
    order and whitespace, and the digest will never match.
    """
    secret = current_app.config["PAYSTACK_SECRET_KEY"].encode("utf-8")
    if not secret or not signature:
        return False
    expected = hmac.new(secret, raw_body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(expected, signature)


def parse_event(raw_body):
    try:
        return json.loads(raw_body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise PaymentError("Malformed webhook payload.") from exc
