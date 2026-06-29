"""create sessions table

Revision ID: 001
Revises:
Create Date: 2026-06-29
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sessions",
        sa.Column("session_id", sa.Text(), primary_key=True),
        sa.Column("destination", sa.Text(), nullable=False),
        sa.Column("origin", sa.Text(), nullable=False),
        sa.Column("num_people", sa.Integer(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("state", postgresql.JSONB(), nullable=False),
        sa.Column("search_vector", postgresql.TSVECTOR(), nullable=True),
    )
    op.create_index("idx_sessions_destination", "sessions", ["destination"])
    op.create_index("idx_sessions_status", "sessions", ["status"])
    op.create_index("idx_sessions_created_at", "sessions", ["created_at"])
    op.create_index(
        "idx_sessions_search",
        "sessions",
        ["search_vector"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("idx_sessions_search", table_name="sessions")
    op.drop_index("idx_sessions_created_at", table_name="sessions")
    op.drop_index("idx_sessions_status", table_name="sessions")
    op.drop_index("idx_sessions_destination", table_name="sessions")
    op.drop_table("sessions")
