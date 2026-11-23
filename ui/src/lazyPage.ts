import { lazy, type ComponentType } from 'react';

/**
 * React.lazy for a page module's default export. With "module": "NodeNext" and no
 * "type": "module", tsc models a dynamic import of a .tsx file as CommonJS interop
 * and mistypes `default`; Vite loads it as ESM, where `default` is the component.
 */
export function lazyPage(load: () => Promise<unknown>) {
  return lazy(load as () => Promise<{ default: ComponentType }>);
}
