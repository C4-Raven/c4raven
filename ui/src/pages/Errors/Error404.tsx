import { Button, Text, Title } from '@mantine/core';
import { Home20Regular } from '@fluentui/react-icons';
import classes from './Error404.module.css';

/** Not-found page: `/404` and any unmatched path inside the app shell. */
export default function Error404() {
    return (
        <div className={classes.root}>
            <div className={classes.code}>404</div>
            <Title order={1} className={classes.title}>
                Nothing to see here
            </Title>
            <Text c="dimmed" className={classes.description}>
                Page you are trying to open does not exist. You may have mistyped the address, or the page has been
                moved to another URL. If you think this is an error contact support.
            </Text>
            {/* A plain anchor on purpose: a full page load back to the app root. */}
            <Button component="a" href="/" leftSection={<Home20Regular />}>
                Take me back to home page
            </Button>
        </div>
    );
}
