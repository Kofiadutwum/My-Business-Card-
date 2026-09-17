"""Application factory.

A factory rather than a module-level `app` object because it lets tests build
an isolated instance per test, and lets you run development and production
configurations from the same code.
"""

import os

import click
from flask import Flask, render_template

from .extensions import csrf, db, login_manager, migrate


def create_app(config_name=None):
    from config import CONFIGS

    config_name = config_name or os.environ.get("FLASK_CONFIG", "development")

    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(CONFIGS[config_name])

    os.makedirs(app.instance_path, exist_ok=True)
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)

    from .blueprints import admin, auth, billing, cards, dashboard, main

    app.register_blueprint(main.bp)
    app.register_blueprint(auth.bp)
    app.register_blueprint(dashboard.bp)
    app.register_blueprint(cards.bp)
    app.register_blueprint(billing.bp)
    app.register_blueprint(admin.bp)

    _register_errors(app)
    _register_filters(app)
    _register_context(app)
    _register_cli(app)
    _register_headers(app)

    return app


def _register_errors(app):
    @app.errorhandler(404)
    def not_found(error):
        return render_template("errors/404.html"), 404

    @app.errorhandler(410)
    def gone(error):
        return render_template("errors/410.html"), 410

    @app.errorhandler(413)
    def too_large(error):
        return render_template("errors/413.html"), 413

    @app.errorhandler(500)
    def server_error(error):
        db.session.rollback()
        return render_template("errors/500.html"), 500


def _register_filters(app):
    @app.template_filter("money")
    def money(minor_or_major, currency=None):
        currency = currency or app.config["CURRENCY"]
        value = float(minor_or_major)
        return f"{currency} {value:,.2f}"

    @app.template_filter("pesewas")
    def pesewas(minor):
        return f"{app.config['CURRENCY']} {minor / 100:,.2f}"

    @app.template_filter("day")
    def day(value):
        return value.strftime("%d %b %Y") if value else "—"

    @app.template_filter("month_name")
    def month_name(key):
        from datetime import datetime

        try:
            return datetime.strptime(key, "%Y-%m").strftime("%b %Y")
        except (ValueError, TypeError):
            return key


def _register_context(app):
    @app.context_processor
    def inject_globals():
        from datetime import datetime

        return {"now_year": datetime.now().year}


def _register_headers(app):
    @app.after_request
    def security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        return response


def _register_cli(app):
    @app.cli.command("init-db")
    def init_db():
        """Create all tables."""
        db.create_all()
        click.echo("Tables created.")

    @app.cli.command("create-admin")
    @click.option("--email", prompt=True)
    @click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True)
    def create_admin(email, password):
        """Promote or create an administrator account."""
        from .models import User

        user = User.query.filter_by(email=email.lower()).first()
        if user is None:
            user = User(email=email.lower(), is_admin=True)
            user.set_password(password)
            db.session.add(user)
        else:
            user.is_admin = True
            user.set_password(password)
        db.session.commit()
        click.echo(f"{email} is now an administrator.")

    @app.cli.command("expire-check")
    def expire_check():
        """List subscriptions past their expiry.

        Read-only. Expiry is computed from the date, so a card deactivates on
        time whether or not this ever runs. Use `send-reminders` to act on it.
        """
        from .models import Subscription

        for subscription in Subscription.query.all():
            if subscription.status in ("expired", "grace"):
                click.echo(
                    f"{subscription.user.email}: {subscription.status} "
                    f"(expired {subscription.expires_at:%d %b %Y})"
                )

    @app.cli.command("send-reminders")
    @click.option("--dry-run", is_flag=True, help="Show what would be sent, send nothing.")
    def send_reminders(dry_run):
        """Text subscribers whose cards are about to expire.

        Run nightly:
            0 8 * * *  cd /srv/cardhub && venv/bin/flask --app run.py send-reminders

        Safe to run repeatedly. Each reminder is recorded, and a recorded
        reminder is never sent again.
        """
        from .services.jobs import send_renewal_reminders

        summary = send_renewal_reminders(dry_run=dry_run)

        if dry_run:
            click.echo(f"Would send {len(summary['messages'])} message(s):\n")
            for email, kind, number, body in summary["messages"]:
                click.echo(f"  {email}  [{kind}]  -> {number}")
                click.echo(f"    {body}\n")
            return

        click.echo(
            f"Sent {summary['sent']}, failed {summary['failed']}, "
            f"already sent {summary['skipped']}, no number {summary['no_number']}."
        )

    @app.cli.command("reconcile")
    @click.option("--minutes", default=60, help="Only check payments older than this.")
    def reconcile(minutes):
        """Settle payments whose webhook never arrived.

        Run every quarter of an hour:
            */15 * * * *  cd /srv/cardhub && venv/bin/flask --app run.py reconcile

        Webhooks do get dropped — a restart mid-request, a nine-second deploy,
        a network blip. When that happens the customer has paid and their card
        is still dark. This asks the gateway directly and fixes it.
        """
        from .services.jobs import reconcile_payments

        summary = reconcile_payments(older_than_minutes=minutes)
        click.echo(
            f"Checked {summary['checked']}: recovered {summary['recovered']}, "
            f"failed {summary['failed']}, abandoned {summary['abandoned']}, "
            f"still open {summary['unresolved']}."
        )

    @app.cli.command("test-sms")
    @click.argument("number")
    def test_sms(number):
        """Send one message, to prove the provider and sender ID work.

        Do this before scheduling anything. An unregistered sender ID returns
        success and delivers nothing, which is the worst kind of failure.
        """
        from .services.sms import SmsError, send

        try:
            provider, reference, _ = send(
                number, f"Test message from {app.config['SITE_NAME']}. Setup is working."
            )
            click.echo(f"Handed to {provider}. Reference: {reference or 'none returned'}")
            click.echo("Now check the handset. 'Sent' is not 'delivered'.")
        except SmsError as exc:
            click.echo(f"Failed: {exc}")
