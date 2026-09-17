"""Forms and validation.

Validation lives here rather than in views so that the same rules apply
whether a field arrives from the browser, a test or a future API client.
"""

import re

from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField
from wtforms import (
    BooleanField,
    EmailField,
    PasswordField,
    RadioField,
    SelectField,
    StringField,
    SubmitField,
    TelField,
    TextAreaField,
    URLField,
)
from wtforms.validators import (
    DataRequired,
    Email,
    EqualTo,
    Length,
    Optional,
    Regexp,
    ValidationError,
)

from .models import SocialLink

# Ghanaian mobile numbers: 0XXXXXXXXX locally or +233XXXXXXXXX internationally.
GH_PHONE = re.compile(r"^(?:\+233|0)\d{9}$")
SLUG = re.compile(r"^[a-z0-9]([a-z0-9-]{1,30})[a-z0-9]$")

RESERVED_SLUGS = {
    "admin", "api", "app", "auth", "billing", "card", "cards", "dashboard",
    "help", "login", "logout", "pricing", "privacy", "register", "settings",
    "static", "support", "terms", "u", "webhook",
}


class GhanaPhone:
    """Accept 024..., +23324... and the same with spaces or dashes."""

    def __init__(self, message=None):
        self.message = message or "Use a Ghanaian number, e.g. 0244123456 or +233244123456."

    def __call__(self, form, field):
        if not field.data:
            return
        cleaned = re.sub(r"[\s\-()]", "", field.data)
        if not GH_PHONE.match(cleaned):
            raise ValidationError(self.message)
        field.data = cleaned


class RegisterForm(FlaskForm):
    email = EmailField("Email address", validators=[DataRequired(), Email(), Length(max=255)])
    full_name = StringField("Full name", validators=[DataRequired(), Length(min=2, max=120)])
    password = PasswordField(
        "Password",
        validators=[DataRequired(), Length(min=8, message="Use at least 8 characters.")],
    )
    confirm = PasswordField(
        "Repeat password",
        validators=[DataRequired(), EqualTo("password", message="The passwords do not match.")],
    )
    accept_terms = BooleanField(
        "I accept the terms of service", validators=[DataRequired(message="Accept the terms to continue.")]
    )
    submit = SubmitField("Create account")


class LoginForm(FlaskForm):
    email = EmailField("Email address", validators=[DataRequired(), Email()])
    password = PasswordField("Password", validators=[DataRequired()])
    remember = BooleanField("Keep me signed in")
    submit = SubmitField("Sign in")


class ProfileForm(FlaskForm):
    full_name = StringField("Full name", validators=[DataRequired(), Length(max=120)])
    job_title = StringField("Job title", validators=[Optional(), Length(max=120)])
    organisation = StringField("Organisation", validators=[Optional(), Length(max=160)])
    slug = StringField(
        "Card link",
        validators=[
            DataRequired(),
            Length(min=3, max=32),
            Regexp(SLUG, message="Lower-case letters, numbers and hyphens only."),
        ],
    )
    phone = TelField("Phone number", validators=[Optional(), GhanaPhone()])
    whatsapp = TelField("WhatsApp number", validators=[Optional(), GhanaPhone()])
    email = EmailField("Contact email", validators=[Optional(), Email(), Length(max=255)])
    website = URLField("Website", validators=[Optional(), Length(max=255)])
    location = StringField("City or town", validators=[Optional(), Length(max=160)])
    bio = TextAreaField(
        "Short introduction",
        validators=[Optional(), Length(max=280, message="Keep this under 280 characters.")],
    )
    avatar = FileField(
        "Photo",
        validators=[FileAllowed(["png", "jpg", "jpeg", "webp"], "Images only: PNG, JPG or WebP.")],
    )
    accent = SelectField(
        "Card colour",
        choices=[
            ("blue", "Blue"),
            ("purple", "Purple"),
            ("amber", "Amber"),
            ("ink", "Charcoal"),
        ],
    )
    is_published = BooleanField("Card is visible to the public")
    submit = SubmitField("Save changes")

    def __init__(self, *args, original_slug=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.original_slug = original_slug

    def validate_slug(self, field):
        value = field.data.lower().strip()
        if value in RESERVED_SLUGS:
            raise ValidationError("That link is reserved. Try another.")
        from .models import Profile  # local import avoids a circular dependency

        if value != self.original_slug:
            if Profile.query.filter_by(slug=value).first():
                raise ValidationError("Someone already has that link.")
        field.data = value


class SocialLinkForm(FlaskForm):
    platform = SelectField(
        "Platform", choices=sorted(SocialLink.PLATFORMS.items(), key=lambda kv: kv[1])
    )
    url = URLField("Link", validators=[DataRequired(), Length(max=500)])
    submit = SubmitField("Add link")

    def validate_url(self, field):
        value = field.data.strip()
        if not value.startswith(("http://", "https://")):
            field.data = "https://" + value


class CheckoutForm(FlaskForm):
    plan = RadioField("Plan", validators=[DataRequired()])
    channel = RadioField(
        "How would you like to pay?",
        choices=[("mobile_money", "Mobile money"), ("card", "Card")],
        default="mobile_money",
        validators=[DataRequired()],
    )
    momo_provider = SelectField(
        "Network",
        choices=[("mtn", "MTN MoMo"), ("vod", "Telecel Cash"), ("atl", "AirtelTigo Money")],
        validators=[Optional()],
    )
    momo_phone = TelField("Mobile money number", validators=[Optional(), GhanaPhone()])
    submit = SubmitField("Pay now")

    def validate(self, extra_validators=None):
        if not super().validate(extra_validators=extra_validators):
            return False
        if self.channel.data == "mobile_money" and not self.momo_phone.data:
            self.momo_phone.errors.append("Enter the number to charge.")
            return False
        return True


class OtpForm(FlaskForm):
    otp = StringField("One-time code", validators=[DataRequired(), Length(min=4, max=10)])
    submit = SubmitField("Confirm")


class PasswordChangeForm(FlaskForm):
    current_password = PasswordField("Current password", validators=[DataRequired()])
    password = PasswordField("New password", validators=[DataRequired(), Length(min=8)])
    confirm = PasswordField(
        "Repeat new password", validators=[DataRequired(), EqualTo("password")]
    )
    submit = SubmitField("Update password")


class LeadForm(FlaskForm):
    """What a visitor sends back to the card owner.

    Only the name is required. Every extra required field costs completions,
    and this form is filled in standing up, on someone else's phone, during a
    conversation. One of phone or email is enforced in validate() because a
    lead with neither is not a lead.
    """

    name = StringField("Your name", validators=[DataRequired(), Length(min=2, max=120)])
    phone = TelField("Your phone number", validators=[Optional(), GhanaPhone()])
    email = EmailField("Your email", validators=[Optional(), Email(), Length(max=255)])
    organisation = StringField("Where you work", validators=[Optional(), Length(max=160)])
    note = TextAreaField(
        "Anything to add?", validators=[Optional(), Length(max=500)]
    )
    # Honeypot. Hidden from people by CSS, irresistible to naive bots. Any
    # submission that fills it is dropped without explanation — telling a bot
    # why it failed only helps it succeed next time.
    website = StringField("Leave this blank")
    submit = SubmitField("Send my details")

    def validate(self, extra_validators=None):
        if not super().validate(extra_validators=extra_validators):
            return False
        if not self.phone.data and not self.email.data:
            self.phone.errors.append("Add a phone number or an email so they can reply.")
            return False
        return True

    @property
    def is_bot(self):
        return bool(self.website.data)
