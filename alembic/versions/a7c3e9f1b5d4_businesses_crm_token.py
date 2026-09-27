"""businesses crm_token (H20: CRM aislado por tenant)

Revision ID: a7c3e9f1b5d4
Revises: c8a1f0d34b27
Create Date: 2026-09-26 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a7c3e9f1b5d4'
down_revision: Union[str, Sequence[str], None] = 'c8a1f0d34b27'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('businesses') as batch_op:
        batch_op.add_column(sa.Column('crm_token', sa.String(length=64), nullable=True))
        batch_op.create_index(
            batch_op.f('ix_businesses_crm_token'), ['crm_token'], unique=True
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('businesses') as batch_op:
        batch_op.drop_index(batch_op.f('ix_businesses_crm_token'))
        batch_op.drop_column('crm_token')
