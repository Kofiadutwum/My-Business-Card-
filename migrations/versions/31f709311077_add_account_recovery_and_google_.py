"""Add account recovery and Google authentication fields

Revision ID: 31f709311077
Revises: 29c13c9d7c6c
Create Date: 2026-09-21 15:22:05.919461

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "31f709311077"
down_revision = "29c13c9d7c6c"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "password_reset_otps",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("phone", sa.String(length=32), nullable=False),
        sa.Column("otp_hash", sa.String(length=255), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column(
            "attempts",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    with op.batch_alter_table(
        "password_reset_otps",
        schema=None,
    ) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_password_reset_otps_created_at"),
            ["created_at"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_password_reset_otps_expires_at"),
            ["expires_at"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_password_reset_otps_phone"),
            ["phone"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_password_reset_otps_user_id"),
            ["user_id"],
            unique=False,
        )

    with op.batch_alter_table(
        "users",
        schema=None,
    ) as batch_op:
        batch_op.add_column(
            sa.Column(
                "phone",
                sa.String(length=32),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column(
                "google_sub",
                sa.String(length=255),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column(
                "email_changed_at",
                sa.DateTime(),
                nullable=True,
            )
        )
        batch_op.create_index(
            batch_op.f("ix_users_google_sub"),
            ["google_sub"],
            unique=True,
        )
        batch_op.create_unique_constraint(
            "uq_users_phone",
            ["phone"],
        )


def downgrade():
    with op.batch_alter_table(
        "users",
        schema=None,
    ) as batch_op:
        batch_op.drop_constraint(
            "uq_users_phone",
            type_="unique",
        )
        batch_op.drop_index(
            batch_op.f("ix_users_google_sub")
        )
        batch_op.drop_column("email_changed_at")
        batch_op.drop_column("google_sub")
        batch_op.drop_column("phone")

    with op.batch_alter_table(
        "password_reset_otps",
        schema=None,
    ) as batch_op:
        batch_op.drop_index(
            batch_op.f(
                "ix_password_reset_otps_user_id"
            )
        )
        batch_op.drop_index(
            batch_op.f(
                "ix_password_reset_otps_phone"
            )
        )
        batch_op.drop_index(
            batch_op.f(
                "ix_password_reset_otps_expires_at"
            )
        )
        batch_op.drop_index(
            batch_op.f(
                "ix_password_reset_otps_created_at"
            )
        )

    op.drop_table("password_reset_otps")