import { Alert, Center, Loader } from '@mantine/core';
import { Map20Regular } from '@fluentui/react-icons';
import { t } from 'i18next';
import { lazyPage } from '../../lazyPage';
import { useFeatures } from '../../features';

const MapPage = lazyPage(() => import('./Map.tsx'));

/**
 * The map pulls its layers from public tile servers, so it only loads once the server has said
 * it is turned on: with RAVEN_MAP_ENABLE off, neither the map code nor any tile is fetched.
 */
export default function MapGate() {
  const features = useFeatures();
  if (features === null) {
    return (
      <Center h={200}>
        <Loader size="sm" />
      </Center>
    );
  }
  if (!features.map) {
    return (
      <Alert variant="light" color="gray" icon={<Map20Regular />} title={t('Map turned off')}>
        {t(
          'The map is turned off on this server, so this browser does not contact any public map tile servers. An administrator can turn it on with RAVEN_MAP_ENABLE in config.yml.'
        )}
      </Alert>
    );
  }
  return <MapPage />;
}
