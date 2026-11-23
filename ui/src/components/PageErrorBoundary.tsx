import { Component, type ErrorInfo, type ReactNode } from 'react';
import { Box, Button, Text, Title } from '@mantine/core';
import { ArrowClockwise20Regular, ErrorCircle20Regular } from '@fluentui/react-icons';
import { t } from 'i18next';

interface Props {
  children: ReactNode;
}

interface State {
  failed: boolean;
}

/**
 * Catches a crash while rendering a page, so the shell (header, navigation) stays
 * usable and the user gets a reload button instead of a blank screen.
 */
export class PageErrorBoundary extends Component<Props, State> {
  state: State = { failed: false };

  static getDerivedStateFromError(): State {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // eslint-disable-next-line no-console
    console.error('Page crashed', error, info.componentStack);
  }

  render() {
    if (!this.state.failed) {
      return this.props.children;
    }
    return (
      <Box py={80} px="md" ta="center" maw={520} mx="auto" role="alert">
        <ErrorCircle20Regular style={{ width: 40, height: 40, opacity: 0.6 }} aria-hidden />
        <Title order={2} mt="md" mb="xs">
          {t('Something went wrong on this page')}
        </Title>
        <Text c="dimmed" mb="lg">
          {t('Reload the page to try again.')}
        </Text>
        <Button
          variant="default"
          leftSection={<ArrowClockwise20Regular />}
          onClick={() => window.location.reload()}
        >
          {t('Reload')}
        </Button>
      </Box>
    );
  }
}

export default PageErrorBoundary;
