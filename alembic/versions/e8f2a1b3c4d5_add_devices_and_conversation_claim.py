"""add devices table and conversation claim fields

Revision ID: e8f2a1b3c4d5
Revises: a7c3e9f1b5d4
Create Date: 2026-09-28 11:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e8f2a1b3c4d5'
down_revision: Union[str, Sequence[str], None] = 'a7c3e9f1b5d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create devices table
    op.create_table(
        'devices',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('business_id', sa.Integer(), nullable=False),
        sa.Column('device_id', sa.String(length=64), nullable=False),
        sa.Column('token_hash', sa.String(length=128), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('last_heartbeat', sa.DateTime(), nullable=True),
        sa.Column('last_activity', sa.DateTime(), nullable=True),
        sa.Column('created', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('revoked_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['business_id'], ['businesses.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('business_id', 'device_id', name='uq_business_device'),
    )
    op.create_index(op.f('ix_devices_business_id'), 'devices', ['business_id'])
    op.create_index(op.f('ix_devices_device_id'), 'devices', ['device_id'])
    op.create_index(op.f('ix_devices_token_hash'), 'devices', ['token_hash'], unique=True)
    op.create_index(op.f('ix_devices_status'), 'devices', ['status'])

    # Add claim fields to conversations
    op.add_column('conversations', sa.Column('assigned_to', sa.String(length=100), nullable=True))
    op.add_column('conversations', sa.Column('assigned_device_id', sa.Integer(), nullable=True))
    op.add_column('conversations', sa.Column('assigned_at', sa.DateTime(), nullable=True))
    op.add_column('conversations', sa.Column('lease_expires_at', sa.DateTime(), nullable=True))
    op.create_index(op.f('ix_conversations_assigned_to'), 'conversations', ['assigned_to'])
    op.create_index(op.f('ix_conversations_assigned_device_id'), 'conversations', ['assigned_device_id'])
    op.create_index(op.f('ix_conversations_lease_expires_at'), 'conversations', ['lease_expires_at'])
    # batch_alter_table: en PostgreSQL ejecuta el ALTER normal y en SQLite
    # usa copy-and-move (SQLite no soporta ALTER de constraints).
    with op.batch_alter_table('conversations') as batch_op:
        batch_op.create_foreign_key(
            'fk_conversations_assigned_device_id',
            'devices',
            ['assigned_device_id'], ['id'],
        )


def downgrade() -> None:
    with op.batch_alter_table('conversations') as batch_op:
        batch_op.drop_constraint('fk_conversations_assigned_device_id', type_='foreignkey')
    op.drop_index(op.f('ix_conversations_lease_expires_at'), table_name='conversations')
    op.drop_index(op.f('ix_conversations_assigned_device_id'), table_name='conversations')
    op.drop_index(op.f('ix_conversations_assigned_to'), table_name='conversations')
    op.drop_column('conversations', 'lease_expires_at')
    op.drop_column('conversations', 'assigned_at')
    op.drop_column('conversations', 'assigned_device_id')
    op.drop_column('conversations', 'assigned_to')

    op.drop_index(op.f('ix_devices_status'), table_name='devices')
    op.drop_index(op.f('ix_devices_token_hash'), table_name='devices')
    op.drop_index(op.f('ix_devices_device_id'), table_name='devices')
    op.drop_index(op.f('ix_devices_business_id'), table_name='devices')
    op.drop_table('devices')
