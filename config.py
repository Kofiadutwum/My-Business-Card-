"""Application configuration.

Every secret is read from the environment. Nothing sensitive is hard-coded,
so the same codebase runs in development, staging and production.
"""

import os
from datetime import timedelta

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


class BaseConfig:
    SECRET_KEY = os.environ.get("SECRET_KEY", "change-me-in-production")

    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", "sqlite:///" + os.path.join(BASE_DIR, "instance", "cardhub.db")
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Session hardening
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    PERMANENT_SESSION_LIFETIME = timedelta(days=14)
    REMEMBER_COOKIE_DURATION = timedelta(days=14)

    # Uploads
    MAX_CONTENT_LENGTH = 3 * 1024 * 1024  # 3 MB profile photo ceiling
    UPLOAD_FOLDER = os.path.join(BASE_DIR, "app", "static", "img", "avatars")
    ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}

    # --- Commercial model -------------------------------------------------
    # Amounts are stored in the smallest currency unit (pesewas) everywhere,
    # exactly as Paystack expects. 12000 pesewas = GHS 120.00
    CURRENCY = os.environ.get("CURRENCY", "GHS")
    PLANS = {
        "starter": {
            "label": "Starter",
            "amount_minor": 10000,
            "months": 12,
            "max_social_links": 5,
            "blurb": "One card, QR sharing, five social links.",
        },
        "professional": {
            "label": "Professional",
            "amount_minor": 20000,
            "months": 12,
            "max_social_links": 12,
            "blurb": "Custom link, twelve social links, scan analytics.",
        },
        "business": {
            "label": "Business",
            "amount_minor": 50000,
            "months": 12,
            "max_social_links": 25,
            "blurb": "Everything in Professional, plus team billing.",
        },
    }
    USD_GHS_RATE = float(os.getenv("USD_GHS_RATE", "0.0869909"))

    GRACE_PERIOD_DAYS = int(os.environ.get("GRACE_PERIOD_DAYS", 7))
    RENEWAL_NOTICE_DAYS = int(os.environ.get("RENEWAL_NOTICE_DAYS", 30))

    # --- Payment gateway --------------------------------------------------
    PAYMENT_PROVIDER = os.environ.get("PAYMENT_PROVIDER", "paystack")
    PAYSTACK_SECRET_KEY = os.environ.get("PAYSTACK_SECRET_KEY", "")
    PAYSTACK_PUBLIC_KEY = os.environ.get("PAYSTACK_PUBLIC_KEY", "")
    PAYSTACK_BASE_URL = "https://api.paystack.co"
    # With no live keys present the gateway runs in simulation mode so the
    # whole flow can be demonstrated offline.
    PAYMENT_SANDBOX = os.environ.get("PAYMENT_SANDBOX", "1") == "1"
    PAYMENT_SANDBOX_OUTCOME = os.environ.get("PAYMENT_SANDBOX_OUTCOME", "success")

    # --- SMS --------------------------------------------------------------
    # 'console' prints instead of sending, which is the default so that a
    # fresh clone can run the reminder job without an SMS account and nobody
    # texts a real person by accident while testing.
    SMS_PROVIDER = os.environ.get("SMS_PROVIDER", "console")
    SMS_API_KEY = os.environ.get("SMS_API_KEY", "")
    SMS_CLIENT_ID = os.environ.get("SMS_CLIENT_ID", "")       # Hubtel only
    SMS_CLIENT_SECRET = os.environ.get("SMS_CLIENT_SECRET", "")  # Hubtel only
    # Must be registered and approved with your provider before it delivers.
    # 11 characters is the ceiling for an alphanumeric sender ID, so the
    # full business name will not fit. "ADSMART" is within the limit and
    # still recognisable on a handset.
    SMS_SENDER_ID = os.environ.get("SMS_SENDER_ID", "ADSMART")

    # --- Lead capture -------------------------------------------------------
    LEADS_ENABLED = os.environ.get("LEADS_ENABLED", "1") == "1"
    LEADS_PER_VISITOR_PER_HOUR = int(os.environ.get("LEADS_PER_VISITOR_PER_HOUR", 3))

    SITE_NAME = os.environ.get("SITE_NAME", "AD Smart Business Cards")
    SITE_SHORT_NAME = os.environ.get("SITE_SHORT_NAME", "AD Smart")
    SUPPORT_EMAIL = os.environ.get("SUPPORT_EMAIL", "adgraphics881@gmail.com")
    SUPPORT_PHONE = os.environ.get("SUPPORT_PHONE", "0545875881")
    SITE_URL = os.environ.get("SITE_URL", "http://127.0.0.1:5000")


class DevelopmentConfig(BaseConfig):
    DEBUG = True


class ProductionConfig(BaseConfig):
    DEBUG = False
    SESSION_COOKIE_SECURE = True
    REMEMBER_COOKIE_SECURE = True


class TestingConfig(BaseConfig):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"


CONFIGS = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
}
