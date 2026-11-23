// Backend endpoints, grouped by area. Keys are camelCase, `<area><Thing>`:
// a bare `...List` is the collection endpoint, verbs come last (`userDelete`).

export const apiRoutes = {
  // Sign-in and the signed-in session
  authLogin: '/api/login?include_auth_token',
  authLdapLogin: '/api/ldap_login',
  authLogout: '/api/logout',
  authMe: '/api/me',
  authTotpSetup: '/api/tf-setup',
  authTotpValidate: '/api/tf-validate',
  authTurnstile: '/api/turnstile',
  /** The signed-in user changes their own password. */
  authChangePassword: '/api/password/change',

  // Server state and settings
  serverStatus: '/api/status',
  serverMetricsSummary: '/api/metrics/summary',
  serverFeatures: '/api/features',
  serverConfig: '/api/config',
  serverAddresses: '/api/server_addresses',
  serverRetention: '/api/retention',
  serverSslStart: '/api/ssl/start',
  serverSslStop: '/api/ssl/stop',
  serverTcpStart: '/api/tcp/start',
  serverTcpStop: '/api/tcp/stop',

  // Security audit and logs
  securityAudit: '/api/security/audit',
  securityAuditDownload: '/api/security/audit/download',
  securityEvents: '/api/security/audit/events',
  securityEventsDownload: '/api/security/audit/events/download',
  logsErrors: '/api/logs/errors',
  logsErrorsDownload: '/api/logs/errors/download',

  // Scheduler
  schedulerJobs: '/api/scheduler/jobs',
  schedulerRun: '/api/scheduler/job/run',
  schedulerPause: '/api/scheduler/job/pause',
  schedulerResume: '/api/scheduler/job/resume',
  schedulerSchedule: '/api/scheduler/job/schedule',
  schedulerCancel: '/api/scheduler/job/cancel',

  // Users
  userList: '/api/users',
  userListAll: '/api/users/all',
  userCreate: '/api/user/create',
  userAdd: '/api/user/add',
  userDelete: '/api/user/delete',
  userActivate: '/api/user/activate',
  userDeactivate: '/api/user/deactivate',
  userRole: '/api/user/role',
  userAdminView: '/api/user/admin_view',
  userSiteAccessGrant: '/api/user/site_access/grant',
  userSiteAccessRevoke: '/api/user/site_access/revoke',
  userAssignEud: '/api/user/assign_eud',
  userSendFile: '/api/user/send_file',
  userClearContent: '/api/user/clear_content',
  /** An administrator sets any user's password. */
  userPasswordReset: '/api/user/password/reset',
  /** Flags a user to pick a new password at their next login. */
  userForcePasswordReset: '/api/user/force_password_reset',
  /** Temporary password for a user who forgot theirs. */
  userTempPassword: '/api/user/issue_temp_password',
  userVisibility: '/api/users/visibility',
  userGroups: '/api/users/groups',
  userFilters: '/api/users/filters',
  userCertificateValidity: '/api/users/certificate_validity',

  // Groups
  groupList: '/api/groups',
  groupListAll: '/api/groups/all',
  groupMembers: '/api/groups/members',

  // Certificates and device onboarding
  certCreate: '/api/certificate',
  certList: '/api/certificates',
  certRevoke: (id: number) => `/api/certificates/${id}/revoke`,
  certRevokeSerial: '/api/certificates/revoke',
  certRevokeUser: '/api/certificates/revoke_user',
  certAllowEnrollment: '/api/certificates/allow_enrollment',
  certTruststore: '/api/truststore',
  qrAtak: '/api/atak_qr_string',
  qrItak: '/api/itak_qr_string',
  qrVpn: '/api/vpn_qr_string',

  // Devices, map and casevac
  eudList: '/api/eud',
  mapMarkers: '/api/markers',
  mapState: '/api/map_state',
  casevacList: '/api/casevac',

  // Data packages and supporting documents
  dataPackageList: '/api/data_packages',
  dataPackageDelete: '/api/data_packages',
  dataPackageDownload: '/api/data_packages/download',
  dataPackageGroups: '/api/data_packages/groups',
  supportingDocuments: '/api/supporting_documents',

  // Missions
  missionList: '/api/missions',
  missionDetail: '/api/missions/detail',
  missionInvite: '/api/missions/invite',
  missionArchive: '/api/missions/archive',
  missionKml: '/api/missions/kml',

  // Video
  videoStreamList: '/api/video_streams',
  videoStreamAdd: '/api/mediamtx/stream/add',
  videoStreamUpdate: '/api/mediamtx/stream/update',
  videoStreamDelete: '/api/mediamtx/stream/delete',
  videoRecording: '/api/videos/recording',
  videoRecordingDelete: '/api/videos/recording',
  videoRecordingList: '/api/videos/recordings',

  // Meshtastic
  meshtasticChannels: '/api/meshtastic/channel',
  meshtasticPsk: '/api/meshtastic/generate_psk',

  // Plugins and device profiles
  pluginPackages: '/api/packages',
  pluginRepo: '/api/plugins/repo',
  pluginRavenClearApk: '/api/plugins/ravenclear/apk',
  deviceProfileEntries: '/api/profiles',
  deviceProfileSets: '/api/device-profiles',
  deviceProfilePreview: '/api/device-profiles/preview',
  deviceProfileImport: '/api/device-profiles/import',
  deviceProfileExtras: '/api/device-profiles/extras',

  // TAK.gov account link
  takgov: '/api/takgov',
  takgovLink: '/api/takgov/link',
  takgovToken: '/api/takgov/token',
  takgovPlugins: '/api/takgov/plugins',
  takgovProducts: '/api/takgov/products',
  takgovPlugin: '/api/takgov/plugin',
  takgovIcon: '/api/takgov/icon',

  // Federation hub
  fedhubOverview: '/api/fedhub/overview',
  fedhubConfig: '/api/fedhub/config',
  fedhubConnections: '/api/fedhub/connections',
  fedhubMetrics: '/api/fedhub/metrics',
  fedhubFederations: '/api/fedhub/federations',
  fedhubPartners: '/api/fedhub/partners',
  fedhubPolicy: '/api/fedhub/policy',
  fedhubPlugins: '/api/fedhub/plugins',
  fedhubKnownGroups: '/api/fedhub/known_groups',
  fedhubTokens: '/api/fedhub/tokens',
  fedhubHandoff: '/api/fedhub/handoff',
  fedhubRepair: '/api/fedhub/repair',
  fedhubReachability: '/api/fedhub/reachability',
  fedhubNativeUrl: '/api/fedhub/native_url',
  fedhubRestartBroker: '/api/fedhub/broker/restart',
  fedhubCaGroups: '/api/fedhub/ca_groups',
  fedhubCaInspect: '/api/fedhub/ca_inspect',
  fedhubSelfCa: '/api/fedhub/ca_self',
  fedhubAdminCert: '/api/fedhub/admin_cert',
  fedhubAdminCertPassword: '/api/fedhub/admin_cert/password',
  fedhubAdminCertVerify2fa: '/api/fedhub/admin_cert/verify_2fa',
};
