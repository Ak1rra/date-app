import { useEffect } from 'react';

export type PersonTheme = 'husband' | 'wife';

/** Session role -> person: the owner is the husband, the partner is the wife. */
export function personForRole(role: string | undefined): PersonTheme | null {
  return role === 'owner' ? 'husband' : role === 'partner' ? 'wife' : null;
}

// Browser-chrome colours per palette; the wife/default values match index.html.
const CANVAS = {
  husband: { light: '#eef4ff', dark: '#0f1726' },
  default: { light: '#FFF4EE', dark: '#1F1221' },
};

/**
 * Sets data-person on <html> (the husband gets the blue palette in styles.css; the wife and the
 * signed-out picker keep the default warm one) and keeps the theme-color meta tags in step.
 */
export function usePersonTheme(role: string | undefined) {
  const person = personForRole(role);
  useEffect(() => {
    const root = document.documentElement;
    if (person) root.dataset.person = person;
    else delete root.dataset.person;
    const colors = person === 'husband' ? CANVAS.husband : CANVAS.default;
    document.querySelectorAll<HTMLMetaElement>('meta[name="theme-color"]').forEach((meta) => {
      meta.content = (meta.getAttribute('media') ?? '').includes('dark')
        ? colors.dark
        : colors.light;
    });
  }, [person]);
}
