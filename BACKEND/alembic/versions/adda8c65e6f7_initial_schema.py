"""initial_schema

Revision ID: adda8c65e6f7
Revises: 
Create Date: 2026-09-28 12:10:22.174815

"""
from typing import Sequence, Union

from alembic import op  # type: ignore
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'adda8c65e6f7'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Baseline schema matches AWS RDS PostgreSQL callinggen_staging
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
