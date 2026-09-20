"""Add gallery images table.

Revision ID: 001_add_gallery_images
Revises:
Create Date: 2026-09-20
"""

from alembic import op
import sqlalchemy as sa


revision = "001_add_gallery_images"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "gallery_images",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "display_order",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "is_published",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade():
    op.drop_table("gallery_images")