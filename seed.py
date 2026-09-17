"""Seed the database with believable demo data.

Run once after setup so the admin charts have something to draw:

    python seed.py

Everything here is fictional. The names, numbers and organisations are
invented, and the phone numbers use the 000000 block so none of them reach a
real person.
"""

import random
from datetime import timedelta

from dotenv import load_dotenv

load_dotenv()

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import (  # noqa: E402
    CardView,
    Lead,
    Payment,
    Profile,
    SocialLink,
    Subscription,
    User,
    utcnow,
)

PEOPLE = [
    ("Ama Serwaa Boateng", "Procurement Lead", "Adinkra Logistics", "Accra", "professional"),
    ("Kwabena Osei Mensah", "Architect", "Osei + Partners", "Kumasi", "business"),
    ("Efua Nyarko", "Senior Midwife", "Cape Coast Teaching Hospital", "Cape Coast", "starter"),
    ("Yaw Darko Antwi", "Software Engineer", "Kola Labs", "Accra", "professional"),
    ("Abena Frimpong", "Fabric Buyer", "Nsuo Textiles", "Tema", "starter"),
    ("Kofi Agyeman", "Cocoa Extension Officer", "COCOBOD", "Sunyani", "starter"),
    ("Akosua Dapaah", "Tax Consultant", "Dapaah Advisory", "Accra", "business"),
    ("Kwesi Amoah", "Tour Operator", "Gold Coast Journeys", "Elmina", "professional"),
    ("Adwoa Asantewaa", "Lecturer", "University of Cape Coast", "Cape Coast", "professional"),
    ("Nii Armah Quaye", "Logistics Manager", "Tema Port Services", "Tema", "starter"),
    ("Mariama Iddrisu", "Agribusiness Officer", "Northern Seeds Ltd", "Tamale", "starter"),
    ("Selorm Dzikunu", "Graphic Designer", "Studio Kente", "Ho", "professional"),
]

PLATFORMS = ["linkedin", "instagram", "x", "facebook", "whatsapp", "website"]
ACCENTS = ["blue", "purple", "amber", "ink"]
PROVIDERS = ["mtn", "mtn", "mtn", "vod", "atl"]  # weighted: MTN dominates

LEAD_NAMES = [
    ("Kojo", "Amankwah"), ("Esi", "Bediako"), ("Nana", "Yeboah"),
    ("Fatima", "Alhassan"), ("Bright", "Amevor"), ("Naa", "Adjei"),
    ("Kwaku", "Tetteh"), ("Hawa", "Sulemana"), ("Elorm", "Agbeko"),
]
LEAD_ORGS = [
    "Ghana Revenue Authority", "Stanbic Bank", "KNUST",
    "Ministry of Education", "Melcom", "Ecobank Ghana", None,
]
LEAD_NOTES = [
    "Met at the trade fair, please send the catalogue.",
    "Following up on the quote we discussed.",
    "Interested in bulk pricing.",
    "Call me next week about the contract.",
]


def slugify(name):
    return "-".join(part.lower() for part in name.split()[:2] if part.isalpha())


def seed():
    app = create_app("development")

    with app.app_context():
        db.drop_all()
        db.create_all()

        admin = User(email="admin@example.com", is_admin=True)
        admin.set_password("admin1234")
        db.session.add(admin)

        now = utcnow()
        plans = app.config["PLANS"]

        for index, (name, title, org, city, plan_key) in enumerate(PEOPLE):
            joined = now - timedelta(days=random.randint(20, 400))

            user = User(
                email=f"{slugify(name).replace('-', '.')}@example.com",
                created_at=joined.replace(tzinfo=None),
                last_login_at=(now - timedelta(days=random.randint(0, 25))).replace(tzinfo=None),
            )
            user.set_password("password123")
            db.session.add(user)
            db.session.flush()

            profile = Profile(
                user=user,
                slug=slugify(name),
                full_name=name,
                job_title=title,
                organisation=org,
                location=city,
                phone=f"+2332{random.randint(40, 59)}000{index:03d}",
                whatsapp=f"+2332{random.randint(40, 59)}000{index:03d}",
                email=user.email,
                website=f"https://{slugify(name).replace('-', '')}.example.com",
                bio=f"{title} working across {city} and the surrounding region.",
                accent=random.choice(ACCENTS),
                created_at=joined.replace(tzinfo=None),
            )
            db.session.add(profile)
            db.session.flush()

            for position, platform in enumerate(random.sample(PLATFORMS, random.randint(2, 5))):
                db.session.add(
                    SocialLink(
                        profile=profile,
                        platform=platform,
                        url=f"https://{platform}.com/{slugify(name)}",
                        position=position,
                    )
                )

            # Roughly one in five has let the plan lapse. A demo where every
            # client is active hides exactly the state the admin screens exist
            # to surface.
            lapsed = index % 5 == 0
            starts = joined
            expires = starts - timedelta(days=20) if lapsed else starts + timedelta(days=365)

            subscription = Subscription(
                user=user,
                plan=plan_key,
                starts_at=starts.replace(tzinfo=None),
                expires_at=expires.replace(tzinfo=None),
            )
            db.session.add(subscription)
            db.session.flush()

            channel = "card" if index % 4 == 0 else "mobile_money"
            provider = None if channel == "card" else random.choice(PROVIDERS)
            amount = plans[plan_key]["amount_minor"]

            db.session.add(
                Payment(
                    user=user,
                    subscription_id=subscription.id,
                    reference=f"KC-SEED-{index:04d}",
                    plan=plan_key,
                    amount_minor=amount,
                    channel=channel,
                    momo_provider=provider,
                    momo_phone=profile.phone if provider else None,
                    status="success",
                    # Paystack's Ghana pricing at time of writing: 1.95% on
                    # local transactions. Replace with your negotiated rate.
                    gateway_fee_minor=int(amount * 0.0195),
                    created_at=starts.replace(tzinfo=None),
                    paid_at=starts.replace(tzinfo=None),
                )
            )

            # A few failures and an unapproved MoMo prompt, so the finance page
            # has something other than a perfect record to show.
            if index % 6 == 0:
                db.session.add(
                    Payment(
                        user=user,
                        reference=f"KC-FAIL-{index:04d}",
                        plan=plan_key,
                        amount_minor=amount,
                        channel="mobile_money",
                        momo_provider="mtn",
                        momo_phone=profile.phone,
                        status="failed",
                        created_at=(starts - timedelta(minutes=8)).replace(tzinfo=None),
                    )
                )
            if index == 3:
                db.session.add(
                    Payment(
                        user=user,
                        reference="KC-PENDING-0003",
                        plan=plan_key,
                        amount_minor=amount,
                        channel="mobile_money",
                        momo_provider="vod",
                        momo_phone=profile.phone,
                        status="pending",
                        created_at=(now - timedelta(hours=30)).replace(tzinfo=None),
                    )
                )

            # Card opens, weighted towards recent days and towards QR scans,
            # which is what the real distribution tends to look like.
            for _ in range(random.randint(8, 120)):
                days_ago = int(random.triangular(0, 88, 6))
                db.session.add(
                    CardView(
                        profile=profile,
                        viewed_at=(now - timedelta(days=days_ago, hours=random.randint(0, 23))).replace(
                            tzinfo=None
                        ),
                        source=random.choices(["qr", "link", "nfc"], weights=[5, 4, 1])[0],
                        action=random.choices(["view", "vcf"], weights=[7, 3])[0],
                        visitor_hash=f"seed{random.randint(1, 400):04d}",
                        user_agent="Mozilla/5.0 (seed)",
                    )
                )

            # A handful of inbound contacts, so the leads inbox is not empty
            # on a fresh install. Not everyone gets them — an inbox where
            # every card is converting hides what a quiet card looks like.
            if index % 3 == 0:
                for lead_index in range(random.randint(1, 6)):
                    first, last = random.choice(LEAD_NAMES)
                    db.session.add(
                        Lead(
                            profile=profile,
                            name=f"{first} {last}",
                            phone=f"+2332{random.randint(40, 59)}{random.randint(100000, 999999)}",
                            email=f"{first.lower()}.{last.lower()}@example.com",
                            organisation=random.choice(LEAD_ORGS),
                            note=random.choice(LEAD_NOTES) if lead_index % 2 else None,
                            source=random.choices(["qr", "link", "nfc"], weights=[6, 3, 1])[0],
                            visitor_hash=f"lead{random.randint(1, 999):04d}",
                            is_read=lead_index > 1,
                            created_at=(
                                now - timedelta(days=random.randint(0, 60))
                            ).replace(tzinfo=None),
                        )
                    )

        db.session.commit()

        print("Seeded.")
        print("  Admin:  admin@example.com / admin1234")
        print("  Client: ama.serwaa@example.com / password123")
        print(f"  {len(PEOPLE)} client accounts created.")
        print(f"  {Lead.query.count()} inbound contacts seeded.")


if __name__ == "__main__":
    seed()
