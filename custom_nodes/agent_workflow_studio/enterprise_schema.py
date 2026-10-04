from sqlalchemy import (
    CheckConstraint,
    Column,
    Float,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
)

metadata = MetaData(
    naming_convention={
        "pk": "pk_%(table_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s",
        "ix": "ix_%(table_name)s_%(column_0_name)s",
    }
)

projects = Table(
    "studio_projects",
    metadata,
    Column("tenant_id", String(128), primary_key=True),
    Column("project_id", String(128), primary_key=True),
    Column("name", String(256), nullable=False),
    Column("created_by", String(128), nullable=False),
    Column("head_revision", Integer, nullable=False),
    CheckConstraint("head_revision >= 0", name="project_head_nonnegative"),
)

revisions = Table(
    "studio_revisions",
    metadata,
    Column("tenant_id", String(128), primary_key=True),
    Column("project_id", String(128), primary_key=True),
    Column("revision", Integer, primary_key=True),
    Column("parent_revision", Integer, nullable=True),
    Column("created_by", String(128), nullable=False),
    Column("semantic_digest", String(64), nullable=False),
    Column("content_digest", String(64), nullable=False),
    Column("schema_version", Integer, nullable=False, server_default="1"),
    Column("graph_json", Text, nullable=False),
    Column("policy_json", Text, nullable=False),
    ForeignKeyConstraint(
        ["tenant_id", "project_id"],
        ["studio_projects.tenant_id", "studio_projects.project_id"],
        name="fk_studio_revision_project",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "project_id", "parent_revision"],
        [
            "studio_revisions.tenant_id",
            "studio_revisions.project_id",
            "studio_revisions.revision",
        ],
        name="fk_studio_revision_parent",
    ),
    CheckConstraint("revision >= 0", name="revision_nonnegative"),
    CheckConstraint(
        "(revision = 0 AND parent_revision IS NULL) OR (revision > 0 AND parent_revision IS NOT NULL AND parent_revision = revision - 1)",
        name="revision_parent_chain",
    ),
)

receipts = Table(
    "studio_save_receipts",
    metadata,
    Column("tenant_id", String(128), primary_key=True),
    Column("project_id", String(128), primary_key=True),
    Column("request_key", String(128), primary_key=True),
    Column("operation", String(16), nullable=False),
    Column("input_digest", String(64), nullable=False),
    Column("revision", Integer, nullable=False),
    ForeignKeyConstraint(
        ["tenant_id", "project_id", "revision"],
        [
            "studio_revisions.tenant_id",
            "studio_revisions.project_id",
            "studio_revisions.revision",
        ],
    ),
    CheckConstraint("operation IN ('create', 'save')", name="receipt_operation"),
)

outbox = Table(
    "studio_revision_outbox",
    metadata,
    Column("tenant_id", String(128), primary_key=True),
    Column("event_id", String(128), primary_key=True),
    Column("project_id", String(128), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("topic", String(64), nullable=False),
    Column("payload_json", Text, nullable=False),
    Column("state", String(16), nullable=False, server_default="pending"),
    Column("generation", Integer, nullable=False, server_default="0"),
    Column("lease_owner", String(128)),
    Column("lease_token", String(128)),
    Column("lease_until", Float),
    ForeignKeyConstraint(
        ["tenant_id", "project_id", "revision"],
        [
            "studio_revisions.tenant_id",
            "studio_revisions.project_id",
            "studio_revisions.revision",
        ],
    ),
    CheckConstraint("topic = 'studio.revision.saved'", name="outbox_notification_only"),
    CheckConstraint("state IN ('pending', 'leased', 'published')", name="outbox_state"),
    CheckConstraint("generation >= 0", name="outbox_generation"),
    CheckConstraint(
        "(state = 'leased' AND lease_owner IS NOT NULL AND lease_token IS NOT NULL AND lease_until IS NOT NULL) OR (state != 'leased' AND lease_owner IS NULL AND lease_token IS NULL AND lease_until IS NULL)",
        name="outbox_lease_shape",
    ),
)
Index(
    "ix_studio_outbox_claim",
    outbox.c.tenant_id,
    outbox.c.state,
    outbox.c.lease_until,
    outbox.c.event_id,
)
