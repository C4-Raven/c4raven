"""packages.tak_prereq: integer -> varchar(255)

tak_prereq holds the APK's plugin-api meta-data value (e.g. com.atakmap.app@5.5.1.CIV), which
product.inf needs as a string. The column was created as INTEGER, so storing a parsed value
failed on PostgreSQL.

Revision ID: 4e932cbe1fb6
Revises: 9c4e1d2b7a31
Create Date: 2026-11-17 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "4e932cbe1fb6"
down_revision = "9c4e1d2b7a31"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("packages", schema=None) as batch_op:
        batch_op.alter_column(
            "tak_prereq",
            existing_type=sa.Integer(),
            type_=sa.String(length=255),
            existing_nullable=True,
            postgresql_using="tak_prereq::text",
        )


def downgrade():
    # Only safe while no row holds a value: strings like com.atakmap.app@5.5.1.CIV can't be integers
    if not op.get_context().as_sql:
        count = op.get_bind().execute(sa.text("SELECT COUNT(*) FROM packages WHERE tak_prereq IS NOT NULL")).scalar()
        if count:
            raise RuntimeError(
                f"Cannot convert packages.tak_prereq back to INTEGER: {count} row(s) hold a value. "
                "Clear them (UPDATE packages SET tak_prereq = NULL) first."
            )
    with op.batch_alter_table("packages", schema=None) as batch_op:
        batch_op.alter_column(
            "tak_prereq",
            existing_type=sa.String(length=255),
            type_=sa.Integer(),
            existing_nullable=True,
            postgresql_using="tak_prereq::integer",
        )
