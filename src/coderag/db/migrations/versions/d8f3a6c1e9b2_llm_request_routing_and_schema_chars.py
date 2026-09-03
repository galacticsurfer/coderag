"""llm request requested_model and tool_schema_chars

Revision ID: d8f3a6c1e9b2
Revises: c7a91e2d5b40
Create Date: 2026-09-03 12:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'd8f3a6c1e9b2'
down_revision: str | None = 'c7a91e2d5b40'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('llm_requests', sa.Column(
        'requested_model', sa.String(length=200), nullable=True))
    op.add_column('llm_requests', sa.Column(
        'tool_schema_chars', sa.Integer(), nullable=False, server_default='0'))
    op.alter_column('llm_requests', 'tool_schema_chars', server_default=None)


def downgrade() -> None:
    op.drop_column('llm_requests', 'tool_schema_chars')
    op.drop_column('llm_requests', 'requested_model')
