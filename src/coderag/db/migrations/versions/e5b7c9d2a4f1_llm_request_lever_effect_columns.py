"""llm request compression/cap/auto-cache effect columns

Revision ID: e5b7c9d2a4f1
Revises: d8f3a6c1e9b2
Create Date: 2026-09-03 15:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'e5b7c9d2a4f1'
down_revision: str | None = 'd8f3a6c1e9b2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('llm_requests', sa.Column(
        'compression_chars_saved', sa.Integer(), nullable=False,
        server_default='0'))
    op.add_column('llm_requests', sa.Column(
        'cap_applied', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('llm_requests', sa.Column(
        'auto_cache_applied', sa.Boolean(), nullable=False,
        server_default=sa.false()))
    for col in ('compression_chars_saved', 'cap_applied', 'auto_cache_applied'):
        op.alter_column('llm_requests', col, server_default=None)


def downgrade() -> None:
    op.drop_column('llm_requests', 'auto_cache_applied')
    op.drop_column('llm_requests', 'cap_applied')
    op.drop_column('llm_requests', 'compression_chars_saved')
