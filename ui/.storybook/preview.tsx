import '@mantine/core/styles.css';
import { useEffect, type ReactNode } from 'react';
import { addons } from 'storybook/preview-api';
import { DARK_MODE_EVENT_NAME } from 'storybook-dark-mode';
import { MantineProvider, useMantineColorScheme } from '@mantine/core';
import { theme } from '../src/theme';

const channel = addons.getChannel();

// Follows the dark-mode toolbar toggle.
function ColorSchemeWrapper({ children }: { children: ReactNode }) {
  const { setColorScheme } = useMantineColorScheme();

  useEffect(() => {
    const onDarkMode = (value: boolean) => setColorScheme(value ? 'dark' : 'light');
    channel.on(DARK_MODE_EVENT_NAME, onDarkMode);
    return () => channel.off(DARK_MODE_EVENT_NAME, onDarkMode);
  }, [setColorScheme]);

  return <>{children}</>;
}

export const decorators = [
  (renderStory: () => ReactNode) => <ColorSchemeWrapper>{renderStory()}</ColorSchemeWrapper>,
  (renderStory: () => ReactNode) => (
    <MantineProvider theme={theme}>{renderStory()}</MantineProvider>
  ),
];
