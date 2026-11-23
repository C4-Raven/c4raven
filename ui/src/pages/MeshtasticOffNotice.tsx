import { Alert, Text } from '@mantine/core';
import { Router20Regular } from '@fluentui/react-icons';
import { t } from 'i18next';

/** What the page shows instead of itself when the server has Meshtastic turned off. */
export function MeshtasticOffNotice() {
    return (
        <Alert variant="light" color="gray" icon={<Router20Regular />} title={t('Meshtastic is turned off on this server')}>
            <Text size="sm">
                {t('An administrator turns it on with RAVEN_ENABLE_MESHTASTIC in config.yml (or by answering yes to the Meshtastic question in the installer), then restarts the server.')}
            </Text>
        </Alert>
    );
}
