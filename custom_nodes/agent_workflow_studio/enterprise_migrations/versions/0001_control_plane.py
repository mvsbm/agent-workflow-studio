import sqlalchemy as sa
from alembic import op

revision = "studio_0001"
down_revision = None
branch_labels = ("studio_enterprise",)
depends_on = None


def upgrade() -> None:
    op.create_table(
        "studio_projects",
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("project_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("head_revision", sa.Integer(), nullable=False),
        sa.CheckConstraint("head_revision >= 0", name="project_head_nonnegative"),
        sa.PrimaryKeyConstraint(
            "tenant_id", "project_id", name=op.f("pk_studio_projects")
        ),
    )
    op.create_table(
        "studio_revisions",
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("project_id", sa.String(length=128), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("parent_revision", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("semantic_digest", sa.String(length=64), nullable=False),
        sa.Column("content_digest", sa.String(length=64), nullable=False),
        sa.Column("schema_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("graph_json", sa.Text(), nullable=False),
        sa.Column("policy_json", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "(revision = 0 AND parent_revision IS NULL) OR (revision > 0 AND parent_revision IS NOT NULL AND parent_revision = revision - 1)",
            name="revision_parent_chain",
        ),
        sa.CheckConstraint("revision >= 0", name="revision_nonnegative"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "parent_revision"],
            [
                "studio_revisions.tenant_id",
                "studio_revisions.project_id",
                "studio_revisions.revision",
            ],
            name="fk_studio_revision_parent",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["studio_projects.tenant_id", "studio_projects.project_id"],
            name="fk_studio_revision_project",
        ),
        sa.PrimaryKeyConstraint(
            "tenant_id", "project_id", "revision", name=op.f("pk_studio_revisions")
        ),
    )
    op.create_table(
        "studio_revision_outbox",
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("event_id", sa.String(length=128), nullable=False),
        sa.Column("project_id", sa.String(length=128), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("topic", sa.String(length=64), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column(
            "state", sa.String(length=16), server_default="pending", nullable=False
        ),
        sa.Column("generation", sa.Integer(), server_default="0", nullable=False),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_token", sa.String(length=128), nullable=True),
        sa.Column("lease_until", sa.Float(), nullable=True),
        sa.CheckConstraint(
            "(state = 'leased' AND lease_owner IS NOT NULL AND lease_token IS NOT NULL AND lease_until IS NOT NULL) OR (state != 'leased' AND lease_owner IS NULL AND lease_token IS NULL AND lease_until IS NULL)",
            name="outbox_lease_shape",
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'leased', 'published')", name="outbox_state"
        ),
        sa.CheckConstraint(
            "topic = 'studio.revision.saved'", name="outbox_notification_only"
        ),
        sa.CheckConstraint("generation >= 0", name="outbox_generation"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "revision"],
            [
                "studio_revisions.tenant_id",
                "studio_revisions.project_id",
                "studio_revisions.revision",
            ],
            name=op.f("fk_studio_revision_outbox_tenant_id"),
        ),
        sa.PrimaryKeyConstraint(
            "tenant_id", "event_id", name=op.f("pk_studio_revision_outbox")
        ),
    )
    op.create_index(
        "ix_studio_outbox_claim",
        "studio_revision_outbox",
        ["tenant_id", "state", "lease_until", "event_id"],
        unique=False,
    )
    op.create_table(
        "studio_save_receipts",
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("project_id", sa.String(length=128), nullable=False),
        sa.Column("request_key", sa.String(length=128), nullable=False),
        sa.Column("operation", sa.String(length=16), nullable=False),
        sa.Column("input_digest", sa.String(length=64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.CheckConstraint("operation IN ('create', 'save')", name="receipt_operation"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "revision"],
            [
                "studio_revisions.tenant_id",
                "studio_revisions.project_id",
                "studio_revisions.revision",
            ],
            name=op.f("fk_studio_save_receipts_tenant_id"),
        ),
        sa.PrimaryKeyConstraint(
            "tenant_id",
            "project_id",
            "request_key",
            name=op.f("pk_studio_save_receipts"),
        ),
    )


def downgrade() -> None:
    op.drop_table("studio_save_receipts")
    op.drop_index("ix_studio_outbox_claim", table_name="studio_revision_outbox")
    op.drop_table("studio_revision_outbox")
    op.drop_table("studio_revisions")
    op.drop_table("studio_projects")
