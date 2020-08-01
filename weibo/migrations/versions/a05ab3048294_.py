"""empty message

Revision ID: a05ab3048294
Revises: 95e7d35598ce
Create Date: 2020-05-16 16:17:27.141162

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a05ab3048294'
down_revision = '95e7d35598ce'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_column('posts', 'language')
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column('posts', sa.Column('language', sa.VARCHAR(length=5), nullable=True))
    # ### end Alembic commands ###