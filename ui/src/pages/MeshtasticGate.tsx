import { useEffect, useState } from 'react';
import { Alert, Button, Center, Loader, Text } from '@mantine/core';
import { ArrowClockwise20Regular, Warning20Regular } from '@fluentui/react-icons';
import { t } from 'i18next';
import { lazyPage } from '../lazyPage';
import { loadFeatures, type Features } from '../features';
import { MeshtasticOffNotice } from './MeshtasticOffNotice';

const MeshtasticPage = lazyPage(() => import('./Meshtastic.tsx'));

/**
 * Meshtastic is off unless the server says otherwise, so the page (and every /api/meshtastic
 * request) waits for a fresh answer from /api/features instead of trusting a cached one.
 */
export default function MeshtasticGate() {
    // undefined: still asking; null: the question failed
    const [features, setFeatures] = useState<Features | null | undefined>(undefined);
    const [attempt, setAttempt] = useState(0);

    useEffect(() => {
        let active = true;
        loadFeatures().then((fresh) => {
            if (active) {setFeatures(fresh);}
        });
        return () => {
            active = false;
        };
    }, [attempt]);

    if (features === undefined) {
        return (
            <Center h={200}>
                <Loader size="sm" />
            </Center>
        );
    }
    if (features === null) {
        return (
            <Alert variant="light" color="red" icon={<Warning20Regular />} title={t('Could not reach the server')}>
                <Text size="sm" mb="sm">{t('The page could not check whether Meshtastic is turned on.')}</Text>
                <Button
                    size="xs"
                    variant="default"
                    leftSection={<ArrowClockwise20Regular />}
                    onClick={() => {
                        setFeatures(undefined);
                        setAttempt((a) => a + 1);
                    }}
                >
                    {t('Try again')}
                </Button>
            </Alert>
        );
    }
    if (!features.meshtastic) {
        return <MeshtasticOffNotice />;
    }
    return <MeshtasticPage />;
}
