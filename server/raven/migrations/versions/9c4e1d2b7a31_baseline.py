"""Baseline: complete Raven schema

Replaces the former revision chain with a single revision that builds the whole
schema on an empty database. It keeps the id of the former head, so databases
that already reached that head are treated as up to date and never run this.

The DDL reproduces the migrated PostgreSQL schema exactly, including its
constraint names and quirks (e.g. no FK on device_profiles.eud_uid, the
standalone device_profiles_id_seq, mission_invitations.mission_name NOT NULL).
Any convergence with the ORM models belongs in later revisions.

Revision ID: 9c4e1d2b7a31
Revises:
Create Date: 2026-11-17 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.schema import CreateSequence, Sequence

# revision identifiers, used by Alembic.
revision = "9c4e1d2b7a31"
down_revision = None
branch_labels = None
depends_on = None


NOW = sa.text("(CURRENT_TIMESTAMP)")
TRUE = sa.text("true")
FALSE = sa.text("false")


def _str(name, length=255, nullable=True, **kw):
    return sa.Column(name, sa.String(length), nullable=nullable, **kw)


def _int(name, nullable=True, **kw):
    return sa.Column(name, sa.Integer(), nullable=nullable, **kw)


def _big(name, nullable=True):
    return sa.Column(name, sa.BigInteger(), nullable=nullable)


def _bool(name, nullable=True, **kw):
    return sa.Column(name, sa.Boolean(), nullable=nullable, **kw)


def _float(name, nullable=True):
    return sa.Column(name, sa.Float(), nullable=nullable)


def _ts(name, nullable=True, **kw):
    return sa.Column(name, sa.DateTime(), nullable=nullable, **kw)


def _text(name, nullable=True):
    return sa.Column(name, sa.Text(), nullable=nullable)


def _json(name, nullable=True):
    return sa.Column(name, sa.JSON(), nullable=nullable)


def _blob(name, nullable=True, length=None):
    return sa.Column(name, sa.LargeBinary(length), nullable=nullable)


def _id():
    """Single-column integer primary key column (SERIAL on PostgreSQL)."""
    return sa.Column("id", sa.Integer(), nullable=False)


def _pk(table, *cols):
    return sa.PrimaryKeyConstraint(*(cols or ("id",)), name=f"{table}_pkey")


def _fk(name, col, ref, ondelete=None):
    cols = [col] if isinstance(col, str) else list(col)
    refs = [ref] if isinstance(ref, str) else list(ref)
    return sa.ForeignKeyConstraint(cols, refs, name=name, ondelete=ondelete)


def _uq(name, *cols):
    return sa.UniqueConstraint(*cols, name=name)


def _index(name, table, *cols, unique=False):
    op.create_index(name, table, list(cols), unique=unique)


def upgrade():
    # Read the dialect from the migration context so offline (--sql) mode works too.
    is_pg = op.get_context().dialect.name == "postgresql"

    # --- users, roles, teams -------------------------------------------------
    op.create_table(
        "chatrooms",
        _str("id", nullable=False),
        _str("name", nullable=False),
        _str("group_owner"),
        _str("parent"),
        _pk("chatrooms"),
    )

    op.create_table(
        "icons",
        _id(),
        _str("iconset_uid"),
        _str("filename"),
        _str("groupName"),
        _str("type2525b"),
        _int("useCnt"),
        _blob("bitmap"),
        _blob("shadow"),
        _pk("icons"),
    )

    op.create_table(
        "role",
        _id(),
        _str("name", 80, nullable=False),
        _str("description"),
        _text("permissions"),
        _ts("update_datetime", nullable=False, server_default=NOW),
        _pk("role"),
        _uq("role_name_key", "name"),
    )

    op.create_table(
        "user",
        _str("email"),
        _str("fs_webauthn_user_handle", 64),
        _text("mf_recovery_codes"),
        _str("password"),
        _str("us_phone_number", 128),
        _str("username"),
        _text("us_totp_secrets"),
        _id(),
        _bool("active", nullable=False),
        _str("fs_uniquifier", 64, nullable=False),
        _ts("confirmed_at"),
        _ts("last_login_at"),
        _ts("current_login_at"),
        _str("last_login_ip", 64),
        _str("current_login_ip", 64),
        _int("login_count"),
        _str("tf_primary_method", 64),
        _str("tf_totp_secret"),
        _str("tf_phone_number", 128),
        _ts("create_datetime", nullable=False, server_default=NOW),
        _ts("update_datetime", nullable=False, server_default=NOW),
        _bool("site_access", nullable=False, server_default=TRUE),
        _bool("force_password_change", nullable=False, server_default=FALSE),
        _int("cert_validity_days"),
        _text("admin_view_areas"),
        _pk("user"),
        _uq("user_fs_uniquifier_key", "fs_uniquifier"),
        _uq("user_fs_webauthn_user_handle_key", "fs_webauthn_user_handle"),
        _uq("user_us_phone_number_key", "us_phone_number"),
        _uq("user_username_key", "username"),
    )

    op.create_table(
        "roles_users",
        _int("user_id"),
        _int("role_id"),
        _fk("role", "role_id", "role.id"),
        _fk("user", "user_id", "user.id"),
    )

    op.create_table(
        "teams",
        _id(),
        _str("name", nullable=False),
        _str("chatroom_id"),
        _pk("teams"),
        _uq("teams_name_key", "name"),
        _fk("team_chatroom", "chatroom_id", "chatrooms.id"),
    )

    op.create_table(
        "web_authn",
        _id(),
        _blob("credential_id", nullable=False, length=1024),
        _blob("public_key", nullable=False),
        _int("sign_count"),
        _text("transports"),
        _bool("backup_state", nullable=False),
        _str("device_type", 64, nullable=False),
        _str("extensions"),
        _ts("create_datetime", nullable=False, server_default=NOW),
        _ts("lastuse_datetime", nullable=False),
        _str("name", 64, nullable=False),
        _str("usage", 64, nullable=False),
        _int("user_id", nullable=False),
        _pk("web_authn"),
        _fk("web_authn_user", "user_id", "user.id", ondelete="CASCADE"),
    )
    _index("ix_web_authn_credential_id", "web_authn", "credential_id", unique=True)

    # --- devices ---------------------------------------------------------------
    op.create_table(
        "euds",
        _id(),
        _str("uid", nullable=False),
        _str("callsign"),
        _str("device"),
        _str("os"),
        _str("platform"),
        _str("version"),
        _big("phone_number"),
        _ts("last_event_time"),
        _str("last_status"),
        _int("user_id"),
        _int("team_id"),
        _str("team_role"),
        _big("meshtastic_id"),
        _str("meshtastic_macaddr"),
        _ts("last_meshtastic_publish"),
        _str("federated_from"),
        _ts("last_rx_at"),
        _pk("euds"),
        _uq("euds_uid_key", "uid"),
        _uq("euds_callsign_key", "callsign"),
        _fk("eud_team", "team_id", "teams.id"),
        _fk("eud_user", "user_id", "user.id"),
    )

    op.create_table(
        "chatrooms_uids",
        _str("chatroom_id", nullable=False),
        _str("uid", nullable=False),
        _pk("chatrooms_uids", "chatroom_id", "uid"),
        _fk("chatroom_uids", "chatroom_id", "chatrooms.id"),
        _fk("chatroom_eud", "uid", "euds.uid", ondelete="CASCADE"),
    )

    # missions is needed before cot (cot.mission_name references it)
    op.create_table(
        "missions",
        _str("name", nullable=False),
        _str("description"),
        _str("chat_room"),
        _str("base_layer"),
        _str("bbox"),
        _str("path"),
        _str("classification"),
        _str("tool"),
        _str("default_role"),
        _json("keywords"),
        _str("creator_uid"),
        _ts("create_time"),
        _json("external_data"),
        _json("feeds"),
        _json("map_layers"),
        _bool("invite_only"),
        _int("expiration", nullable=False),
        _str("guid"),
        _bool("password_protected"),
        _str("password"),
        _str("group"),
        _pk("missions", "name"),
        _fk("mission_creator", "creator_uid", "euds.uid", ondelete="CASCADE"),
    )

    op.create_table(
        "cot",
        _id(),
        _str("how"),
        _str("type"),
        _str("sender_callsign"),
        _str("sender_device_name"),
        _str("sender_uid"),
        _json("recipients"),
        _ts("timestamp", nullable=False),
        _ts("start", nullable=False),
        _ts("stale", nullable=False),
        _text("xml", nullable=False),
        _str("mission_name"),
        _str("uid"),
        _pk("cot"),
        _fk("cot_eud", "sender_uid", "euds.uid", ondelete="CASCADE"),
        _fk("mission_name", "mission_name", "missions.name"),
    )
    _index("ix_cot_timestamp", "cot", "timestamp")
    _index("ix_cot_type", "cot", "type")
    _index("ix_cot_uid", "cot", "uid")
    _index("ix_cot_sender_uid", "cot", "sender_uid")

    op.create_table(
        "data_packages",
        _id(),
        _str("filename", nullable=False),
        _str("hash", nullable=False),
        _str("creator_uid"),
        _ts("submission_time", nullable=False),
        _int("submission_user"),
        _str("keywords"),
        _str("mime_type", nullable=False),
        _int("size", nullable=False),
        _str("tool"),
        _str("expiration"),
        _bool("install_on_enrollment"),
        _bool("install_on_connection"),
        _pk("data_packages"),
        _uq("data_packages_hash_key", "hash"),
        _fk("data_packages_eud", "creator_uid", "euds.uid", ondelete="CASCADE"),
        _fk("data_packages_user", "submission_user", "user.id"),
    )

    op.create_table(
        "certificates",
        _id(),
        _str("common_name", nullable=False),
        _str("eud_uid"),
        _int("data_package_id"),
        _str("callsign"),
        _str("username"),
        _ts("expiration_date", nullable=False),
        _str("server_address", nullable=False),
        _int("server_port", nullable=False),
        _str("truststore_filename", nullable=False),
        _str("user_cert_filename", nullable=False),
        _str("csr"),
        _str("cert_password"),
        _str("serial_number", 64),
        _ts("revoked_at"),
        _str("revocation_reason", 64),
        _pk("certificates"),
        _fk("certificate_data_package", "data_package_id", "data_packages.id"),
        _fk("certificate_eud", "eud_uid", "euds.uid", ondelete="CASCADE"),
        _fk("certificate_user", "username", "user.username"),
    )
    _index("ix_certificates_serial_number", "certificates", "serial_number")

    op.create_table(
        "points",
        _id(),
        _str("uid", nullable=False),
        _str("device_uid"),
        _float("latitude"),
        _float("longitude"),
        _float("ce"),
        _float("hae"),
        _float("le"),
        _float("course"),
        _float("speed"),
        _str("location_source"),
        _float("battery"),
        _ts("timestamp", nullable=False),
        _float("azimuth"),
        _float("fov"),
        _int("cot_id"),
        _pk("points"),
        _fk("point_cot", "cot_id", "cot.id", ondelete="CASCADE"),
        _fk("point_eud", "device_uid", "euds.uid", ondelete="CASCADE"),
    )
    _index("ix_points_cot_id", "points", "cot_id")
    _index("ix_points_latitude_longitude", "points", "latitude", "longitude")
    _index("ix_points_timestamp", "points", "timestamp")
    _index("ix_points_device_uid_timestamp", "points", "device_uid", "timestamp")

    op.create_table(
        "video_streams",
        _str("path", nullable=False),
        _str("protocol", nullable=False),
        _int("port", nullable=False),
        _int("network_timeout", nullable=False),
        _str("uid"),
        _int("buffer_time"),
        _int("rover_port"),
        _int("rtsp_reliable"),
        _bool("ignore_embedded_klv"),
        _str("alias"),
        _str("preferred_mac_address"),
        _str("preferred_interface_address"),
        _str("username"),
        _text("xml"),
        _bool("ready", nullable=False),
        _text("mediamtx_settings", nullable=False),
        _int("cot_id"),
        _str("web_path"),
        _pk("video_streams", "path"),
        _fk("video_stream_cot", "cot_id", "cot.id"),
        _fk("video_stream_user", "username", "user.username"),
    )

    op.create_table(
        "alerts",
        _id(),
        _str("uid", nullable=False),
        _str("sender_uid", nullable=False),
        _ts("start_time", nullable=False),
        _ts("cancel_time"),
        _str("alert_type", nullable=False),
        _int("point_id"),
        _int("cot_id"),
        _pk("alerts"),
        _fk("alert_point", "point_id", "points.id", ondelete="CASCADE"),
        _fk("alert_eud", "sender_uid", "euds.uid", ondelete="CASCADE"),
        _fk("alert_cot", "cot_id", "cot.id", ondelete="CASCADE"),
    )
    _index("ix_alerts_start_time", "alerts", "start_time")
    _index("ix_alerts_point_id", "alerts", "point_id")

    op.create_table(
        "casevac",
        _id(),
        _str("sender_uid", nullable=False),
        _str("uid", nullable=False),
        _ts("timestamp", nullable=False),
        _int("ambulatory"),
        _bool("casevac"),
        _int("child"),
        _str("enemy"),
        _int("epw"),
        _str("equipment_detail"),
        _bool("equipment_none"),
        _bool("equipment_other"),
        _bool("extraction_equipment"),
        _float("freq"),
        _str("friendlies"),
        _int("hlz_marking"),
        _str("hlz_remarks"),
        _bool("hoist"),
        _int("litter"),
        _str("marked_by"),
        _str("medline_remarks"),
        _int("nonus_civilian"),
        _int("nonus_military"),
        _str("obstacles"),
        _int("priority"),
        _int("routine"),
        _int("security"),
        _bool("terrain_loose"),
        _bool("terrain_other"),
        _str("terrain_other_detail"),
        _str("terrain_detail"),
        _bool("terrain_none"),
        _bool("terrain_rough"),
        _bool("terrain_slope"),
        _str("terrain_slope_dir"),
        _str("title", nullable=False),
        _int("urgent"),
        _int("us_civilian"),
        _int("us_military"),
        _bool("ventilator"),
        _str("winds_are_from"),
        _int("zone_prot_selection"),
        _int("point_id"),
        _int("cot_id"),
        _int("convenience"),
        _str("urgent_surgical"),
        _pk("casevac"),
        _uq("casevac_uid_key", "uid"),
        _fk("casevac_cot", "cot_id", "cot.id", ondelete="CASCADE"),
        _fk("casevac_eud", "sender_uid", "euds.uid", ondelete="CASCADE"),
        _fk("casevac_point", "point_id", "points.id", ondelete="CASCADE"),
    )
    _index("ix_casevac_point_id", "casevac", "point_id")

    op.create_table(
        "geochat",
        _str("uid", nullable=False),
        _str("chatroom_id", nullable=False),
        _str("sender_uid", nullable=False),
        _str("remarks", nullable=False),
        _ts("timestamp", nullable=False),
        _int("point_id", nullable=False),
        _int("cot_id", nullable=False),
        _pk("geochat", "uid"),
        _fk("geochat_chatroom", "chatroom_id", "chatrooms.id"),
        _fk("geochat_cot", "cot_id", "cot.id"),
        _fk("geochat_point", "point_id", "points.id"),
        _fk("geochat_eud", "sender_uid", "euds.uid", ondelete="CASCADE"),
    )
    _index("ix_geochat_cot_id", "geochat", "cot_id")
    _index("ix_geochat_timestamp", "geochat", "timestamp")
    _index("ix_geochat_point_id", "geochat", "point_id")

    op.create_table(
        "markers",
        _id(),
        _str("uid", nullable=False),
        _str("affiliation"),
        _str("battle_dimension"),
        _int("point_id", nullable=False),
        _str("callsign"),
        _bool("readiness"),
        _int("argb"),
        _str("color_hex"),
        _str("iconset_path"),
        _str("parent_callsign"),
        _str("production_time"),
        _str("relation"),
        _str("relation_type"),
        _str("location_source"),
        _int("icon_id"),
        _str("parent_uid"),
        _str("remarks"),
        _int("cot_id"),
        _str("mil_std_2525c"),
        _pk("markers"),
        _uq("markers_uid_key", "uid"),
        _fk("marker_icon", "icon_id", "icons.id"),
        _fk("markers_cot", "cot_id", "cot.id", ondelete="CASCADE"),
        _fk("markers_eud", "parent_uid", "euds.uid", ondelete="CASCADE"),
        _fk("markers_point", "point_id", "points.id", ondelete="CASCADE"),
    )
    _index("ix_markers_cot_id", "markers", "cot_id")
    _index("ix_markers_point_id", "markers", "point_id")

    op.create_table(
        "rb_lines",
        _id(),
        _str("sender_uid", nullable=False),
        _str("uid", nullable=False),
        _ts("timestamp", nullable=False),
        _float("range", nullable=False),
        _float("bearing", nullable=False),
        _float("inclination"),
        _str("anchor_uid"),
        _int("range_units"),
        _int("bearing_units"),
        _int("north_ref"),
        _int("color"),
        _str("color_hex"),
        _str("callsign"),
        _int("stroke_color"),
        _float("stroke_weight"),
        _str("stroke_style"),
        _bool("labels_on"),
        _int("point_id"),
        _int("cot_id"),
        _float("end_latitude"),
        _float("end_longitude"),
        _pk("rb_lines"),
        _uq("rb_lines_uid_key", "uid"),
        _fk("rb_line_cot", "cot_id", "cot.id", ondelete="CASCADE"),
        _fk("rb_line_eud", "sender_uid", "euds.uid", ondelete="CASCADE"),
        _fk("rb_line_point", "point_id", "points.id", ondelete="CASCADE"),
    )
    _index("ix_rb_lines_point_id", "rb_lines", "point_id")

    op.create_table(
        "video_recordings",
        _id(),
        _str("segment_path", nullable=False),
        _str("path", nullable=False),
        _bool("in_progress", nullable=False),
        _ts("start_time"),
        _ts("stop_time"),
        _int("duration"),
        _int("width"),
        _int("height"),
        _str("video_codec"),
        _int("video_bitrate"),
        _str("audio_codec"),
        _int("audio_bitrate"),
        _int("audio_samplerate"),
        _int("audio_channels"),
        _int("file_size"),
        _pk("video_recordings"),
        _uq("video_recordings_segment_path_key", "segment_path"),
        _fk("video_recording_video_stream", "path", "video_streams.path"),
    )

    op.create_table(
        "zmist",
        _id(),
        _str("i"),
        _str("m"),
        _str("s"),
        _str("t"),
        _str("title"),
        _int("z"),
        _str("casevac_uid", nullable=False),
        _pk("zmist"),
        _fk("zmist_casevac", "casevac_uid", "casevac.uid", ondelete="CASCADE"),
    )

    op.create_table(
        "iconsets",
        _id(),
        _str("name"),
        _str("uid"),
        _int("version"),
        _str("defaultFriendly"),
        _str("defaultHostile"),
        _str("defaultNeutral"),
        _str("defaultUnknown"),
        _str("selectedGroup"),
        _pk("iconsets"),
    )

    op.create_table(
        "meshtastic_channels",
        _id(),
        _str("psk"),
        _str("name"),
        _bool("uplink_enabled", nullable=False),
        _bool("downlink_enabled", nullable=False),
        _int("position_precision", nullable=False),
        _int("lora_region", nullable=False),
        _int("lora_hop_limit", nullable=False),
        _bool("lora_tx_enabled", nullable=False),
        _int("lora_tx_power", nullable=False),
        _bool("lora_sx126x_rx_boosted_gain", nullable=False),
        _int("modem_preset", nullable=False),
        _str("url", nullable=False),
        _pk("meshtastic_channels"),
        _uq("meshtastic_channels_url_key", "url"),
    )

    # Legacy device_profiles: on PostgreSQL its id draws from a standalone
    # (not OWNED BY) sequence; elsewhere a plain autoincrement key is used.
    if is_pg:
        op.execute(CreateSequence(Sequence("device_profiles_id_seq")))
        dp_id = sa.Column(
            "id",
            sa.Integer(),
            nullable=False,
            autoincrement=False,
            server_default=sa.text("nextval('device_profiles_id_seq'::regclass)"),
        )
    else:
        dp_id = sa.Column("id", sa.Integer(), nullable=False, autoincrement=True)
    op.create_table(
        "device_profiles",
        _str("preference_key", nullable=False),
        _str("preference_value"),
        _str("value_class"),
        _bool("enrollment"),
        _bool("connection"),
        _str("tool"),
        _bool("active", nullable=False),
        _ts("publish_time", nullable=False, server_default=NOW),
        _str("eud_uid"),
        dp_id,
        sa.PrimaryKeyConstraint("id", name="primary_key"),
    )
    _index("preference_key_eud_uid", "device_profiles", "preference_key", "eud_uid", unique=True)

    op.create_table(
        "packages",
        _id(),
        _str("platform", nullable=False),
        _str("plugin_type", nullable=False),
        _str("package_name", nullable=False),
        _str("name", nullable=False),
        _str("file_name", nullable=False),
        _str("version", nullable=False),
        _int("revision_code"),
        _str("description"),
        _str("apk_hash"),
        _int("os_requirement"),
        _int("tak_prereq"),
        _int("file_size", nullable=False),
        _blob("icon"),
        _str("icon_filename"),
        _bool("install_on_enrollment"),
        _bool("install_on_connection"),
        _ts("publish_time", nullable=False, server_default=NOW),
        _str("atak_version"),
        _pk("packages"),
    )

    # --- missions --------------------------------------------------------------
    op.create_table(
        "mission_content",
        _id(),
        _json("keywords"),
        _str("mime_type"),
        _ts("submission_time"),
        _str("submitter"),
        _str("uid", nullable=False),
        _str("creator_uid"),
        _str("hash", nullable=False),
        _int("size"),
        _int("expiration"),
        _str("filename"),
        _pk("mission_content"),
        _uq("mission_content_uid_key", "uid"),
        _uq("hash_index", "hash"),
    )

    op.create_table(
        "mission_uids",
        _str("uid", nullable=False),
        _str("mission_name"),
        _ts("timestamp"),
        _str("creator_uid"),
        _str("cot_type"),
        _str("callsign"),
        _str("iconset_path"),
        _str("color"),
        _float("latitude"),
        _float("longitude"),
        _pk("mission_uids", "uid"),
        _fk("mission_uids_mission_name_fkey", "mission_name", "missions.name"),
    )

    op.create_table(
        "mission_changes",
        _id(),
        _bool("isFederatedChange", nullable=False),
        _str("change_type", nullable=False),
        _str("mission_name", nullable=False),
        _ts("timestamp", nullable=False),
        _str("creator_uid", nullable=False),
        _ts("server_time", nullable=False),
        _str("content_uid"),
        _str("mission_uid"),
        _json("external_data"),
        _json("mission_feed"),
        _json("mission_layer"),
        _pk("mission_changes"),
        _fk("mission_changes", "mission_name", "missions.name"),
        _fk("content_resource", "content_uid", "mission_content.uid"),
        _fk("mission_change_uid", "mission_uid", "mission_uids.uid", ondelete="CASCADE"),
    )

    op.create_table(
        "mission_content_mission",
        _id(),
        _int("mission_content_id", nullable=False),
        _str("mission_name", nullable=False),
        _pk("mission_content_mission"),
        _fk("mission_content", "mission_content_id", "mission_content.id"),
        _fk("mission", "mission_name", "missions.name"),
    )

    op.create_table(
        "mission_invitations",
        _id(),
        _str("mission_name", nullable=False),
        _str("client_uid"),
        _str("callsign"),
        _str("username"),
        _str("group_name"),
        _str("team_name"),
        _str("creator_uid"),
        _str("role"),
        _str("type"),
        _str("mission_guid"),
        _pk("mission_invitations"),
        _fk("mission_invitations", "mission_name", "missions.name"),
        _fk("mission_invitation_user", "username", "user.username"),
        _fk("mission_invitation_team", "team_name", "teams.name"),
        _fk("mission_invitation_callsign", "callsign", "euds.callsign", ondelete="CASCADE"),
        _fk("euds", "client_uid", "euds.uid"),
    )

    op.create_table(
        "mission_roles",
        _id(),
        _str("clientUid", nullable=False),
        _str("username", nullable=False),
        _ts("createTime", nullable=False),
        _str("role_type", nullable=False),
        _str("mission_name"),
        _str("mission_guid"),
        _pk("mission_roles"),
        _fk("mission_role_names", "mission_name", "missions.name"),
    )

    op.create_table(
        "mission_logs",
        _id(),
        _str("content", nullable=False),
        _str("creator_uid", nullable=False),
        _str("entry_uid", nullable=False),
        _str("mission_name", nullable=False),
        _ts("server_time", nullable=False),
        _ts("dtg", nullable=False),
        _ts("created", nullable=False),
        _str("content_hash"),
        _json("keywords", nullable=False),
        _json("content_hashes"),
        _pk("mission_logs"),
        _fk("mission_logs", "mission_name", "missions.name"),
    )

    # --- groups ----------------------------------------------------------------
    op.create_table(
        "groups",
        _id(),
        _ts("created", nullable=False),
        _int("bitpos", nullable=False),
        _str("name", nullable=False),
        _str("distinguishedName"),
        _str("type", nullable=False),
        _str("description"),
        _str("default_access", 8, server_default="both"),
        _bool("auto_join", nullable=False, server_default=FALSE),
        _int("default_membership_days"),
        _bool("locked", nullable=False, server_default=FALSE),
        _pk("groups"),
        _uq("groups_name_key", "name"),
    )

    op.create_table(
        "groups_users",
        _int("user_id", nullable=False),
        _int("group_id", nullable=False),
        _str("direction", nullable=False),
        _bool("enabled", nullable=False),
        _ts("expires_at"),
        _pk("groups_users", "user_id", "group_id", "direction"),
        _fk("groups_users_group_id_fkey", "group_id", "groups.id"),
        _fk("groups_users_user_id_fkey", "user_id", "user.id"),
    )
    _index("ix_groups_users_expires_at", "groups_users", "expires_at")

    op.create_table(
        "groups_missions",
        _str("mission_name", nullable=False),
        _int("group_id", nullable=False),
        _pk("groups_missions", "mission_name", "group_id"),
        _fk("groups_missions_group_id_fkey", "group_id", "groups.id"),
        _fk("groups_missions_mission_name_fkey", "mission_name", "missions.name"),
    )

    # --- misc ------------------------------------------------------------------
    op.create_table(
        "eud_stats",
        _id(),
        _ts("timestamp"),
        _str("eud_uid", nullable=False),
        _big("heap_free_size"),
        _int("app_framerate"),
        _big("storage_total"),
        _int("heap_current_size"),
        _int("battery"),
        _int("battery_temp"),
        _big("deviceDataRx"),
        _big("heap_max_size"),
        _big("storage_available"),
        _big("deviceDataTx"),
        _str("ip_address"),
        _str("battery_status"),
        _pk("eud_stats"),
        _fk("eud_stats_eud_uid_fkey", "eud_uid", "euds.uid", ondelete="CASCADE"),
    )
    _index("ix_eud_stats_timestamp", "eud_stats", "timestamp")

    op.create_table(
        "plugins",
        _id(),
        _str("name", nullable=False),
        _bool("enabled", nullable=False),
        _str("author"),
        _str("version"),
        _str("distro", nullable=False),
        _pk("plugins"),
        _uq("plugins_name_key", "name"),
        _uq("distro", "distro"),
    )

    op.create_table(
        "tokens",
        _id(),
        _str("username"),
        _int("max_uses"),
        _int("total_uses"),
        _big("creation", nullable=False),
        _big("not_before"),
        _big("expiration"),
        _bool("disabled"),
        _str("token_hash", nullable=False),
        _pk("tokens"),
        _uq("tokens_token_hash_key", "token_hash"),
        _uq("tokens_username_key", "username"),
        _fk("tokens_username_fkey", "username", "user.username"),
    )

    op.create_table(
        "citrap",
        _str("id", nullable=False),
        _str("type"),
        _str("title"),
        _bool("visible"),
        _str("delimiter"),
        _str("user_callsign"),
        _str("user_description"),
        _ts("date_time"),
        _str("date_time_description"),
        _int("point_id", nullable=False),
        _str("location_description"),
        _str("event_scale"),
        _str("importance"),
        _str("tags"),
        _str("scale_description"),
        _str("status"),
        _str("file_name"),
        _str("hash"),
        _pk("citrap"),
        _uq("citrap_hash_key", "hash"),
        _fk("citrap_point_id_fkey", "point_id", "points.id", ondelete="CASCADE"),
    )
    _index("ix_citrap_point_id", "citrap", "point_id")

    op.create_table(
        "user_filters",
        _id(),
        _str("name", nullable=False),
        _pk("user_filters"),
        _uq("user_filters_name_key", "name"),
    )

    op.create_table(
        "user_filters_members",
        _int("user_id", nullable=False),
        _int("filter_id", nullable=False),
        _pk("user_filters_members", "user_id", "filter_id"),
        _fk("user_filters_members_user_id_fkey", "user_id", "user.id"),
        _fk("user_filters_members_filter_id_fkey", "filter_id", "user_filters.id"),
    )

    op.create_table(
        "supporting_documents",
        _id(),
        _str("filename", nullable=False),
        _str("stored_filename", nullable=False),
        _str("mime_type"),
        _int("size", nullable=False),
        _ts("uploaded_at", nullable=False),
        _int("uploaded_by_id"),
        _pk("supporting_documents"),
        _uq("supporting_documents_stored_filename_key", "stored_filename"),
        _fk("supporting_documents_uploaded_by_id_fkey", "uploaded_by_id", "user.id", ondelete="SET NULL"),
    )

    op.create_table(
        "data_package_recipients",
        _id(),
        _str("data_package_hash", nullable=False),
        _str("eud_uid"),
        _str("callsign"),
        _ts("created_at", nullable=False),
        _pk("data_package_recipients"),
        _uq("uq_data_package_recipient", "data_package_hash", "eud_uid", "callsign"),
        _fk(
            "data_package_recipients_data_package_hash_fkey",
            "data_package_hash",
            "data_packages.hash",
            ondelete="CASCADE",
        ),
    )
    _index("ix_data_package_recipients_hash", "data_package_recipients", "data_package_hash")

    op.create_table(
        "pending_clear_commands",
        _id(),
        _str("eud_uid", nullable=False),
        _bool("clearmaps", nullable=False),
        _str("requested_by"),
        _ts("requested_at", nullable=False),
        _ts("delivered_at"),
        _str("node_id"),
        _pk("pending_clear_commands"),
    )
    _index("ix_pending_clear_commands_eud_uid", "pending_clear_commands", "eud_uid")

    op.create_table(
        "audit_events",
        _id(),
        _ts("timestamp", nullable=False),
        _str("category", 32, nullable=False),
        _str("action", 64, nullable=False),
        _str("username"),
        _str("actor"),
        _str("ip", 64),
        _str("user_agent", 512),
        _bool("success", nullable=False),
        _text("details"),
        _str("summary"),
        _pk("audit_events"),
    )
    for col in ("timestamp", "category", "action", "username"):
        _index(f"ix_audit_events_{col}", "audit_events", col)

    # --- device profiles -------------------------------------------------------
    op.create_table(
        "device_profile_sets",
        _id(),
        _str("name", nullable=False),
        _text("description"),
        _bool("enabled", nullable=False, server_default=TRUE),
        _bool("apply_on_enrollment", nullable=False, server_default=TRUE),
        _bool("apply_on_connection", nullable=False, server_default=FALSE),
        _bool("target_everyone", nullable=False, server_default=FALSE),
        _ts("created_at", nullable=False),
        _ts("updated_at", nullable=False),
        _str("tool"),
        _pk("device_profile_sets"),
        _uq("device_profile_sets_name_key", "name"),
    )
    _index("ix_device_profile_sets_tool", "device_profile_sets", "tool")

    op.create_table(
        "device_profile_targets",
        _id(),
        _int("profile_id", nullable=False),
        _str("kind", 16, nullable=False),
        _str("value", nullable=False),
        _pk("device_profile_targets"),
        _fk("device_profile_targets_profile_id_fkey", "profile_id", "device_profile_sets.id", ondelete="CASCADE"),
    )
    _index("ix_device_profile_targets_profile_id", "device_profile_targets", "profile_id")

    op.create_table(
        "device_profile_settings",
        _id(),
        _int("profile_id", nullable=False),
        _str("key", nullable=False),
        _text("value", nullable=False),
        _str("value_class", 64, nullable=False),
        _pk("device_profile_settings"),
        _uq("uq_device_profile_settings_profile_key", "profile_id", "key"),
        _fk("device_profile_settings_profile_id_fkey", "profile_id", "device_profile_sets.id", ondelete="CASCADE"),
    )
    _index("ix_device_profile_settings_profile_id", "device_profile_settings", "profile_id")

    op.create_table(
        "device_profile_files",
        _id(),
        _int("profile_id", nullable=False),
        _str("filename", nullable=False),
        _str("zip_path", 512, nullable=False),
        _str("stored_name", 64, nullable=False),
        _big("size", nullable=False),
        _str("sha256", 64, nullable=False),
        _str("content_type"),
        _str("uploaded_by"),
        _ts("created_at", nullable=False),
        _pk("device_profile_files"),
        _uq("device_profile_files_stored_name_key", "stored_name"),
        _uq("uq_device_profile_files_profile_path", "profile_id", "zip_path"),
        _fk("device_profile_files_profile_id_fkey", "profile_id", "device_profile_sets.id", ondelete="CASCADE"),
    )
    _index("ix_device_profile_files_profile_id", "device_profile_files", "profile_id")

    op.create_table(
        "data_packages_groups",
        _int("data_package_id", nullable=False),
        _int("group_id", nullable=False),
        _pk("data_packages_groups", "data_package_id", "group_id"),
        _fk("data_packages_groups_data_package_id_fkey", "data_package_id", "data_packages.id", ondelete="CASCADE"),
        _fk("data_packages_groups_group_id_fkey", "group_id", "groups.id", ondelete="CASCADE"),
    )

    # --- metrics, retention, offline chat --------------------------------------
    op.create_table(
        "metric_counters",
        _str("name", 128, nullable=False),
        _str("labels", nullable=False),
        _str("kind", 16, nullable=False),
        _big("value", nullable=False),
        _ts("updated_at", nullable=False),
        _pk("metric_counters", "name", "labels"),
    )

    op.create_table(
        "metric_samples",
        _id(),
        _ts("timestamp", nullable=False),
        _big("cot_received", nullable=False),
        _big("cot_sent", nullable=False),
        _big("bytes_received", nullable=False),
        _big("bytes_sent", nullable=False),
        _int("euds_connected", nullable=False),
        _pk("metric_samples"),
    )
    _index("ix_metric_samples_timestamp", "metric_samples", "timestamp")

    op.create_table(
        "retention_settings",
        _str("data_type", 64, nullable=False),
        _int("keep_seconds"),
        _bool("customized", nullable=False, server_default=FALSE),
        _ts("updated_at"),
        _str("updated_by"),
        _ts("last_purge_at"),
        _int("last_purge_count"),
        _text("last_purge_error"),
        _pk("retention_settings", "data_type"),
    )

    op.create_table(
        "offline_chat_messages",
        _id(),
        _str("recipient_uid", nullable=False),
        _str("sender_uid"),
        _str("chatroom_id"),
        _str("message_uid", nullable=False),
        _text("xml", nullable=False),
        _ts("created_at", nullable=False),
        _ts("delivered_at"),
        _int("recipient_user_id"),
        _text("read_groups"),
        _pk("offline_chat_messages"),
        _uq("uq_offline_chat_recipient_message", "recipient_uid", "message_uid"),
    )
    _index("ix_offline_chat_messages_recipient_uid", "offline_chat_messages", "recipient_uid")
    _index("ix_offline_chat_messages_created_at", "offline_chat_messages", "created_at")

    # --- mission sub-resources -------------------------------------------------
    op.create_table(
        "mission_layers",
        _str("uid", nullable=False),
        _str("mission_name", nullable=False),
        _str("name", nullable=False),
        _str("type", 32, nullable=False),
        _str("parent_uid"),
        _str("after"),
        _json("uids"),
        _json("hashes"),
        _str("creator_uid"),
        _ts("create_time"),
        _pk("mission_layers", "uid"),
        _fk("mission_layers_mission_name_fkey", "mission_name", "missions.name", ondelete="CASCADE"),
        _fk("mission_layers_parent_uid_fkey", "parent_uid", "mission_layers.uid", ondelete="CASCADE"),
    )
    _index("ix_mission_layers_mission_name", "mission_layers", "mission_name")

    op.create_table(
        "mission_external_data",
        _id(),
        _str("uid", nullable=False),
        _str("mission_name", nullable=False),
        _str("name"),
        _str("tool"),
        _text("url_data"),
        _text("url_view"),
        _text("notes"),
        _str("creator_uid"),
        _ts("create_time"),
        _pk("mission_external_data"),
        _uq("mission_external_data_mission_name_uid_key", "mission_name", "uid"),
        _fk("mission_external_data_mission_name_fkey", "mission_name", "missions.name", ondelete="CASCADE"),
    )

    op.create_table(
        "mission_feeds",
        _id(),
        _str("uid", nullable=False),
        _str("mission_name", nullable=False),
        _str("data_feed_uid", nullable=False),
        _str("name"),
        _str("filter_bbox", 1024),
        _str("filter_type", 1024),
        _str("filter_callsign"),
        _str("creator_uid"),
        _ts("create_time"),
        _pk("mission_feeds"),
        _uq("mission_feeds_uid_key", "uid"),
        _fk("mission_feeds_mission_name_fkey", "mission_name", "missions.name", ondelete="CASCADE"),
    )

    op.create_table(
        "enrollment_blocks",
        _str("eud_uid", nullable=False),
        _str("username"),
        _str("serial_number", 64),
        _str("reason", 64),
        _ts("blocked_at", nullable=False),
        _str("blocked_by"),
        _pk("enrollment_blocks", "eud_uid"),
    )
    _index("ix_enrollment_blocks_username", "enrollment_blocks", "username")


def downgrade():
    # Downgrading the baseline would mean dropping every table and all data.
    # Refuse instead, so "downgrade -1" fails harmlessly.
    raise RuntimeError(
        "Refusing to downgrade past the baseline revision 9c4e1d2b7a31: "
        "this would drop the entire Raven schema. Restore from a backup instead."
    )
