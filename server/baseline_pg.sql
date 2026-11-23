CREATE TABLE chatrooms (
    id VARCHAR(255) NOT NULL, 
    name VARCHAR(255) NOT NULL, 
    group_owner VARCHAR(255), 
    parent VARCHAR(255), 
    CONSTRAINT chatrooms_pkey PRIMARY KEY (id)
);

CREATE TABLE icons (
    id SERIAL NOT NULL, 
    iconset_uid VARCHAR(255), 
    filename VARCHAR(255), 
    "groupName" VARCHAR(255), 
    type2525b VARCHAR(255), 
    "useCnt" INTEGER, 
    bitmap BYTEA, 
    shadow BYTEA, 
    CONSTRAINT icons_pkey PRIMARY KEY (id)
);

CREATE TABLE role (
    id SERIAL NOT NULL, 
    name VARCHAR(80) NOT NULL, 
    description VARCHAR(255), 
    permissions TEXT, 
    update_datetime TIMESTAMP WITHOUT TIME ZONE DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
    CONSTRAINT role_pkey PRIMARY KEY (id), 
    CONSTRAINT role_name_key UNIQUE (name)
);

CREATE TABLE "user" (
    email VARCHAR(255), 
    fs_webauthn_user_handle VARCHAR(64), 
    mf_recovery_codes TEXT, 
    password VARCHAR(255), 
    us_phone_number VARCHAR(128), 
    username VARCHAR(255), 
    us_totp_secrets TEXT, 
    id SERIAL NOT NULL, 
    active BOOLEAN NOT NULL, 
    fs_uniquifier VARCHAR(64) NOT NULL, 
    confirmed_at TIMESTAMP WITHOUT TIME ZONE, 
    last_login_at TIMESTAMP WITHOUT TIME ZONE, 
    current_login_at TIMESTAMP WITHOUT TIME ZONE, 
    last_login_ip VARCHAR(64), 
    current_login_ip VARCHAR(64), 
    login_count INTEGER, 
    tf_primary_method VARCHAR(64), 
    tf_totp_secret VARCHAR(255), 
    tf_phone_number VARCHAR(128), 
    create_datetime TIMESTAMP WITHOUT TIME ZONE DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
    update_datetime TIMESTAMP WITHOUT TIME ZONE DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
    site_access BOOLEAN DEFAULT true NOT NULL, 
    force_password_change BOOLEAN DEFAULT false NOT NULL, 
    cert_validity_days INTEGER, 
    admin_view_areas TEXT, 
    CONSTRAINT user_pkey PRIMARY KEY (id), 
    CONSTRAINT user_fs_uniquifier_key UNIQUE (fs_uniquifier), 
    CONSTRAINT user_fs_webauthn_user_handle_key UNIQUE (fs_webauthn_user_handle), 
    CONSTRAINT user_us_phone_number_key UNIQUE (us_phone_number), 
    CONSTRAINT user_username_key UNIQUE (username)
);

CREATE TABLE roles_users (
    user_id INTEGER, 
    role_id INTEGER, 
    CONSTRAINT role FOREIGN KEY(role_id) REFERENCES role (id), 
    CONSTRAINT "user" FOREIGN KEY(user_id) REFERENCES "user" (id)
);

CREATE TABLE teams (
    id SERIAL NOT NULL, 
    name VARCHAR(255) NOT NULL, 
    chatroom_id VARCHAR(255), 
    CONSTRAINT teams_pkey PRIMARY KEY (id), 
    CONSTRAINT teams_name_key UNIQUE (name), 
    CONSTRAINT team_chatroom FOREIGN KEY(chatroom_id) REFERENCES chatrooms (id)
);

CREATE TABLE web_authn (
    id SERIAL NOT NULL, 
    credential_id BYTEA NOT NULL, 
    public_key BYTEA NOT NULL, 
    sign_count INTEGER, 
    transports TEXT, 
    backup_state BOOLEAN NOT NULL, 
    device_type VARCHAR(64) NOT NULL, 
    extensions VARCHAR(255), 
    create_datetime TIMESTAMP WITHOUT TIME ZONE DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
    lastuse_datetime TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
    name VARCHAR(64) NOT NULL, 
    usage VARCHAR(64) NOT NULL, 
    user_id INTEGER NOT NULL, 
    CONSTRAINT web_authn_pkey PRIMARY KEY (id), 
    CONSTRAINT web_authn_user FOREIGN KEY(user_id) REFERENCES "user" (id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX ix_web_authn_credential_id ON web_authn (credential_id);

CREATE TABLE euds (
    id SERIAL NOT NULL, 
    uid VARCHAR(255) NOT NULL, 
    callsign VARCHAR(255), 
    device VARCHAR(255), 
    os VARCHAR(255), 
    platform VARCHAR(255), 
    version VARCHAR(255), 
    phone_number BIGINT, 
    last_event_time TIMESTAMP WITHOUT TIME ZONE, 
    last_status VARCHAR(255), 
    user_id INTEGER, 
    team_id INTEGER, 
    team_role VARCHAR(255), 
    meshtastic_id BIGINT, 
    meshtastic_macaddr VARCHAR(255), 
    last_meshtastic_publish TIMESTAMP WITHOUT TIME ZONE, 
    federated_from VARCHAR(255), 
    last_rx_at TIMESTAMP WITHOUT TIME ZONE, 
    CONSTRAINT euds_pkey PRIMARY KEY (id), 
    CONSTRAINT euds_uid_key UNIQUE (uid), 
    CONSTRAINT euds_callsign_key UNIQUE (callsign), 
    CONSTRAINT eud_team FOREIGN KEY(team_id) REFERENCES teams (id), 
    CONSTRAINT eud_user FOREIGN KEY(user_id) REFERENCES "user" (id)
);

CREATE TABLE chatrooms_uids (
    chatroom_id VARCHAR(255) NOT NULL, 
    uid VARCHAR(255) NOT NULL, 
    CONSTRAINT chatrooms_uids_pkey PRIMARY KEY (chatroom_id, uid), 
    CONSTRAINT chatroom_uids FOREIGN KEY(chatroom_id) REFERENCES chatrooms (id), 
    CONSTRAINT chatroom_eud FOREIGN KEY(uid) REFERENCES euds (uid) ON DELETE CASCADE
);

CREATE TABLE missions (
    name VARCHAR(255) NOT NULL, 
    description VARCHAR(255), 
    chat_room VARCHAR(255), 
    base_layer VARCHAR(255), 
    bbox VARCHAR(255), 
    path VARCHAR(255), 
    classification VARCHAR(255), 
    tool VARCHAR(255), 
    default_role VARCHAR(255), 
    keywords JSON, 
    creator_uid VARCHAR(255), 
    create_time TIMESTAMP WITHOUT TIME ZONE, 
    external_data JSON, 
    feeds JSON, 
    map_layers JSON, 
    invite_only BOOLEAN, 
    expiration INTEGER NOT NULL, 
    guid VARCHAR(255), 
    password_protected BOOLEAN, 
    password VARCHAR(255), 
    "group" VARCHAR(255), 
    CONSTRAINT missions_pkey PRIMARY KEY (name), 
    CONSTRAINT mission_creator FOREIGN KEY(creator_uid) REFERENCES euds (uid) ON DELETE CASCADE
);

CREATE TABLE cot (
    id SERIAL NOT NULL, 
    how VARCHAR(255), 
    type VARCHAR(255), 
    sender_callsign VARCHAR(255), 
    sender_device_name VARCHAR(255), 
    sender_uid VARCHAR(255), 
    recipients JSON, 
    timestamp TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
    start TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
    stale TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
    xml TEXT NOT NULL, 
    mission_name VARCHAR(255), 
    uid VARCHAR(255), 
    CONSTRAINT cot_pkey PRIMARY KEY (id), 
    CONSTRAINT cot_eud FOREIGN KEY(sender_uid) REFERENCES euds (uid) ON DELETE CASCADE, 
    CONSTRAINT mission_name FOREIGN KEY(mission_name) REFERENCES missions (name)
);

CREATE INDEX ix_cot_timestamp ON cot (timestamp);

CREATE INDEX ix_cot_type ON cot (type);

CREATE INDEX ix_cot_uid ON cot (uid);

CREATE INDEX ix_cot_sender_uid ON cot (sender_uid);

CREATE TABLE data_packages (
    id SERIAL NOT NULL, 
    filename VARCHAR(255) NOT NULL, 
    hash VARCHAR(255) NOT NULL, 
    creator_uid VARCHAR(255), 
    submission_time TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
    submission_user INTEGER, 
    keywords VARCHAR(255), 
    mime_type VARCHAR(255) NOT NULL, 
    size INTEGER NOT NULL, 
    tool VARCHAR(255), 
    expiration VARCHAR(255), 
    install_on_enrollment BOOLEAN, 
    install_on_connection BOOLEAN, 
    CONSTRAINT data_packages_pkey PRIMARY KEY (id), 
    CONSTRAINT data_packages_hash_key UNIQUE (hash), 
    CONSTRAINT data_packages_eud FOREIGN KEY(creator_uid) REFERENCES euds (uid) ON DELETE CASCADE, 
    CONSTRAINT data_packages_user FOREIGN KEY(submission_user) REFERENCES "user" (id)
);

CREATE TABLE certificates (
    id SERIAL NOT NULL, 
    common_name VARCHAR(255) NOT NULL, 
    eud_uid VARCHAR(255), 
    data_package_id INTEGER, 
    callsign VARCHAR(255), 
    username VARCHAR(255), 
    expiration_date TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
    server_address VARCHAR(255) NOT NULL, 
    server_port INTEGER NOT NULL, 
    truststore_filename VARCHAR(255) NOT NULL, 
    user_cert_filename VARCHAR(255) NOT NULL, 
    csr VARCHAR(255), 
    cert_password VARCHAR(255), 
    serial_number VARCHAR(64), 
    revoked_at TIMESTAMP WITHOUT TIME ZONE, 
    revocation_reason VARCHAR(64), 
    CONSTRAINT certificates_pkey PRIMARY KEY (id), 
    CONSTRAINT certificate_data_package FOREIGN KEY(data_package_id) REFERENCES data_packages (id), 
    CONSTRAINT certificate_eud FOREIGN KEY(eud_uid) REFERENCES euds (uid) ON DELETE CASCADE, 
    CONSTRAINT certificate_user FOREIGN KEY(username) REFERENCES "user" (username)
);

CREATE INDEX ix_certificates_serial_number ON certificates (serial_number);

CREATE TABLE points (
    id SERIAL NOT NULL, 
    uid VARCHAR(255) NOT NULL, 
    device_uid VARCHAR(255), 
    latitude FLOAT, 
    longitude FLOAT, 
    ce FLOAT, 
    hae FLOAT, 
    le FLOAT, 
    course FLOAT, 
    speed FLOAT, 
    location_source VARCHAR(255), 
    battery FLOAT, 
    timestamp TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
    azimuth FLOAT, 
    fov FLOAT, 
    cot_id INTEGER, 
    CONSTRAINT points_pkey PRIMARY KEY (id), 
    CONSTRAINT point_cot FOREIGN KEY(cot_id) REFERENCES cot (id) ON DELETE CASCADE, 
    CONSTRAINT point_eud FOREIGN KEY(device_uid) REFERENCES euds (uid) ON DELETE CASCADE
);

CREATE INDEX ix_points_cot_id ON points (cot_id);

CREATE INDEX ix_points_latitude_longitude ON points (latitude, longitude);

CREATE INDEX ix_points_timestamp ON points (timestamp);

CREATE INDEX ix_points_device_uid_timestamp ON points (device_uid, timestamp);

CREATE TABLE video_streams (
    path VARCHAR(255) NOT NULL, 
    protocol VARCHAR(255) NOT NULL, 
    port INTEGER NOT NULL, 
    network_timeout INTEGER NOT NULL, 
    uid VARCHAR(255), 
    buffer_time INTEGER, 
    rover_port INTEGER, 
    rtsp_reliable INTEGER, 
    ignore_embedded_klv BOOLEAN, 
    alias VARCHAR(255), 
    preferred_mac_address VARCHAR(255), 
    preferred_interface_address VARCHAR(255), 
    username VARCHAR(255), 
    xml TEXT, 
    ready BOOLEAN NOT NULL, 
    mediamtx_settings TEXT NOT NULL, 
    cot_id INTEGER, 
    web_path VARCHAR(255), 
    CONSTRAINT video_streams_pkey PRIMARY KEY (path), 
    CONSTRAINT video_stream_cot FOREIGN KEY(cot_id) REFERENCES cot (id), 
    CONSTRAINT video_stream_user FOREIGN KEY(username) REFERENCES "user" (username)
);

CREATE TABLE alerts (
    id SERIAL NOT NULL, 
    uid VARCHAR(255) NOT NULL, 
    sender_uid VARCHAR(255) NOT NULL, 
    start_time TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
    cancel_time TIMESTAMP WITHOUT TIME ZONE, 
    alert_type VARCHAR(255) NOT NULL, 
    point_id INTEGER, 
    cot_id INTEGER, 
    CONSTRAINT alerts_pkey PRIMARY KEY (id), 
    CONSTRAINT alert_point FOREIGN KEY(point_id) REFERENCES points (id) ON DELETE CASCADE, 
    CONSTRAINT alert_eud FOREIGN KEY(sender_uid) REFERENCES euds (uid) ON DELETE CASCADE, 
    CONSTRAINT alert_cot FOREIGN KEY(cot_id) REFERENCES cot (id) ON DELETE CASCADE
);

CREATE INDEX ix_alerts_start_time ON alerts (start_time);

CREATE INDEX ix_alerts_point_id ON alerts (point_id);

CREATE TABLE casevac (
    id SERIAL NOT NULL, 
    sender_uid VARCHAR(255) NOT NULL, 
    uid VARCHAR(255) NOT NULL, 
    timestamp TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
    ambulatory INTEGER, 
    casevac BOOLEAN, 
    child INTEGER, 
    enemy VARCHAR(255), 
    epw INTEGER, 
    equipment_detail VARCHAR(255), 
    equipment_none BOOLEAN, 
    equipment_other BOOLEAN, 
    extraction_equipment BOOLEAN, 
    freq FLOAT, 
    friendlies VARCHAR(255), 
    hlz_marking INTEGER, 
    hlz_remarks VARCHAR(255), 
    hoist BOOLEAN, 
    litter INTEGER, 
    marked_by VARCHAR(255), 
    medline_remarks VARCHAR(255), 
    nonus_civilian INTEGER, 
    nonus_military INTEGER, 
    obstacles VARCHAR(255), 
    priority INTEGER, 
    routine INTEGER, 
    security INTEGER, 
    terrain_loose BOOLEAN, 
    terrain_other BOOLEAN, 
    terrain_other_detail VARCHAR(255), 
    terrain_detail VARCHAR(255), 
    terrain_none BOOLEAN, 
    terrain_rough BOOLEAN, 
    terrain_slope BOOLEAN, 
    terrain_slope_dir VARCHAR(255), 
    title VARCHAR(255) NOT NULL, 
    urgent INTEGER, 
    us_civilian INTEGER, 
    us_military INTEGER, 
    ventilator BOOLEAN, 
    winds_are_from VARCHAR(255), 
    zone_prot_selection INTEGER, 
    point_id INTEGER, 
    cot_id INTEGER, 
    convenience INTEGER, 
    urgent_surgical VARCHAR(255), 
    CONSTRAINT casevac_pkey PRIMARY KEY (id), 
    CONSTRAINT casevac_uid_key UNIQUE (uid), 
    CONSTRAINT casevac_cot FOREIGN KEY(cot_id) REFERENCES cot (id) ON DELETE CASCADE, 
    CONSTRAINT casevac_eud FOREIGN KEY(sender_uid) REFERENCES euds (uid) ON DELETE CASCADE, 
    CONSTRAINT casevac_point FOREIGN KEY(point_id) REFERENCES points (id) ON DELETE CASCADE
);

CREATE INDEX ix_casevac_point_id ON casevac (point_id);

CREATE TABLE geochat (
    uid VARCHAR(255) NOT NULL, 
    chatroom_id VARCHAR(255) NOT NULL, 
    sender_uid VARCHAR(255) NOT NULL, 
    remarks VARCHAR(255) NOT NULL, 
    timestamp TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
    point_id INTEGER NOT NULL, 
    cot_id INTEGER NOT NULL, 
    CONSTRAINT geochat_pkey PRIMARY KEY (uid), 
    CONSTRAINT geochat_chatroom FOREIGN KEY(chatroom_id) REFERENCES chatrooms (id), 
    CONSTRAINT geochat_cot FOREIGN KEY(cot_id) REFERENCES cot (id), 
    CONSTRAINT geochat_point FOREIGN KEY(point_id) REFERENCES points (id), 
    CONSTRAINT geochat_eud FOREIGN KEY(sender_uid) REFERENCES euds (uid) ON DELETE CASCADE
);

CREATE INDEX ix_geochat_cot_id ON geochat (cot_id);

CREATE INDEX ix_geochat_timestamp ON geochat (timestamp);

CREATE INDEX ix_geochat_point_id ON geochat (point_id);

CREATE TABLE markers (
    id SERIAL NOT NULL, 
    uid VARCHAR(255) NOT NULL, 
    affiliation VARCHAR(255), 
    battle_dimension VARCHAR(255), 
    point_id INTEGER NOT NULL, 
    callsign VARCHAR(255), 
    readiness BOOLEAN, 
    argb INTEGER, 
    color_hex VARCHAR(255), 
    iconset_path VARCHAR(255), 
    parent_callsign VARCHAR(255), 
    production_time VARCHAR(255), 
    relation VARCHAR(255), 
    relation_type VARCHAR(255), 
    location_source VARCHAR(255), 
    icon_id INTEGER, 
    parent_uid VARCHAR(255), 
    remarks VARCHAR(255), 
    cot_id INTEGER, 
    mil_std_2525c VARCHAR(255), 
    CONSTRAINT markers_pkey PRIMARY KEY (id), 
    CONSTRAINT markers_uid_key UNIQUE (uid), 
    CONSTRAINT marker_icon FOREIGN KEY(icon_id) REFERENCES icons (id), 
    CONSTRAINT markers_cot FOREIGN KEY(cot_id) REFERENCES cot (id) ON DELETE CASCADE, 
    CONSTRAINT markers_eud FOREIGN KEY(parent_uid) REFERENCES euds (uid) ON DELETE CASCADE, 
    CONSTRAINT markers_point FOREIGN KEY(point_id) REFERENCES points (id) ON DELETE CASCADE
);

CREATE INDEX ix_markers_cot_id ON markers (cot_id);

CREATE INDEX ix_markers_point_id ON markers (point_id);

CREATE TABLE rb_lines (
    id SERIAL NOT NULL, 
    sender_uid VARCHAR(255) NOT NULL, 
    uid VARCHAR(255) NOT NULL, 
    timestamp TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
    range FLOAT NOT NULL, 
    bearing FLOAT NOT NULL, 
    inclination FLOAT, 
    anchor_uid VARCHAR(255), 
    range_units INTEGER, 
    bearing_units INTEGER, 
    north_ref INTEGER, 
    color INTEGER, 
    color_hex VARCHAR(255), 
    callsign VARCHAR(255), 
    stroke_color INTEGER, 
    stroke_weight FLOAT, 
    stroke_style VARCHAR(255), 
    labels_on BOOLEAN, 
    point_id INTEGER, 
    cot_id INTEGER, 
    end_latitude FLOAT, 
    end_longitude FLOAT, 
    CONSTRAINT rb_lines_pkey PRIMARY KEY (id), 
    CONSTRAINT rb_lines_uid_key UNIQUE (uid), 
    CONSTRAINT rb_line_cot FOREIGN KEY(cot_id) REFERENCES cot (id) ON DELETE CASCADE, 
    CONSTRAINT rb_line_eud FOREIGN KEY(sender_uid) REFERENCES euds (uid) ON DELETE CASCADE, 
    CONSTRAINT rb_line_point FOREIGN KEY(point_id) REFERENCES points (id) ON DELETE CASCADE
);

CREATE INDEX ix_rb_lines_point_id ON rb_lines (point_id);

CREATE TABLE video_recordings (
    id SERIAL NOT NULL, 
    segment_path VARCHAR(255) NOT NULL, 
    path VARCHAR(255) NOT NULL, 
    in_progress BOOLEAN NOT NULL, 
    start_time TIMESTAMP WITHOUT TIME ZONE, 
    stop_time TIMESTAMP WITHOUT TIME ZONE, 
    duration INTEGER, 
    width INTEGER, 
    height INTEGER, 
    video_codec VARCHAR(255), 
    video_bitrate INTEGER, 
    audio_codec VARCHAR(255), 
    audio_bitrate INTEGER, 
    audio_samplerate INTEGER, 
    audio_channels INTEGER, 
    file_size INTEGER, 
    CONSTRAINT video_recordings_pkey PRIMARY KEY (id), 
    CONSTRAINT video_recordings_segment_path_key UNIQUE (segment_path), 
    CONSTRAINT video_recording_video_stream FOREIGN KEY(path) REFERENCES video_streams (path)
);

CREATE TABLE zmist (
    id SERIAL NOT NULL, 
    i VARCHAR(255), 
    m VARCHAR(255), 
    s VARCHAR(255), 
    t VARCHAR(255), 
    title VARCHAR(255), 
    z INTEGER, 
    casevac_uid VARCHAR(255) NOT NULL, 
    CONSTRAINT zmist_pkey PRIMARY KEY (id), 
    CONSTRAINT zmist_casevac FOREIGN KEY(casevac_uid) REFERENCES casevac (uid) ON DELETE CASCADE
);

CREATE TABLE iconsets (
    id SERIAL NOT NULL, 
    name VARCHAR(255), 
    uid VARCHAR(255), 
    version INTEGER, 
    "defaultFriendly" VARCHAR(255), 
    "defaultHostile" VARCHAR(255), 
    "defaultNeutral" VARCHAR(255), 
    "defaultUnknown" VARCHAR(255), 
    "selectedGroup" VARCHAR(255), 
    CONSTRAINT iconsets_pkey PRIMARY KEY (id)
);

CREATE TABLE meshtastic_channels (
    id SERIAL NOT NULL, 
    psk VARCHAR(255), 
    name VARCHAR(255), 
    uplink_enabled BOOLEAN NOT NULL, 
    downlink_enabled BOOLEAN NOT NULL, 
    position_precision INTEGER NOT NULL, 
    lora_region INTEGER NOT NULL, 
    lora_hop_limit INTEGER NOT NULL, 
    lora_tx_enabled BOOLEAN NOT NULL, 
    lora_tx_power INTEGER NOT NULL, 
    lora_sx126x_rx_boosted_gain BOOLEAN NOT NULL, 
    modem_preset INTEGER NOT NULL, 
    url VARCHAR(255) NOT NULL, 
    CONSTRAINT meshtastic_channels_pkey PRIMARY KEY (id), 
    CONSTRAINT meshtastic_channels_url_key UNIQUE (url)
);

CREATE SEQUENCE device_profiles_id_seq;

CREATE TABLE device_profiles (
    preference_key VARCHAR(255) NOT NULL, 
    preference_value VARCHAR(255), 
    value_class VARCHAR(255), 
    enrollment BOOLEAN, 
    connection BOOLEAN, 
    tool VARCHAR(255), 
    active BOOLEAN NOT NULL, 
    publish_time TIMESTAMP WITHOUT TIME ZONE DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
    eud_uid VARCHAR(255), 
    id INTEGER DEFAULT nextval('device_profiles_id_seq'::regclass) NOT NULL, 
    CONSTRAINT primary_key PRIMARY KEY (id)
);

CREATE UNIQUE INDEX preference_key_eud_uid ON device_profiles (preference_key, eud_uid);

CREATE TABLE packages (
    id SERIAL NOT NULL, 
    platform VARCHAR(255) NOT NULL, 
    plugin_type VARCHAR(255) NOT NULL, 
    package_name VARCHAR(255) NOT NULL, 
    name VARCHAR(255) NOT NULL, 
    file_name VARCHAR(255) NOT NULL, 
    version VARCHAR(255) NOT NULL, 
    revision_code INTEGER, 
    description VARCHAR(255), 
    apk_hash VARCHAR(255), 
    os_requirement INTEGER, 
    tak_prereq INTEGER, 
    file_size INTEGER NOT NULL, 
    icon BYTEA, 
    icon_filename VARCHAR(255), 
    install_on_enrollment BOOLEAN, 
    install_on_connection BOOLEAN, 
    publish_time TIMESTAMP WITHOUT TIME ZONE DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
    atak_version VARCHAR(255), 
    CONSTRAINT packages_pkey PRIMARY KEY (id)
);

CREATE TABLE mission_content (
    id SERIAL NOT NULL, 
    keywords JSON, 
    mime_type VARCHAR(255), 
    submission_time TIMESTAMP WITHOUT TIME ZONE, 
    submitter VARCHAR(255), 
    uid VARCHAR(255) NOT NULL, 
    creator_uid VARCHAR(255), 
    hash VARCHAR(255) NOT NULL, 
    size INTEGER, 
    expiration INTEGER, 
    filename VARCHAR(255), 
    CONSTRAINT mission_content_pkey PRIMARY KEY (id), 
    CONSTRAINT mission_content_uid_key UNIQUE (uid), 
    CONSTRAINT hash_index UNIQUE (hash)
);

CREATE TABLE mission_uids (
    uid VARCHAR(255) NOT NULL, 
    mission_name VARCHAR(255), 
    timestamp TIMESTAMP WITHOUT TIME ZONE, 
    creator_uid VARCHAR(255), 
    cot_type VARCHAR(255), 
    callsign VARCHAR(255), 
    iconset_path VARCHAR(255), 
    color VARCHAR(255), 
    latitude FLOAT, 
    longitude FLOAT, 
    CONSTRAINT mission_uids_pkey PRIMARY KEY (uid), 
    CONSTRAINT mission_uids_mission_name_fkey FOREIGN KEY(mission_name) REFERENCES missions (name)
);

CREATE TABLE mission_changes (
    id SERIAL NOT NULL, 
    "isFederatedChange" BOOLEAN NOT NULL, 
    change_type VARCHAR(255) NOT NULL, 
    mission_name VARCHAR(255) NOT NULL, 
    timestamp TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
    creator_uid VARCHAR(255) NOT NULL, 
    server_time TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
    content_uid VARCHAR(255), 
    mission_uid VARCHAR(255), 
    external_data JSON, 
    mission_feed JSON, 
    mission_layer JSON, 
    CONSTRAINT mission_changes_pkey PRIMARY KEY (id), 
    CONSTRAINT mission_changes FOREIGN KEY(mission_name) REFERENCES missions (name), 
    CONSTRAINT content_resource FOREIGN KEY(content_uid) REFERENCES mission_content (uid), 
    CONSTRAINT mission_change_uid FOREIGN KEY(mission_uid) REFERENCES mission_uids (uid) ON DELETE CASCADE
);

CREATE TABLE mission_content_mission (
    id SERIAL NOT NULL, 
    mission_content_id INTEGER NOT NULL, 
    mission_name VARCHAR(255) NOT NULL, 
    CONSTRAINT mission_content_mission_pkey PRIMARY KEY (id), 
    CONSTRAINT mission_content FOREIGN KEY(mission_content_id) REFERENCES mission_content (id), 
    CONSTRAINT mission FOREIGN KEY(mission_name) REFERENCES missions (name)
);

CREATE TABLE mission_invitations (
    id SERIAL NOT NULL, 
    mission_name VARCHAR(255) NOT NULL, 
    client_uid VARCHAR(255), 
    callsign VARCHAR(255), 
    username VARCHAR(255), 
    group_name VARCHAR(255), 
    team_name VARCHAR(255), 
    creator_uid VARCHAR(255), 
    role VARCHAR(255), 
    type VARCHAR(255), 
    mission_guid VARCHAR(255), 
    CONSTRAINT mission_invitations_pkey PRIMARY KEY (id), 
    CONSTRAINT mission_invitations FOREIGN KEY(mission_name) REFERENCES missions (name), 
    CONSTRAINT mission_invitation_user FOREIGN KEY(username) REFERENCES "user" (username), 
    CONSTRAINT mission_invitation_team FOREIGN KEY(team_name) REFERENCES teams (name), 
    CONSTRAINT mission_invitation_callsign FOREIGN KEY(callsign) REFERENCES euds (callsign) ON DELETE CASCADE, 
    CONSTRAINT euds FOREIGN KEY(client_uid) REFERENCES euds (uid)
);

CREATE TABLE mission_roles (
    id SERIAL NOT NULL, 
    "clientUid" VARCHAR(255) NOT NULL, 
    username VARCHAR(255) NOT NULL, 
    "createTime" TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
    role_type VARCHAR(255) NOT NULL, 
    mission_name VARCHAR(255), 
    mission_guid VARCHAR(255), 
    CONSTRAINT mission_roles_pkey PRIMARY KEY (id), 
    CONSTRAINT mission_role_names FOREIGN KEY(mission_name) REFERENCES missions (name)
);

CREATE TABLE mission_logs (
    id SERIAL NOT NULL, 
    content VARCHAR(255) NOT NULL, 
    creator_uid VARCHAR(255) NOT NULL, 
    entry_uid VARCHAR(255) NOT NULL, 
    mission_name VARCHAR(255) NOT NULL, 
    server_time TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
    dtg TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
    created TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
    content_hash VARCHAR(255), 
    keywords JSON NOT NULL, 
    content_hashes JSON, 
    CONSTRAINT mission_logs_pkey PRIMARY KEY (id), 
    CONSTRAINT mission_logs FOREIGN KEY(mission_name) REFERENCES missions (name)
);

CREATE TABLE groups (
    id SERIAL NOT NULL, 
    created TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
    bitpos INTEGER NOT NULL, 
    name VARCHAR(255) NOT NULL, 
    "distinguishedName" VARCHAR(255), 
    type VARCHAR(255) NOT NULL, 
    description VARCHAR(255), 
    default_access VARCHAR(8) DEFAULT 'both', 
    auto_join BOOLEAN DEFAULT false NOT NULL, 
    default_membership_days INTEGER, 
    locked BOOLEAN DEFAULT false NOT NULL, 
    CONSTRAINT groups_pkey PRIMARY KEY (id), 
    CONSTRAINT groups_name_key UNIQUE (name)
);

CREATE TABLE groups_users (
    user_id INTEGER NOT NULL, 
    group_id INTEGER NOT NULL, 
    direction VARCHAR(255) NOT NULL, 
    enabled BOOLEAN NOT NULL, 
    expires_at TIMESTAMP WITHOUT TIME ZONE, 
    CONSTRAINT groups_users_pkey PRIMARY KEY (user_id, group_id, direction), 
    CONSTRAINT groups_users_group_id_fkey FOREIGN KEY(group_id) REFERENCES groups (id), 
    CONSTRAINT groups_users_user_id_fkey FOREIGN KEY(user_id) REFERENCES "user" (id)
);

CREATE INDEX ix_groups_users_expires_at ON groups_users (expires_at);

CREATE TABLE groups_missions (
    mission_name VARCHAR(255) NOT NULL, 
    group_id INTEGER NOT NULL, 
    CONSTRAINT groups_missions_pkey PRIMARY KEY (mission_name, group_id), 
    CONSTRAINT groups_missions_group_id_fkey FOREIGN KEY(group_id) REFERENCES groups (id), 
    CONSTRAINT groups_missions_mission_name_fkey FOREIGN KEY(mission_name) REFERENCES missions (name)
);

CREATE TABLE eud_stats (
    id SERIAL NOT NULL, 
    timestamp TIMESTAMP WITHOUT TIME ZONE, 
    eud_uid VARCHAR(255) NOT NULL, 
    heap_free_size BIGINT, 
    app_framerate INTEGER, 
    storage_total BIGINT, 
    heap_current_size INTEGER, 
    battery INTEGER, 
    battery_temp INTEGER, 
    "deviceDataRx" BIGINT, 
    heap_max_size BIGINT, 
    storage_available BIGINT, 
    "deviceDataTx" BIGINT, 
    ip_address VARCHAR(255), 
    battery_status VARCHAR(255), 
    CONSTRAINT eud_stats_pkey PRIMARY KEY (id), 
    CONSTRAINT eud_stats_eud_uid_fkey FOREIGN KEY(eud_uid) REFERENCES euds (uid) ON DELETE CASCADE
);

CREATE INDEX ix_eud_stats_timestamp ON eud_stats (timestamp);

CREATE TABLE plugins (
    id SERIAL NOT NULL, 
    name VARCHAR(255) NOT NULL, 
    enabled BOOLEAN NOT NULL, 
    author VARCHAR(255), 
    version VARCHAR(255), 
    distro VARCHAR(255) NOT NULL, 
    CONSTRAINT plugins_pkey PRIMARY KEY (id), 
    CONSTRAINT plugins_name_key UNIQUE (name), 
    CONSTRAINT distro UNIQUE (distro)
);

CREATE TABLE tokens (
    id SERIAL NOT NULL, 
    username VARCHAR(255), 
    max_uses INTEGER, 
    total_uses INTEGER, 
    creation BIGINT NOT NULL, 
    not_before BIGINT, 
    expiration BIGINT, 
    disabled BOOLEAN, 
    token_hash VARCHAR(255) NOT NULL, 
    CONSTRAINT tokens_pkey PRIMARY KEY (id), 
    CONSTRAINT tokens_token_hash_key UNIQUE (token_hash), 
    CONSTRAINT tokens_username_key UNIQUE (username), 
    CONSTRAINT tokens_username_fkey FOREIGN KEY(username) REFERENCES "user" (username)
);

CREATE TABLE citrap (
    id VARCHAR(255) NOT NULL, 
    type VARCHAR(255), 
    title VARCHAR(255), 
    visible BOOLEAN, 
    delimiter VARCHAR(255), 
    user_callsign VARCHAR(255), 
    user_description VARCHAR(255), 
    date_time TIMESTAMP WITHOUT TIME ZONE, 
    date_time_description VARCHAR(255), 
    point_id INTEGER NOT NULL, 
    location_description VARCHAR(255), 
    event_scale VARCHAR(255), 
    importance VARCHAR(255), 
    tags VARCHAR(255), 
    scale_description VARCHAR(255), 
    status VARCHAR(255), 
    file_name VARCHAR(255), 
    hash VARCHAR(255), 
    CONSTRAINT citrap_pkey PRIMARY KEY (id), 
    CONSTRAINT citrap_hash_key UNIQUE (hash), 
    CONSTRAINT citrap_point_id_fkey FOREIGN KEY(point_id) REFERENCES points (id) ON DELETE CASCADE
);

CREATE INDEX ix_citrap_point_id ON citrap (point_id);

CREATE TABLE user_filters (
    id SERIAL NOT NULL, 
    name VARCHAR(255) NOT NULL, 
    CONSTRAINT user_filters_pkey PRIMARY KEY (id), 
    CONSTRAINT user_filters_name_key UNIQUE (name)
);

CREATE TABLE user_filters_members (
    user_id INTEGER NOT NULL, 
    filter_id INTEGER NOT NULL, 
    CONSTRAINT user_filters_members_pkey PRIMARY KEY (user_id, filter_id), 
    CONSTRAINT user_filters_members_user_id_fkey FOREIGN KEY(user_id) REFERENCES "user" (id), 
    CONSTRAINT user_filters_members_filter_id_fkey FOREIGN KEY(filter_id) REFERENCES user_filters (id)
);

CREATE TABLE supporting_documents (
    id SERIAL NOT NULL, 
    filename VARCHAR(255) NOT NULL, 
    stored_filename VARCHAR(255) NOT NULL, 
    mime_type VARCHAR(255), 
    size INTEGER NOT NULL, 
    uploaded_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
    uploaded_by_id INTEGER, 
    CONSTRAINT supporting_documents_pkey PRIMARY KEY (id), 
    CONSTRAINT supporting_documents_stored_filename_key UNIQUE (stored_filename), 
    CONSTRAINT supporting_documents_uploaded_by_id_fkey FOREIGN KEY(uploaded_by_id) REFERENCES "user" (id) ON DELETE SET NULL
);

CREATE TABLE data_package_recipients (
    id SERIAL NOT NULL, 
    data_package_hash VARCHAR(255) NOT NULL, 
    eud_uid VARCHAR(255), 
    callsign VARCHAR(255), 
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
    CONSTRAINT data_package_recipients_pkey PRIMARY KEY (id), 
    CONSTRAINT uq_data_package_recipient UNIQUE (data_package_hash, eud_uid, callsign), 
    CONSTRAINT data_package_recipients_data_package_hash_fkey FOREIGN KEY(data_package_hash) REFERENCES data_packages (hash) ON DELETE CASCADE
);

CREATE INDEX ix_data_package_recipients_hash ON data_package_recipients (data_package_hash);

CREATE TABLE pending_clear_commands (
    id SERIAL NOT NULL, 
    eud_uid VARCHAR(255) NOT NULL, 
    clearmaps BOOLEAN NOT NULL, 
    requested_by VARCHAR(255), 
    requested_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
    delivered_at TIMESTAMP WITHOUT TIME ZONE, 
    node_id VARCHAR(255), 
    CONSTRAINT pending_clear_commands_pkey PRIMARY KEY (id)
);

CREATE INDEX ix_pending_clear_commands_eud_uid ON pending_clear_commands (eud_uid);

CREATE TABLE audit_events (
    id SERIAL NOT NULL, 
    timestamp TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
    category VARCHAR(32) NOT NULL, 
    action VARCHAR(64) NOT NULL, 
    username VARCHAR(255), 
    actor VARCHAR(255), 
    ip VARCHAR(64), 
    user_agent VARCHAR(512), 
    success BOOLEAN NOT NULL, 
    details TEXT, 
    summary VARCHAR(255), 
    CONSTRAINT audit_events_pkey PRIMARY KEY (id)
);

CREATE INDEX ix_audit_events_timestamp ON audit_events (timestamp);

CREATE INDEX ix_audit_events_category ON audit_events (category);

CREATE INDEX ix_audit_events_action ON audit_events (action);

CREATE INDEX ix_audit_events_username ON audit_events (username);

CREATE TABLE device_profile_sets (
    id SERIAL NOT NULL, 
    name VARCHAR(255) NOT NULL, 
    description TEXT, 
    enabled BOOLEAN DEFAULT true NOT NULL, 
    apply_on_enrollment BOOLEAN DEFAULT true NOT NULL, 
    apply_on_connection BOOLEAN DEFAULT false NOT NULL, 
    target_everyone BOOLEAN DEFAULT false NOT NULL, 
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
    tool VARCHAR(255), 
    CONSTRAINT device_profile_sets_pkey PRIMARY KEY (id), 
    CONSTRAINT device_profile_sets_name_key UNIQUE (name)
);

CREATE INDEX ix_device_profile_sets_tool ON device_profile_sets (tool);

CREATE TABLE device_profile_targets (
    id SERIAL NOT NULL, 
    profile_id INTEGER NOT NULL, 
    kind VARCHAR(16) NOT NULL, 
    value VARCHAR(255) NOT NULL, 
    CONSTRAINT device_profile_targets_pkey PRIMARY KEY (id), 
    CONSTRAINT device_profile_targets_profile_id_fkey FOREIGN KEY(profile_id) REFERENCES device_profile_sets (id) ON DELETE CASCADE
);

CREATE INDEX ix_device_profile_targets_profile_id ON device_profile_targets (profile_id);

CREATE TABLE device_profile_settings (
    id SERIAL NOT NULL, 
    profile_id INTEGER NOT NULL, 
    key VARCHAR(255) NOT NULL, 
    value TEXT NOT NULL, 
    value_class VARCHAR(64) NOT NULL, 
    CONSTRAINT device_profile_settings_pkey PRIMARY KEY (id), 
    CONSTRAINT uq_device_profile_settings_profile_key UNIQUE (profile_id, key), 
    CONSTRAINT device_profile_settings_profile_id_fkey FOREIGN KEY(profile_id) REFERENCES device_profile_sets (id) ON DELETE CASCADE
);

CREATE INDEX ix_device_profile_settings_profile_id ON device_profile_settings (profile_id);

CREATE TABLE device_profile_files (
    id SERIAL NOT NULL, 
    profile_id INTEGER NOT NULL, 
    filename VARCHAR(255) NOT NULL, 
    zip_path VARCHAR(512) NOT NULL, 
    stored_name VARCHAR(64) NOT NULL, 
    size BIGINT NOT NULL, 
    sha256 VARCHAR(64) NOT NULL, 
    content_type VARCHAR(255), 
    uploaded_by VARCHAR(255), 
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
    CONSTRAINT device_profile_files_pkey PRIMARY KEY (id), 
    CONSTRAINT device_profile_files_stored_name_key UNIQUE (stored_name), 
    CONSTRAINT uq_device_profile_files_profile_path UNIQUE (profile_id, zip_path), 
    CONSTRAINT device_profile_files_profile_id_fkey FOREIGN KEY(profile_id) REFERENCES device_profile_sets (id) ON DELETE CASCADE
);

CREATE INDEX ix_device_profile_files_profile_id ON device_profile_files (profile_id);

CREATE TABLE data_packages_groups (
    data_package_id INTEGER NOT NULL, 
    group_id INTEGER NOT NULL, 
    CONSTRAINT data_packages_groups_pkey PRIMARY KEY (data_package_id, group_id), 
    CONSTRAINT data_packages_groups_data_package_id_fkey FOREIGN KEY(data_package_id) REFERENCES data_packages (id) ON DELETE CASCADE, 
    CONSTRAINT data_packages_groups_group_id_fkey FOREIGN KEY(group_id) REFERENCES groups (id) ON DELETE CASCADE
);

CREATE TABLE metric_counters (
    name VARCHAR(128) NOT NULL, 
    labels VARCHAR(255) NOT NULL, 
    kind VARCHAR(16) NOT NULL, 
    value BIGINT NOT NULL, 
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
    CONSTRAINT metric_counters_pkey PRIMARY KEY (name, labels)
);

CREATE TABLE metric_samples (
    id SERIAL NOT NULL, 
    timestamp TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
    cot_received BIGINT NOT NULL, 
    cot_sent BIGINT NOT NULL, 
    bytes_received BIGINT NOT NULL, 
    bytes_sent BIGINT NOT NULL, 
    euds_connected INTEGER NOT NULL, 
    CONSTRAINT metric_samples_pkey PRIMARY KEY (id)
);

CREATE INDEX ix_metric_samples_timestamp ON metric_samples (timestamp);

CREATE TABLE retention_settings (
    data_type VARCHAR(64) NOT NULL, 
    keep_seconds INTEGER, 
    customized BOOLEAN DEFAULT false NOT NULL, 
    updated_at TIMESTAMP WITHOUT TIME ZONE, 
    updated_by VARCHAR(255), 
    last_purge_at TIMESTAMP WITHOUT TIME ZONE, 
    last_purge_count INTEGER, 
    last_purge_error TEXT, 
    CONSTRAINT retention_settings_pkey PRIMARY KEY (data_type)
);

CREATE TABLE offline_chat_messages (
    id SERIAL NOT NULL, 
    recipient_uid VARCHAR(255) NOT NULL, 
    sender_uid VARCHAR(255), 
    chatroom_id VARCHAR(255), 
    message_uid VARCHAR(255) NOT NULL, 
    xml TEXT NOT NULL, 
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
    delivered_at TIMESTAMP WITHOUT TIME ZONE, 
    recipient_user_id INTEGER, 
    read_groups TEXT, 
    CONSTRAINT offline_chat_messages_pkey PRIMARY KEY (id), 
    CONSTRAINT uq_offline_chat_recipient_message UNIQUE (recipient_uid, message_uid)
);

CREATE INDEX ix_offline_chat_messages_recipient_uid ON offline_chat_messages (recipient_uid);

CREATE INDEX ix_offline_chat_messages_created_at ON offline_chat_messages (created_at);

CREATE TABLE mission_layers (
    uid VARCHAR(255) NOT NULL, 
    mission_name VARCHAR(255) NOT NULL, 
    name VARCHAR(255) NOT NULL, 
    type VARCHAR(32) NOT NULL, 
    parent_uid VARCHAR(255), 
    after VARCHAR(255), 
    uids JSON, 
    hashes JSON, 
    creator_uid VARCHAR(255), 
    create_time TIMESTAMP WITHOUT TIME ZONE, 
    CONSTRAINT mission_layers_pkey PRIMARY KEY (uid), 
    CONSTRAINT mission_layers_mission_name_fkey FOREIGN KEY(mission_name) REFERENCES missions (name) ON DELETE CASCADE, 
    CONSTRAINT mission_layers_parent_uid_fkey FOREIGN KEY(parent_uid) REFERENCES mission_layers (uid) ON DELETE CASCADE
);

CREATE INDEX ix_mission_layers_mission_name ON mission_layers (mission_name);

CREATE TABLE mission_external_data (
    id SERIAL NOT NULL, 
    uid VARCHAR(255) NOT NULL, 
    mission_name VARCHAR(255) NOT NULL, 
    name VARCHAR(255), 
    tool VARCHAR(255), 
    url_data TEXT, 
    url_view TEXT, 
    notes TEXT, 
    creator_uid VARCHAR(255), 
    create_time TIMESTAMP WITHOUT TIME ZONE, 
    CONSTRAINT mission_external_data_pkey PRIMARY KEY (id), 
    CONSTRAINT mission_external_data_mission_name_uid_key UNIQUE (mission_name, uid), 
    CONSTRAINT mission_external_data_mission_name_fkey FOREIGN KEY(mission_name) REFERENCES missions (name) ON DELETE CASCADE
);

CREATE TABLE mission_feeds (
    id SERIAL NOT NULL, 
    uid VARCHAR(255) NOT NULL, 
    mission_name VARCHAR(255) NOT NULL, 
    data_feed_uid VARCHAR(255) NOT NULL, 
    name VARCHAR(255), 
    filter_bbox VARCHAR(1024), 
    filter_type VARCHAR(1024), 
    filter_callsign VARCHAR(255), 
    creator_uid VARCHAR(255), 
    create_time TIMESTAMP WITHOUT TIME ZONE, 
    CONSTRAINT mission_feeds_pkey PRIMARY KEY (id), 
    CONSTRAINT mission_feeds_uid_key UNIQUE (uid), 
    CONSTRAINT mission_feeds_mission_name_fkey FOREIGN KEY(mission_name) REFERENCES missions (name) ON DELETE CASCADE
);

CREATE TABLE enrollment_blocks (
    eud_uid VARCHAR(255) NOT NULL, 
    username VARCHAR(255), 
    serial_number VARCHAR(64), 
    reason VARCHAR(64), 
    blocked_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
    blocked_by VARCHAR(255), 
    CONSTRAINT enrollment_blocks_pkey PRIMARY KEY (eud_uid)
);

CREATE INDEX ix_enrollment_blocks_username ON enrollment_blocks (username);

