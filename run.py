"""Development entry point: python run.py"""

import os

from dotenv import load_dotenv

load_dotenv()

from app import create_app  # noqa: E402  (must follow load_dotenv)
from app.extensions import db  # noqa: E402

app = create_app(os.environ.get("FLASK_CONFIG", "development"))


@app.shell_context_processor
def shell_context():
    from app.models import CardView, Payment, Profile, SocialLink, Subscription, User

    return {
        "db": db,
        "User": User,
        "Profile": Profile,
        "SocialLink": SocialLink,
        "Subscription": Subscription,
        "Payment": Payment,
        "CardView": CardView,
    }


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True, port=5000)
