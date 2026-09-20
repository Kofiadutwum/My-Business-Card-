"""Email service for CardHub using Resend."""

import resend

from flask import current_app


def send_email(
    recipient,
    subject,
    body,
):
    """
    Send a plain-text transactional email using Resend.

    The Resend API key is read from Flask configuration/environment
    variables and is never hard-coded.
    """

    api_key = current_app.config.get("RESEND_API_KEY")
    sender = current_app.config.get(
        "RESEND_FROM_EMAIL",
        "onboarding@resend.dev",
    )

    if not api_key:
        raise RuntimeError(
            "RESEND_API_KEY is not configured."
        )

    if not sender:
        raise RuntimeError(
            "RESEND_FROM_EMAIL is not configured."
        )

    resend.api_key = api_key

    params = {
        "from": sender,
        "to": [recipient],
        "subject": subject,
        "text": body,
    }

    response = resend.Emails.send(params)

    return response