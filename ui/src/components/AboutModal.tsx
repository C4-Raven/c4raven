import { Anchor, List, Modal, Stack, Text } from '@mantine/core';
import { t } from 'i18next';
import versions from '../_versions';

// Summary of the "ORIGINAL C4 RAVEN WORK" sections of the server and UI
// NOTICE files, which list the exact files. Keep the three in step.
const ORIGINAL_WORK = [
  'Federation Hub bridge and its admin pages',
  'Security audit log, login lockout, Turnstile, read-only admin role',
  'Certificate revocation, validity checks and the certificates API',
  'Group isolation and CoT sharing rules',
  'VPN onboarding (QR enrolment)',
  'Metrics, data retention and error logs',
  'Offline chat',
  'Remote Clear',
  'Mission detail and export',
  'Device profiles and server requests pages',
  'Optional Meshtastic and map features',
  'Raven UI component kit, styling and logo',
];

// GPLv3 section 5(d) "Appropriate Legal Notices": copyright, no-warranty
// statement, the licence and how to get the source. Keep this wording in
// step with the NOTICE files in c4raven-server and c4raven-ui.
export default function AboutModal({ opened, onClose }: { opened: boolean; onClose: () => void }) {
  return (
    <Modal opened={opened} onClose={onClose} title={t('About Raven')} size="md">
      <Stack gap="sm">
        <Text size="sm" fw={600}>
          Raven TAK Server{versions.gitCommitHash ? ` (${versions.gitCommitHash})` : ''}
        </Text>
        <Text size="sm">Copyright © 2026 C4 Raven.</Text>
        <Text size="sm">
          {t(
            'Raven is free software: you can redistribute it and/or modify it under the terms of the'
          )}{' '}
          <Anchor
            href="https://www.gnu.org/licenses/gpl-3.0.html"
            target="_blank"
            rel="noopener noreferrer"
          >
            GNU General Public License v3.0
          </Anchor>{' '}
          {t('or any later version. It comes with ABSOLUTELY NO WARRANTY.')}
        </Text>
        <Text size="sm">
          {t('Raven is built on')}{' '}
          <Anchor
            href="https://github.com/brian7704/OpenTAKServer"
            target="_blank"
            rel="noopener noreferrer"
          >
            OpenTAKServer
          </Anchor>{' '}
          {t('and')}{' '}
          <Anchor
            href="https://github.com/brian7704/OpenTAKServer-UI"
            target="_blank"
            rel="noopener noreferrer"
          >
            OpenTAKServer-UI
          </Anchor>{' '}
          {t(
            'by Brian Wallen and the OpenTAKServer contributors (GPL-3.0). Their work gave Raven its foundation, and we are grateful for it. C4 Raven has since extended and reworked it substantially.'
          )}
        </Text>
        <div>
          <Text size="sm" fw={600}>
            {t('Added by C4 Raven')}
          </Text>
          <Text size="sm">
            {t(
              'Building on that foundation, C4 Raven wrote these parts from scratch (copyright C4 Raven):'
            )}
          </Text>
          <List size="sm" spacing={2} mt={4}>
            {ORIGINAL_WORK.map((item) => (
              <List.Item key={item}>{t(item)}</List.Item>
            ))}
          </List>
          <Text size="xs" c="dimmed" mt={4}>
            {t('The NOTICE file in the source lists the exact files.')}
          </Text>
        </div>
        <Text size="sm" c="dimmed">
          {t(
            'The source code for this installation is available from your server administrator, and is included in every Raven install bundle.'
          )}
        </Text>
      </Stack>
    </Modal>
  );
}
