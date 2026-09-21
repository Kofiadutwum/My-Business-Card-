"""Paystack gateway adapter.

All payment communication with Paystack lives in this module.

Card payments use Paystack's hosted checkout.

Mobile Money payments use Paystack's Charge API. The customer authorizes
the payment on their phone, so the final payment status may arrive later
through verification or the webhook.
"""

import hashlib
import hmac
import json
import logging
import secrets
import time

import requests
from flask import current_app


logger = logging.getLogger(__name__)


MOMO_PROVIDERS = {
    "mtn": "MTN MoMo",
    "atl": "AirtelTigo Money",
    "vod": "Telecel Cash",
}


class PaymentError(Exception):
    """Raised when Paystack refuses a request or cannot be reached."""


def new_reference(prefix="KC"):
    return f"{prefix}-{int(time.time())}-{secrets.token_hex(4).upper()}"


def _headers():
    secret_key = current_app.config.get("PAYSTACK_SECRET_KEY", "")

    return {
        "Authorization": f"Bearer {secret_key}",
        "Content-Type": "application/json",
    }


def _safe_key_info():
    """Return non-secret information about the configured Paystack key."""

    key = current_app.config.get("PAYSTACK_SECRET_KEY", "")

    return {
        "prefix": key[:8] if key else "",
        "length": len(key),
    }


def _sandbox():
    return current_app.config["PAYMENT_SANDBOX"] or not current_app.config[
        "PAYSTACK_SECRET_KEY"
    ]


def _post(path, payload):
    url = current_app.config["PAYSTACK_BASE_URL"] + path

    logger.info(
        "Paystack POST %s | key_prefix=%s | key_length=%s",
        path,
        _safe_key_info()["prefix"],
        _safe_key_info()["length"],
    )

    try:
        response = requests.post(
            url,
            headers=_headers(),
            json=payload,
            timeout=30,
        )
    except requests.RequestException as exc:
        logger.exception("Paystack request failed before receiving a response.")
        raise PaymentError(
            f"Could not reach the payment gateway: {exc}"
        ) from exc

    try:
        body = response.json() if response.content else {}
    except ValueError:
        body = {}

    logger.info(
        "Paystack response %s | HTTP %s | status=%s | message=%s",
        path,
        response.status_code,
        body.get("status"),
        body.get("message"),
    )

    if response.status_code >= 400:
        logger.error(
            "Paystack HTTP error | path=%s | HTTP=%s | body=%s",
            path,
            response.status_code,
            body,
        )

        raise PaymentError(
            body.get(
                "message",
                f"Paystack request failed with HTTP {response.status_code}.",
            )
        )

    if not body.get("status"):
        logger.error(
            "Paystack API rejected request | path=%s | body=%s",
            path,
            body,
        )

        raise PaymentError(
            body.get(
                "message",
                "The gateway declined this request.",
            )
        )

    return body


def _get(path):
    url = current_app.config["PAYSTACK_BASE_URL"] + path

    logger.info(
        "Paystack GET %s | key_prefix=%s | key_length=%s",
        path,
        _safe_key_info()["prefix"],
        _safe_key_info()["length"],
    )

    try:
        response = requests.get(
            url,
            headers=_headers(),
            timeout=30,
        )
    except requests.RequestException as exc:
        logger.exception("Paystack request failed before receiving a response.")
        raise PaymentError(
            f"Could not reach the payment gateway: {exc}"
        ) from exc

    try:
        body = response.json() if response.content else {}
    except ValueError:
        body = {}

    logger.info(
        "Paystack response %s | HTTP %s | status=%s | message=%s",
        path,
        response.status_code,
        body.get("status"),
        body.get("message"),
    )

    if response.status_code >= 400:
        logger.error(
            "Paystack HTTP error | path=%s | HTTP=%s | body=%s",
            path,
            response.status_code,
            body,
        )

        raise PaymentError(
            body.get(
                "message",
                f"Paystack request failed with HTTP {response.status_code}.",
            )
        )

    if not body.get("status"):
        logger.error(
            "Paystack API rejected request | path=%s | body=%s",
            path,
            body,
        )

        raise PaymentError(
            body.get(
                "message",
                "The gateway declined this request.",
            )
        )

    return body


def initialise_card(
    email,
    amount_minor,
    reference,
    callback_url,
    metadata=None,
):
    if _sandbox():
        return {
            "authorization_url": (
                f"{callback_url}?reference={reference}&simulated=1"
            ),
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


def charge_mobile_money(
    email,
    amount_minor,
    reference,
    phone,
    provider,
    metadata=None,
):
    if provider not in MOMO_PROVIDERS:
        raise PaymentError(
            "Choose MTN MoMo, Telecel Cash or AirtelTigo Money."
        )

    if _sandbox():
        return {
            "status": "pay_offline",
            "reference": reference,
            "display_text": (
                "Approve the prompt on your phone to finish."
            ),
            "simulated": True,
        }

    payload = {
        "email": email,
        "amount": amount_minor,
        "currency": current_app.config["CURRENCY"],
        "reference": reference,
        "mobile_money": {
            "phone": phone,
            "provider": provider,
        },
        "metadata": metadata or {},
    }

    logger.info(
        "Starting Paystack MoMo charge | reference=%s | "
        "amount=%s | currency=%s | provider=%s",
        reference,
        amount_minor,
        current_app.config["CURRENCY"],
        provider,
    )

    body = _post("/charge", payload)

    return body["data"]


def submit_otp(otp, reference):
    if _sandbox():
        return {
            "status": "success",
            "reference": reference,
            "simulated": True,
        }

    return _post(
        "/charge/submit_otp",
        {
            "otp": otp,
            "reference": reference,
        },
    )["data"]


def verify(
    reference,
    expected_amount=None,
    expected_currency=None,
):
    if _sandbox():
        outcome = current_app.config.get(
            "PAYMENT_SANDBOX_OUTCOME",
            "success",
        )

        if outcome not in {"success", "failed", "abandoned"}:
            outcome = "success"

        return {
            "status": outcome,
            "reference": reference,
            "amount": expected_amount,
            "currency": (
                expected_currency
                or current_app.config["CURRENCY"]
            ),
            "channel": "simulated",
            "fees": 0,
            "simulated": True,
        }

    return _get(
        f"/transaction/verify/{reference}"
    )["data"]


def signature_is_valid(raw_body, signature):
    secret = current_app.config["PAYSTACK_SECRET_KEY"].encode("utf-8")

    if not secret or not signature:
        return False

    expected = hmac.new(
        secret,
        raw_body,
        hashlib.sha512,
    ).hexdigest()

    return hmac.compare_digest(
        expected,
        signature,
    )


def parse_event(raw_body):
    try:
        return json.loads(
            raw_body.decode("utf-8")
        )
    except (
        ValueError,
        UnicodeDecodeError,
    ) as exc:
        raise PaymentError(
            "Malformed webhook payload."
        ) from exc