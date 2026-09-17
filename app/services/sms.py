"""SMS delivery.

WHY SMS AND NOT EMAIL
Annual billing dies of silence. Someone pays in March, hears nothing for
twelve months, and finds out the card went dark when a client tells them.
In Ghana an SMS gets read; a marketing email often does not arrive at all.
This is the cheapest insurance against silent churn that exists.

VERIFY THESE ENDPOINTS BEFORE GOING LIVE
The request shapes below reflect each provider's published API as understood
at the time of writing, but SMS providers change paths, parameter names and
authentication headers without much ceremony. Before you take real money,
open your provider's current documentation, send one test message, and
correct whatever has moved. Treat this file as a starting point, not as
verified fact.

SENDER ID REGISTRATION
This trips up nearly everyone on their first send. In Ghana an alphanumeric
sender ID ("KuulCard") must be registered with your provider and approved
before it will deliver. Unregistered IDs are silently dropped or rewritten,
so messages appear to send successfully and never arrive. Register the sender
ID first, then test, then schedule the job.
"""

import requests
from flask import current_app


class SmsError(Exception):
    """Raised when a message could not be handed to the provider."""


def normalise(number):
    """Convert a Ghanaian number to the international form providers expect.

    0244123456 -> 233244123456
    +233244123456 -> 233244123456

    Providers are inconsistent about whether they want the leading plus, and
    most reject the local 0 prefix outright, which produces a 'sent' response
    and no delivery.
    """
    if not number:
        raise SmsError("No phone number to send to.")
    cleaned = "".join(ch for ch in str(number) if ch.isdigit() or ch == "+")
    cleaned = cleaned.lstrip("+")
    if cleaned.startswith("0"):
        cleaned = "233" + cleaned[1:]
    if not cleaned.startswith("233") or len(cleaned) != 12:
        raise SmsError(f"{number} is not a valid Ghanaian mobile number.")
    return cleaned


def _sender():
    return current_app.config.get("SMS_SENDER_ID", "KuulCard")


# --------------------------------------------------------------------------
# Provider drivers. Each returns (provider_reference, raw_response).
# --------------------------------------------------------------------------
def _send_console(recipient, message):
    """Development driver. Prints instead of sending, and costs nothing.

    This is the default so that a fresh clone can run the reminder job end to
    end without an SMS account, and so nobody accidentally texts real people
    while testing.
    """
    current_app.logger.info("SMS (console) -> %s: %s", recipient, message)
    print(f"\n  [SMS] to {recipient}\n  {message}\n")
    return "console", {"simulated": True}


def _send_arkesel(recipient, message):
    key = current_app.config.get("SMS_API_KEY")
    if not key:
        raise SmsError("SMS_API_KEY is not set.")
    try:
        response = requests.post(
            "https://sms.arkesel.com/api/v2/sms/send",
            headers={"api-key": key, "Content-Type": "application/json"},
            json={"sender": _sender(), "message": message, "recipients": [recipient]},
            timeout=20,
        )
    except requests.RequestException as exc:
        raise SmsError(f"Could not reach Arkesel: {exc}") from exc

    body = response.json() if response.content else {}
    if response.status_code >= 400 or str(body.get("status", "")).lower() not in ("success", "ok"):
        raise SmsError(body.get("message") or f"Arkesel refused the message ({response.status_code}).")

    data = body.get("data")
    reference = ""
    if isinstance(data, list) and data:
        reference = str(data[0].get("id", ""))
    return reference, body


def _send_mnotify(recipient, message):
    key = current_app.config.get("SMS_API_KEY")
    if not key:
        raise SmsError("SMS_API_KEY is not set.")
    try:
        response = requests.post(
            "https://api.mnotify.com/api/sms/quick",
            params={"key": key},
            json={
                "recipient": [recipient],
                "sender": _sender(),
                "message": message,
                "is_schedule": False,
                "schedule_date": "",
            },
            timeout=20,
        )
    except requests.RequestException as exc:
        raise SmsError(f"Could not reach mNotify: {exc}") from exc

    body = response.json() if response.content else {}
    if response.status_code >= 400:
        raise SmsError(body.get("message") or f"mNotify refused the message ({response.status_code}).")
    return str(body.get("summary", {}).get("_id", "")), body


def _send_hubtel(recipient, message):
    client_id = current_app.config.get("SMS_CLIENT_ID")
    client_secret = current_app.config.get("SMS_CLIENT_SECRET")
    if not (client_id and client_secret):
        raise SmsError("SMS_CLIENT_ID and SMS_CLIENT_SECRET are not set.")
    try:
        response = requests.get(
            "https://smsc.hubtel.com/v1/messages/send",
            auth=(client_id, client_secret),
            params={"From": _sender(), "To": recipient, "Content": message},
            timeout=20,
        )
    except requests.RequestException as exc:
        raise SmsError(f"Could not reach Hubtel: {exc}") from exc

    body = response.json() if response.content else {}
    if response.status_code >= 400:
        raise SmsError(body.get("Message") or f"Hubtel refused the message ({response.status_code}).")
    return str(body.get("MessageId", "")), body


DRIVERS = {
    "console": _send_console,
    "arkesel": _send_arkesel,
    "mnotify": _send_mnotify,
    "hubtel": _send_hubtel,
}


def send(recipient, message):
    """Send one message. Returns (provider_name, reference, raw_response)."""
    provider = current_app.config.get("SMS_PROVIDER", "console")
    driver = DRIVERS.get(provider)
    if driver is None:
        raise SmsError(f"Unknown SMS provider: {provider}")

    number = normalise(recipient)
    body = message.strip()

    # A GSM-7 segment is 160 characters; beyond that you are billed per extra
    # segment. Templates are written to fit one, and this catches any that
    # have quietly grown past it.
    if len(body) > 160:
        current_app.logger.warning(
            "SMS is %d characters and will bill as %d segments.",
            len(body),
            (len(body) // 153) + 1,
        )

    reference, raw = driver(number, body)
    return provider, reference, raw
