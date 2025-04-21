"""Add roles column to climber table and remove user table

Revision ID: 64115324ae94
Revises: 
Create Date: 2025-04-20 17:56:21.017141

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector

# revision identifiers, used by Alembic.
revision = '64115324ae94'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Use SQLAlchemy Inspector to check existing columns and tables
    conn = op.get_bind()
    inspector = Inspector.from_engine(conn)
    existing_columns = [col['name'] for col in inspector.get_columns('climber')]

    # Add roles column to climber table if it doesn't already exist
    if 'roles' not in existing_columns:
        with op.batch_alter_table('climber', schema=None) as batch_op:
            batch_op.add_column(sa.Column('roles', sa.String(length=100), nullable=False, server_default='climber'))  # Default to 'climber'

    # Drop the redundant role column if it exists
    if 'roles' in existing_columns:
        with op.batch_alter_table('climber', schema=None) as batch_op:
            batch_op.drop_column('roles')

    # Drop the user table if it exists
    if 'user' in inspector.get_table_names():
        op.drop_table('user')


def downgrade():
    # Recreate the role column in climber table
    with op.batch_alter_table('climber', schema=None) as batch_op:
        batch_op.add_column(sa.Column('roles', sa.String(length=20), nullable=False))

    # Remove roles column from climber table
    with op.batch_alter_table('climber', schema=None) as batch_op:
        batch_op.drop_column('roles')

    # Recreate the user table
    op.create_table(
        'user',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('username', sa.String(length=100), nullable=False, unique=True),
        sa.Column('password', sa.String(length=200), nullable=False),
        sa.Column('roles', sa.String(length=100), nullable=False, server_default='climber')
    )