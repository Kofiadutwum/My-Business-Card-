"""Database models.

Design notes worth carrying in your head while reading this file:

*   Money is never stored as a float. Every amount is an integer of the
    smallest currency unit (pesewas). Floats lose pennies; integers do not.
*   A profile is never "deleted" when a subscription lapses. It is simply
    not served publicly. The data survives so a renewal restores the card
    intact, which is the whole point of an annual model.
*   Analytics live in their own table (CardView) rather than as counters on
    Profile, because a counter can tell you *how many* but never *when*,
    *from where* or *how often the same card is re-scanned*.
"""

from datetime import datetime, timedelta, timezone

from flask import current_app
from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db, login_manager


def utcnow():
    """Timezone-aware UTC. Naive datetimes cause quiet bugs at renewal time."""
    return datetime.now(timezone.utc)


def _aware(value):
    """SQLite hands back naive datetimes; normalise before comparing."""
    if value is None:
        return None

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)

    return value


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    email = db.Column(
        db.String(255),
        unique=True,
        nullable=False,
        index=True,
    )

    password_hash = db.Column(
        db.String(255),
        nullable=False,
    )

    # Phone number associated with the user's account.
    # This is separate from Profile.phone because it is used for
    # account recovery and authentication-related purposes.
    phone = db.Column(
        db.String(32),
        unique=True,
    )

    # Google's stable account identifier returned in the verified
    # Google ID token. Do not use the Google email as the identifier
    # because the email address can change.
    google_sub = db.Column(
        db.String(255),
        unique=True,
        index=True,
    )

    # Records the last successful email change. The account-management
    # layer will enforce the 90-day waiting period.
    email_changed_at = db.Column(
        db.DateTime,
    )

    is_admin = db.Column(
        db.Boolean,
        default=False,
        nullable=False,
    )

    is_suspended = db.Column(
        db.Boolean,
        default=False,
        nullable=False,
    )

    created_at = db.Column(
        db.DateTime,
        default=utcnow,
        nullable=False,
    )

    last_login_at = db.Column(
        db.DateTime,
    )

    profile = db.relationship(
        "Profile",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )

    subscriptions = db.relationship(
        "Subscription",
        back_populates="user",
        cascade="all, delete-orphan",
        order_by="Subscription.expires_at.desc()",
    )

    payments = db.relationship(
        "Payment",
        back_populates="user",
        cascade="all, delete-orphan",
        order_by="Payment.created_at.desc()",
    )

    password_reset_otps = db.relationship(
        "PasswordResetOTP",
        back_populates="user",
        cascade="all, delete-orphan",
        order_by="PasswordResetOTP.created_at.desc()",
    )

    def set_password(self, raw):
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw):
        return check_password_hash(
            self.password_hash,
            raw,
        )

    @property
    def current_subscription(self):
        """The subscription with the furthest expiry, active or not."""
        if not self.subscriptions:
            return None

        return self.subscriptions[0]

    @property
    def is_subscription_active(self):
        sub = self.current_subscription

        return bool(
            sub
            and sub.is_active
        )

    @property
    def days_to_expiry(self):
        sub = self.current_subscription

        if not sub:
            return None

        delta = (
            _aware(sub.expires_at)
            - utcnow()
        )

        return delta.days

    @property
    def can_change_email(self):
        """Whether the user has passed the 90-day email-change window."""
        if not self.email_changed_at:
            return True

        next_change = (
            _aware(self.email_changed_at)
            + timedelta(days=90)
        )

        return utcnow() >= next_change

    @property
    def next_email_change_at(self):
        """Return the next permitted email-change date/time."""
        if not self.email_changed_at:
            return None

        return (
            _aware(self.email_changed_at)
            + timedelta(days=90)
        )

    def __repr__(self):
        return f"<User {self.email}>"


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(
        User,
        int(user_id),
    )


class PasswordResetOTP(db.Model):
    """One-time password-reset/recovery code.

    The actual OTP is never stored in the database.
    Only a password hash of the OTP is stored.
    """

    __tablename__ = "password_reset_otps"

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    user_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "users.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    phone = db.Column(
        db.String(32),
        nullable=False,
        index=True,
    )

    otp_hash = db.Column(
        db.String(255),
        nullable=False,
    )

    expires_at = db.Column(
        db.DateTime,
        nullable=False,
        index=True,
    )

    attempts = db.Column(
        db.Integer,
        default=0,
        nullable=False,
    )

    used_at = db.Column(
        db.DateTime,
    )

    created_at = db.Column(
        db.DateTime,
        default=utcnow,
        nullable=False,
        index=True,
    )

    user = db.relationship(
        "User",
        back_populates="password_reset_otps",
    )

    def set_otp(self, otp):
        """Hash and store an OTP without storing the raw code."""
        self.otp_hash = generate_password_hash(
            str(otp)
        )

    def check_otp(self, otp):
        """Verify an OTP and increment the attempt counter."""
        if self.used_at:
            return False

        if self.is_expired:
            return False

        self.attempts += 1

        if self.attempts > 5:
            return False

        return check_password_hash(
            self.otp_hash,
            str(otp),
        )

    @property
    def is_expired(self):
        return (
            utcnow()
            >= _aware(self.expires_at)
        )

    @property
    def is_used(self):
        return self.used_at is not None

    @property
    def is_valid(self):
        return (
            not self.is_used
            and not self.is_expired
            and self.attempts < 5
        )

    def mark_used(self):
        self.used_at = utcnow()

    def __repr__(self):
        return (
            f"<PasswordResetOTP "
            f"user={self.user_id} "
            f"phone={self.phone}>"
        )


class Profile(db.Model):
    """The contact card itself."""

    __tablename__ = "profiles"

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    user_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "users.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        unique=True,
    )

    slug = db.Column(
        db.String(64),
        unique=True,
        nullable=False,
        index=True,
    )

    full_name = db.Column(
        db.String(120),
        nullable=False,
    )

    job_title = db.Column(
        db.String(120),
    )

    organisation = db.Column(
        db.String(160),
    )

    phone = db.Column(
        db.String(32),
    )

    whatsapp = db.Column(
        db.String(32),
    )

    email = db.Column(
        db.String(255),
    )

    website = db.Column(
        db.String(255),
    )

    location = db.Column(
        db.String(160),
    )

    bio = db.Column(
        db.Text,
    )

    avatar_filename = db.Column(
        db.String(255),
    )

    accent = db.Column(
        db.String(16),
        default="blue",
        nullable=False,
    )

    is_published = db.Column(
        db.Boolean,
        default=True,
        nullable=False,
    )

    created_at = db.Column(
        db.DateTime,
        default=utcnow,
        nullable=False,
    )

    updated_at = db.Column(
        db.DateTime,
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )

    user = db.relationship(
        "User",
        back_populates="profile",
    )

    social_links = db.relationship(
        "SocialLink",
        back_populates="profile",
        cascade="all, delete-orphan",
        order_by="SocialLink.position",
    )

    views = db.relationship(
        "CardView",
        back_populates="profile",
        cascade="all, delete-orphan",
    )

    @property
    def is_live(self):
        """A card is served publicly only when paid for and published."""
        return (
            self.is_published
            and self.user.is_subscription_active
            and not self.user.is_suspended
        )

    @property
    def initials(self):
        parts = [
            p
            for p in (self.full_name or "").split()
            if p
        ]

        if not parts:
            return "?"

        if len(parts) == 1:
            return parts[0][:2].upper()

        return (
            parts[0][0]
            + parts[-1][0]
        ).upper()

    @property
    def view_count(self):
        return len(self.views)

    def __repr__(self):
        return f"<Profile {self.slug}>"


class SocialLink(db.Model):
    __tablename__ = "social_links"

    PLATFORMS = {
        "linkedin": "LinkedIn",
        "x": "X",
        "facebook": "Facebook",
        "instagram": "Instagram",
        "tiktok": "TikTok",
        "youtube": "YouTube",
        "whatsapp": "WhatsApp",
        "telegram": "Telegram",
        "github": "GitHub",
        "behance": "Behance",
        "researchgate": "ResearchGate",
        "orcid": "ORCID",
        "scholar": "Google Scholar",
        "website": "Website",
    }

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    profile_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "profiles.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    platform = db.Column(
        db.String(32),
        nullable=False,
    )

    url = db.Column(
        db.String(500),
        nullable=False,
    )

    position = db.Column(
        db.Integer,
        default=0,
        nullable=False,
    )

    profile = db.relationship(
        "Profile",
        back_populates="social_links",
    )

    @property
    def label(self):
        return self.PLATFORMS.get(
            self.platform,
            self.platform.title(),
        )


class Subscription(db.Model):
    __tablename__ = "subscriptions"

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    user_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "users.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    plan = db.Column(
        db.String(32),
        nullable=False,
    )

    starts_at = db.Column(
        db.DateTime,
        default=utcnow,
        nullable=False,
    )

    expires_at = db.Column(
        db.DateTime,
        nullable=False,
    )

    cancelled_at = db.Column(
        db.DateTime,
    )

    user = db.relationship(
        "User",
        back_populates="subscriptions",
    )

    payment = db.relationship(
        "Payment",
        back_populates="subscription",
        uselist=False,
    )

    @property
    def is_active(self):
        if self.cancelled_at:
            return False

        grace = timedelta(
            days=current_app.config[
                "GRACE_PERIOD_DAYS"
            ]
        )

        return (
            utcnow()
            <= _aware(self.expires_at)
            + grace
        )

    @property
    def in_grace_period(self):
        now = utcnow()
        expiry = _aware(
            self.expires_at
        )

        grace = timedelta(
            days=current_app.config[
                "GRACE_PERIOD_DAYS"
            ]
        )

        return (
            expiry < now
            <= expiry + grace
        )

    @property
    def status(self):
        if self.cancelled_at:
            return "cancelled"

        if self.in_grace_period:
            return "grace"

        return (
            "active"
            if self.is_active
            else "expired"
        )

    @classmethod
    def start_or_extend(
        cls,
        user,
        plan_key,
        months,
    ):
        """Renewal extends from the existing expiry, not from today."""

        now = utcnow()

        existing = user.current_subscription

        if (
            existing
            and _aware(existing.expires_at) > now
            and not existing.cancelled_at
        ):
            base = _aware(
                existing.expires_at
            )
        else:
            base = now

        return cls(
            user=user,
            plan=plan_key,
            starts_at=now,
            expires_at=(
                base
                + timedelta(
                    days=30 * months
                )
            ),
        )


class Payment(db.Model):
    __tablename__ = "payments"

    STATUSES = (
        "pending",
        "success",
        "failed",
        "abandoned",
    )

    CHANNELS = (
        "card",
        "mobile_money",
    )

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    user_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "users.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    subscription_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "subscriptions.id",
        ),
    )

    reference = db.Column(
        db.String(64),
        unique=True,
        nullable=False,
        index=True,
    )

    gateway = db.Column(
        db.String(32),
        default="paystack",
        nullable=False,
    )

    plan = db.Column(
        db.String(32),
        nullable=False,
    )

    amount_minor = db.Column(
        db.Integer,
        nullable=False,
    )

    currency = db.Column(
        db.String(8),
        default="GHS",
        nullable=False,
    )

    channel = db.Column(
        db.String(32),
        nullable=False,
    )

    momo_provider = db.Column(
        db.String(16),
    )

    momo_phone = db.Column(
        db.String(32),
    )

    status = db.Column(
        db.String(16),
        default="pending",
        nullable=False,
        index=True,
    )

    gateway_fee_minor = db.Column(
        db.Integer,
        default=0,
        nullable=False,
    )

    raw_response = db.Column(
        db.Text,
    )

    created_at = db.Column(
        db.DateTime,
        default=utcnow,
        nullable=False,
        index=True,
    )

    paid_at = db.Column(
        db.DateTime,
    )

    user = db.relationship(
        "User",
        back_populates="payments",
    )

    subscription = db.relationship(
        "Subscription",
        back_populates="payment",
    )

    @property
    def amount(self):
        """Major units, for display only."""
        return self.amount_minor / 100

    @property
    def net_minor(self):
        return (
            self.amount_minor
            - (self.gateway_fee_minor or 0)
        )


class CardView(db.Model):
    """One row per public card open."""

    __tablename__ = "card_views"

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    profile_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "profiles.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    viewed_at = db.Column(
        db.DateTime,
        default=utcnow,
        nullable=False,
        index=True,
    )

    source = db.Column(
        db.String(16),
        default="link",
        nullable=False,
    )

    action = db.Column(
        db.String(16),
        default="view",
        nullable=False,
    )

    visitor_hash = db.Column(
        db.String(64),
    )

    user_agent = db.Column(
        db.String(255),
    )

    profile = db.relationship(
        "Profile",
        back_populates="views",
    )


class Notification(db.Model):
    """One row per reminder actually sent."""

    __tablename__ = "notifications"

    __table_args__ = (
        db.UniqueConstraint(
            "subscription_id",
            "kind",
            name="uq_notification_once",
        ),
    )

    KINDS = (
        "renewal_30",
        "renewal_7",
        "renewal_1",
        "expired",
        "lead",
        "recovered",
    )

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    user_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "users.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    subscription_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "subscriptions.id",
            ondelete="CASCADE",
        ),
    )

    kind = db.Column(
        db.String(32),
        nullable=False,
    )

    channel = db.Column(
        db.String(16),
        default="sms",
        nullable=False,
    )

    destination = db.Column(
        db.String(64),
    )

    body = db.Column(
        db.Text,
    )

    status = db.Column(
        db.String(16),
        default="sent",
        nullable=False,
    )

    provider = db.Column(
        db.String(32),
    )

    provider_ref = db.Column(
        db.String(128),
    )

    error = db.Column(
        db.String(255),
    )

    created_at = db.Column(
        db.DateTime,
        default=utcnow,
        nullable=False,
        index=True,
    )

    user = db.relationship("User")

    def __repr__(self):
        return (
            f"<Notification "
            f"{self.kind} -> "
            f"{self.destination} "
            f"({self.status})>"
        )


class Lead(db.Model):
    """Contact details a visitor sent back to the card owner."""

    __tablename__ = "leads"

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    profile_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "profiles.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    name = db.Column(
        db.String(120),
        nullable=False,
    )

    phone = db.Column(
        db.String(32),
    )

    email = db.Column(
        db.String(255),
    )

    organisation = db.Column(
        db.String(160),
    )

    note = db.Column(
        db.Text,
    )

    source = db.Column(
        db.String(16),
        default="link",
        nullable=False,
    )

    visitor_hash = db.Column(
        db.String(64),
        index=True,
    )

    is_read = db.Column(
        db.Boolean,
        default=False,
        nullable=False,
    )

    created_at = db.Column(
        db.DateTime,
        default=utcnow,
        nullable=False,
        index=True,
    )

    profile = db.relationship(
        "Profile",
        backref=db.backref(
            "leads",
            cascade="all, delete-orphan",
        ),
    )

    def __repr__(self):
        return (
            f"<Lead "
            f"{self.name} -> "
            f"{self.profile_id}>"
        )


class GalleryImage(db.Model):
    """An image displayed in the public homepage gallery."""

    __tablename__ = "gallery_images"

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    filename = db.Column(
        db.String(255),
        nullable=False,
    )

    title = db.Column(
        db.String(160),
    )

    description = db.Column(
        db.Text,
    )

    display_order = db.Column(
        db.Integer,
        default=0,
        nullable=False,
    )

    is_published = db.Column(
        db.Boolean,
        default=True,
        nullable=False,
    )

    created_at = db.Column(
        db.DateTime,
        default=utcnow,
        nullable=False,
    )

    updated_at = db.Column(
        db.DateTime,
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )

    def __repr__(self):
        return f"<GalleryImage {self.filename}>"


class NFCOrder(db.Model):
    """Physical NFC card orders placed by customers."""

    __tablename__ = "nfc_orders"

    ORDER_TYPES = (
        "digital_and_nfc",
        "nfc_only",
        "replacement",
    )

    STATUSES = (
        "pending",
        "paid",
        "design_pending",
        "designing",
        "ready",
        "delivered",
        "cancelled",
    )

    PAYMENT_STATUSES = (
        "pending",
        "success",
        "failed",
        "abandoned",
    )

    NFC_PRICES_MINOR = {
        1: 20000,
        5: 75000,
    }

    REPLACEMENT_DISCOUNT = 0.30

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    user_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "users.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    profile_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "profiles.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    order_number = db.Column(
        db.String(32),
        unique=True,
        nullable=False,
        index=True,
    )

    order_type = db.Column(
        db.String(32),
        nullable=False,
        index=True,
    )

    quantity = db.Column(
        db.Integer,
        nullable=False,
    )

    nfc_price_minor = db.Column(
        db.Integer,
        nullable=False,
    )

    discount_minor = db.Column(
        db.Integer,
        default=0,
        nullable=False,
    )

    digital_plan = db.Column(
        db.String(32),
    )

    digital_plan_price_minor = db.Column(
        db.Integer,
        default=0,
        nullable=False,
    )

    total_amount_minor = db.Column(
        db.Integer,
        nullable=False,
    )

    currency = db.Column(
        db.String(8),
        default="GHS",
        nullable=False,
    )

    full_name = db.Column(
        db.String(120),
        nullable=False,
    )

    position = db.Column(
        db.String(120),
    )

    email = db.Column(
        db.String(255),
        nullable=False,
    )

    phone = db.Column(
        db.String(32),
        nullable=False,
    )

    delivery_location = db.Column(
        db.String(255),
        nullable=False,
    )

    logo_filename = db.Column(
        db.String(255),
    )

    design_instructions = db.Column(
        db.Text,
    )

    replacement_reason = db.Column(
        db.String(64),
    )

    payment_reference = db.Column(
        db.String(128),
        unique=True,
        index=True,
    )

    payment_status = db.Column(
        db.String(16),
        default="pending",
        nullable=False,
        index=True,
    )

    paid_at = db.Column(
        db.DateTime,
    )

    status = db.Column(
        db.String(32),
        default="pending",
        nullable=False,
        index=True,
    )

    # Records when the successful-payment notification was sent
    # to the AD Smart Business Cards administrator.
    #
    # This prevents duplicate notification emails when Paystack
    # sends the same payment confirmation more than once.
    admin_notified_at = db.Column(
        db.DateTime,
    )

    created_at = db.Column(
        db.DateTime,
        default=utcnow,
        nullable=False,
        index=True,
    )

    updated_at = db.Column(
        db.DateTime,
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )

    user = db.relationship(
        "User",
        backref=db.backref(
            "nfc_orders",
            cascade="all, delete-orphan",
        ),
    )

    profile = db.relationship(
        "Profile",
        backref=db.backref(
            "nfc_orders",
            cascade="all, delete-orphan",
        ),
    )

    @property
    def nfc_price(self):
        """NFC package price in major currency units."""
        return self.nfc_price_minor / 100

    @property
    def discount(self):
        """Discount amount in major currency units."""
        return self.discount_minor / 100

    @property
    def digital_plan_price(self):
        """Digital plan price in major currency units."""
        return self.digital_plan_price_minor / 100

    @property
    def total_amount(self):
        """Total order amount in major currency units."""
        return self.total_amount_minor / 100

    @classmethod
    def calculate_nfc_price(
        cls,
        quantity,
        replacement=False,
    ):
        """Return NFC price and discount in minor currency units."""

        if quantity not in cls.NFC_PRICES_MINOR:
            raise ValueError(
                "NFC quantity must be either 1 or 5."
            )

        base_price = cls.NFC_PRICES_MINOR[
            quantity
        ]

        if replacement:
            discount = int(
                base_price
                * cls.REPLACEMENT_DISCOUNT
            )

            return (
                base_price - discount,
                discount,
            )

        return base_price, 0

    def __repr__(self):
        return (
            f"<NFCOrder "
            f"{self.order_number} "
            f"{self.order_type}>"
        )