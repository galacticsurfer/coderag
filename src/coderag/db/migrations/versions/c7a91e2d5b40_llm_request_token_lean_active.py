"""llm request token lean active flag

Revision ID: c7a91e2d5b40
Revises: 40ced96cfa80
Create Date: 2026-09-03 10:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'c7a91e2d5b40'
down_revision: str | None = '40ced96cfa80'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('llm_requests', sa.Column(
        'token_lean_active', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.alter_column('llm_requests', 'token_lean_active', server_default=None)


def downgrade() -> None:
    op.drop_column('llm_requests', 'token_lean_active')
