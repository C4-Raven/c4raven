/**
 * Raven UI is English only. Every t() key is the English source string, so a
 * key missing from the catalogue still renders correctly as itself. The
 * catalogue (public/locales/en/translation.json) is kept in step with the
 * source by `yarn i18n:extract` (see i18next.config.ts).
 *
 * Keys are whole sentences that may contain "." and ":", so key and namespace
 * separators are switched off.
 */
import i18next from 'i18next';
import HttpBackend from 'i18next-http-backend';
import { initReactI18next } from 'react-i18next';

export const UI_LANGUAGE = 'en';

void i18next
  .use(HttpBackend)
  .use(initReactI18next)
  .init({
    lng: UI_LANGUAGE,
    fallbackLng: UI_LANGUAGE,
    supportedLngs: [UI_LANGUAGE],
    ns: ['translation'],
    defaultNS: 'translation',
    keySeparator: false,
    nsSeparator: false,
    returnEmptyString: false,
    backend: { loadPath: `/locales/${UI_LANGUAGE}/translation.json` },
  });

export default i18next;
