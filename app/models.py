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

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    is_suspended = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    last_login_at = db.Column(db.DateTime)

    profile = db.relationship(
        "Profile", back_populates="user", uselist=False, cascade="all, delete-orphan"
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

    # --- password -------------------------------------------------------
    def set_password(self, raw):
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw):
        return check_password_hash(self.password_hash, raw)

    # --- subscription state ---------------------------------------------
    @property
    def current_subscription(self):
        """The subscription with the furthest expiry, active or not."""
        if not self.subscriptions:
            return None
        return self.subscriptions[0]

    @property
    def is_subscription_active(self):
        sub = self.current_subscription
        return bool(sub and sub.is_active)

    @property
    def days_to_expiry(self):
        sub = self.current_subscription
        if not sub:
            return None
        delta = _aware(sub.expires_at) - utcnow()
        return delta.days

    def __repr__(self):
        return f"<User {self.email}>"


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


class Profile(db.Model):
    """The contact card itself."""

    __tablename__ = "profiles"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True
    )

    slug = db.Column(db.String(64), unique=True, nullable=False, index=True)
    full_name = db.Column(db.String(120), nullable=False)
    job_title = db.Column(db.String(120))
    organisation = db.Column(db.String(160))
    phone = db.Column(db.String(32))
    whatsapp = db.Column(db.String(32))
    email = db.Column(db.String(255))
    website = db.Column(db.String(255))
    location = db.Column(db.String(160))
    bio = db.Column(db.Text)
    avatar_filename = db.Column(db.String(255))
    accent = db.Column(db.String(16), default="blue", nullable=False)

    is_published = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    user = db.relationship("User", back_populates="profile")
    social_links = db.relationship(
        "SocialLink",
        back_populates="profile",
        cascade="all, delete-orphan",
        order_by="SocialLink.position",
    )
    views = db.relationship("CardView", back_populates="profile", cascade="all, delete-orphan")

    @property
    def is_live(self):
        """A card is served publicly only when paid for and published."""
        return self.is_published and self.user.is_subscription_active and not self.user.is_suspended

    @property
    def initials(self):
        parts = [p for p in (self.full_name or "").split() if p]
        if not parts:
            return "?"
        if len(parts) == 1:
            return parts[0][:2].upper()
        return (parts[0][0] + parts[-1][0]).upper()

    @property
    def view_count(self):
        return len(self.views)

    def __repr__(self):
        return f"<Profile {self.slug}>"


class SocialLink(db.Model):
    __tablename__ = "social_links"

    # Keys map to the icon set in static/img and to vCard X-SOCIALPROFILE types
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

    id = db.Column(db.Integer, primary_key=True)
    profile_id = db.Column(
        db.Integer, db.ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False
    )
    platform = db.Column(db.String(32), nullable=False)
    url = db.Column(db.String(500), nullable=False)
    position = db.Column(db.Integer, default=0, nullable=False)

    profile = db.relationship("Profile", back_populates="social_links")

    @property
    def label(self):
        return self.PLATFORMS.get(self.platform, self.platform.title())


class Subscription(db.Model):
    __tablename__ = "subscriptions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    plan = db.Column(db.String(32), nullable=False)
    starts_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    cancelled_at = db.Column(db.DateTime)

    user = db.relationship("User", back_populates="subscriptions")
    payment = db.relationship("Payment", back_populates="subscription", uselist=False)

    @property
    def is_active(self):
        if self.cancelled_at:
            return False
        grace = timedelta(days=current_app.config["GRACE_PERIOD_DAYS"])
        return utcnow() <= _aware(self.expires_at) + grace

    @property
    def in_grace_period(self):
        now = utcnow()
        expiry = _aware(self.expires_at)
        grace = timedelta(days=current_app.config["GRACE_PERIOD_DAYS"])
        return expiry < now <= expiry + grace

    @property
    def status(self):
        if self.cancelled_at:
            return "cancelled"
        if self.in_grace_period:
            return "grace"
        return "active" if self.is_active else "expired"

    @classmethod
    def start_or_extend(cls, user, plan_key, months):
        """Renewal extends from the existing expiry, not from today.

        Paying three days early must not cost the subscriber three days.
        Paying after a lapse restarts from today, because those days are gone.
        """
        now = utcnow()
        existing = user.current_subscription
        if existing and _aware(existing.expires_at) > now and not existing.cancelled_at:
            base = _aware(existing.expires_at)
        else:
            base = now
        return cls(
            user=user,
            plan=plan_key,
            starts_at=now,
            expires_at=base + timedelta(days=30 * months),
        )


class Payment(db.Model):
    __tablename__ = "payments"

    STATUSES = ("pending", "success", "failed", "abandoned")
    CHANNELS = ("card", "mobile_money")

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    subscription_id = db.Column(db.Integer, db.ForeignKey("subscriptions.id"))

    reference = db.Column(db.String(64), unique=True, nullable=False, index=True)
    gateway = db.Column(db.String(32), default="paystack", nullable=False)
    plan = db.Column(db.String(32), nullable=False)
    amount_minor = db.Column(db.Integer, nullable=False)  # pesewas
    currency = db.Column(db.String(8), default="GHS", nullable=False)
    channel = db.Column(db.String(32), nullable=False)
    momo_provider = db.Column(db.String(16))  # mtn | atl | vod
    momo_phone = db.Column(db.String(32))
    status = db.Column(db.String(16), default="pending", nullable=False, index=True)
    gateway_fee_minor = db.Column(db.Integer, default=0, nullable=False)
    raw_response = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)
    paid_at = db.Column(db.DateTime)

    user = db.relationship("User", back_populates="payments")
    subscription = db.relationship("Subscription", back_populates="payment")

    @property
    def amount(self):
        """Major units, for display only. Never use for arithmetic."""
        return self.amount_minor / 100

    @property
    def net_minor(self):
        return self.amount_minor - (self.gateway_fee_minor or 0)


class CardView(db.Model):
    """One row per public card open. The raw material for analytics."""

    __tablename__ = "card_views"

    id = db.Column(db.Integer, primary_key=True)
    profile_id = db.Column(
        db.Integer, db.ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    viewed_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)
    source = db.Column(db.String(16), default="link", nullable=False)  # link | qr | nfc
    action = db.Column(db.String(16), default="view", nullable=False)  # view | vcf | tap
    visitor_hash = db.Column(db.String(64))  # salted, not reversible to an IP
    user_agent = db.Column(db.String(255))

    profile = db.relationship("Profile", back_populates="views")


class Notification(db.Model):
    """One row per reminder actually sent.

    This table exists for one reason: so a reminder is never sent twice. The
    unique constraint on (subscription_id, kind) is the guard. Without it, a
    cron job that runs twice — or a server that runs two workers — texts your
    customer the same warning twice, and you pay for both messages.
    """

    __tablename__ = "notifications"
    __table_args__ = (
        db.UniqueConstraint("subscription_id", "kind", name="uq_notification_once"),
    )

    # Reminder points, named by days remaining. 'expired' fires once, on the
    # day the grace period ends and the card actually goes dark.
    #
    # The last two are transactional rather than scheduled and are written
    # with subscription_id = NULL, which puts them outside the unique
    # constraint above. Leads and recovered payments can legitimately recur;
    # suppressing the second one as a "duplicate" would lose a real customer.
    KINDS = ("renewal_30", "renewal_7", "renewal_1", "expired", "lead", "recovered")

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    subscription_id = db.Column(db.Integer, db.ForeignKey("subscriptions.id", ondelete="CASCADE"))

    kind = db.Column(db.String(32), nullable=False)
    channel = db.Column(db.String(16), default="sms", nullable=False)
    destination = db.Column(db.String(64))
    body = db.Column(db.Text)
    status = db.Column(db.String(16), default="sent", nullable=False)  # sent | failed
    provider = db.Column(db.String(32))
    provider_ref = db.Column(db.String(128))
    error = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)

    user = db.relationship("User")

    def __repr__(self):
        return f"<Notification {self.kind} -> {self.destination} ({self.status})>"


class Lead(db.Model):
    """Contact details a visitor sent back to the card owner.

    The feature that makes this more than a digital business card. A paper
    card is one-directional: you hand it over and hope. Here the person
    holding your card can push their own details back to you in one step,
    while they are still standing in front of you.
    """

    __tablename__ = "leads"

    id = db.Column(db.Integer, primary_key=True)
    profile_id = db.Column(
        db.Integer, db.ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )

    name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(32))
    email = db.Column(db.String(255))
    organisation = db.Column(db.String(160))
    note = db.Column(db.Text)

    source = db.Column(db.String(16), default="link", nullable=False)
    visitor_hash = db.Column(db.String(64), index=True)
    is_read = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)

    profile = db.relationship("Profile", backref=db.backref("leads", cascade="all, delete-orphan"))

    def __repr__(self):
        return f"<Lead {self.name} -> {self.profile_id}>"
