"""appointments link to lead

Revision ID: c8a1f0d34b27
Revises: d5f4608edf79
Create Date: 2026-09-26 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c8a1f0d34b27'
down_revision: Union[str, Sequence[str], None] = 'd5f4608edf79'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'appointments',
        sa.Column('lead_id', sa.Integer(), nullable=True),
    )
    op.create_index('ix_appointments_lead_id', 'appointments', ['lead_id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_appointments_lead_id', table_name='appointments')
    op.drop_column('appointments', 'lead_id')
