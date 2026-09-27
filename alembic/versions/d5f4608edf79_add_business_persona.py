"""add business persona

Revision ID: d5f4608edf79
Revises: d570b43fba10
Create Date: 2026-09-25 17:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'd5f4608edf79'
down_revision: Union[str, Sequence[str], None] = 'd570b43fba10'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'businesses',
        sa.Column('persona', sa.String(length=40), nullable=False,
                  server_default='consultiva'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('businesses', 'persona')
