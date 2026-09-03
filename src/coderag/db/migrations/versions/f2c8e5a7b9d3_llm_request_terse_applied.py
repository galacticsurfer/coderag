"""llm request terse_applied flag

Revision ID: f2c8e5a7b9d3
Revises: e5b7c9d2a4f1
Create Date: 2026-09-03 18:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'f2c8e5a7b9d3'
down_revision: str | None = 'e5b7c9d2a4f1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('llm_requests', sa.Column(
        'terse_applied', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.alter_column('llm_requests', 'terse_applied', server_default=None)


def downgrade() -> None:
    op.drop_column('llm_requests', 'terse_applied')
