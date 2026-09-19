import os

from app import create_app
from app.extensions import db
from app.models import User


app = create_app(os.environ.get("FLASK_CONFIG", "production"))

with app.app_context():
    email = "admin@example.com"
    password = "admin1234"

    user = User.query.filter_by(email=email).first()

    if user is None:
        user = User(
            email=email,
            is_admin=True,
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        print("Admin account created.")
    else:
        user.is_admin = True
        user.set_password(password)
        db.session.commit()
        print("Admin account already existed. Password reset.")