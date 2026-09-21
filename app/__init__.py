import os
from datetime import datetime

from flask import Flask, jsonify, render_template, request
from flask_login import current_user

from .extensions import csrf, db, login_manager, migrate


def create_app(config_name=None):
    from config import CONFIGS

    config_name = config_name or os.environ.get(
        "FLASK_CONFIG",
        "development",
    )

    app = Flask(
        __name__,
        instance_relative_config=True,
    )

    app.config.from_object(CONFIGS[config_name])

    os.makedirs(
        app.instance_path,
        exist_ok=True,
    )

    os.makedirs(
        app.config["UPLOAD_FOLDER"],
        exist_ok=True,
    )

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)

    from .blueprints import (
        admin,
        auth,
        billing,
        cards,
        dashboard,
        main,
        nfc,
    )

    app.register_blueprint(main.bp)
    app.register_blueprint(auth.bp)
    app.register_blueprint(dashboard.bp)
    app.register_blueprint(cards.bp)
    app.register_blueprint(billing.bp)
    app.register_blueprint(nfc.bp)
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
        if request.path.startswith("/api/"):
            return jsonify({"error": "Not found"}), 404

        return render_template("errors/404.html"), 404

    @app.errorhandler(403)
    def forbidden(error):
        if request.path.startswith("/api/"):
            return jsonify({"error": "Forbidden"}), 403

        return render_template("errors/403.html"), 403

    @app.errorhandler(413)
    def request_too_large(error):
        if request.path.startswith("/api/"):
            return jsonify({"error": "File too large"}), 413

        return render_template("errors/413.html"), 413

    @app.errorhandler(500)
    def internal_server_error(error):
        if request.path.startswith("/api/"):
            return jsonify({"error": "Internal server error"}), 500

        return render_template("errors/500.html"), 500


def _register_filters(app):
    @app.template_filter("day")
    def format_day(value):
        if not value:
            return ""

        return f"{value.day} {value.strftime('%B %Y')}"

    @app.template_filter("month_name")
    def format_month_name(value):
        """Return the full month name from a date or month number."""

        if not value:
            return ""

        if isinstance(value, datetime):
            return value.strftime("%B")

        if hasattr(value, "strftime"):
            return value.strftime("%B")

        try:
            month_number = int(value)

            if 1 <= month_number <= 12:
                return datetime(
                    2000,
                    month_number,
                    1,
                ).strftime("%B")

        except (TypeError, ValueError):
            pass

        return str(value)

    @app.template_filter("money")
    def format_money(value):
        """Format a monetary amount as Ghana cedis."""

        if value is None:
            return "GHS 0.00"

        try:
            amount = float(value)
        except (TypeError, ValueError):
            return "GHS 0.00"

        return f"GHS {amount:,.2f}"


def _register_context(app):
    @app.context_processor
    def inject_context():
        return {
            "current_user": current_user,
        }


def _register_cli(app):
    @app.cli.command("healthcheck")
    def healthcheck():
        """Basic application health check."""

        print("CardHub application is running.")


def _register_headers(app):
    @app.after_request
    def add_security_headers(response):
        response.headers.setdefault(
            "X-Content-Type-Options",
            "nosniff",
        )

        response.headers.setdefault(
            "X-Frame-Options",
            "SAMEORIGIN",
        )

        response.headers.setdefault(
            "Referrer-Policy",
            "strict-origin-when-cross-origin",
        )

        return response
