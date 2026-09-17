"""Extension singletons, created here and bound to the app in the factory."""

from flask_login import LoginManager
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect

db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()
csrf = CSRFProtect()

login_manager.login_view = "auth.login"
login_manager.login_message = "Sign in to reach your dashboard."
login_manager.login_message_category = "info"
login_manager.session_protection = "strong"
