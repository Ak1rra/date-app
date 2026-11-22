import { expect, type Page } from '@playwright/test';

export type Profile = 'Husband' | 'Wife';

const HUSBAND_NAME = 'Hugo';
const WIFE_NAME = 'Wendy';

/** "YYYY-MM-DDTHH:mm" in the browser's timezone, `hours` from now (what datetime-local wants). */
export async function localDateTime(page: Page, hours: number): Promise<string> {
  return page.evaluate((offset) => {
    const d = new Date(Date.now() + offset * 3_600_000);
    const pad = (n: number) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }, hours);
}

/** The first-run wizard: both names, then a home area. */
export async function completeSetup(page: Page, myName: string, partnerName: string) {
  await expect(page.getByRole('heading', { name: "Let's get you set up" })).toBeVisible();
  await page.getByLabel('Your first name').fill(myName);
  await page.getByRole('button', { name: 'Next' }).click();
  // Only a profile that may edit the partner's name (the husband's) gets this field; the wife
  // sees a note that her partner adds their own.
  const partnerField = page.getByLabel("Your partner's first name");
  await expect(partnerField.or(page.getByText(/keeps their own profile/))).toBeVisible();
  if (await partnerField.isVisible()) await partnerField.fill(partnerName);
  await page.getByRole('button', { name: 'Next' }).click();
  await page.getByLabel('Home area').fill('Downtown Austin');
  await page.getByRole('button', { name: 'Finish' }).click();
  await expect(page).not.toHaveURL(/\/setup/);
}

/** From a fresh browser context: pick a profile on /signin, doing the setup wizard if it opens. */
export async function signIn(page: Page, profile: Profile) {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: "Who's using Date Planner?" })).toBeVisible();
  await page.getByRole('button', { name: profile }).click();
  await expect(page).not.toHaveURL(/\/signin/);
  if (new URL(page.url()).pathname === '/setup') {
    await completeSetup(
      page,
      profile === 'Husband' ? HUSBAND_NAME : WIFE_NAME,
      profile === 'Husband' ? WIFE_NAME : HUSBAND_NAME,
    );
  }
}

/** Switch the active profile from /profile (no-login mode). */
export async function switchProfile(page: Page, profile: Profile) {
  await page.goto('/profile');
  const button = page
    .getByRole('group', { name: 'Switch profile' })
    .getByRole('button', { name: profile });
  await button.click();
  await expect(button).toHaveAttribute('aria-pressed', 'true');
}

/** Press Schedule on an itinerary, set a window in the past, and confirm. The worker then completes it. */
export async function schedulePastDate(page: Page, mode: 'Announce' | 'Surprise') {
  await page.getByRole('button', { name: 'Schedule', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Schedule your date' });
  await dialog.getByLabel('Starts').fill(await localDateTime(page, -4));
  await dialog.getByLabel('Ends (optional)').fill(await localDateTime(page, -1));
  await dialog.getByRole('button', { name: mode, exact: true }).click();
  await expect(dialog.getByRole('button', { name: mode, exact: true })).toHaveAttribute(
    'aria-pressed',
    'true',
  );
  await dialog.getByRole('button', { name: 'Schedule date' }).click();
  await expect(dialog).toBeHidden();
}

/** The in-process worker completes a past date within a few ticks; reload until it shows. */
export async function waitForCompleted(page: Page) {
  await expect(async () => {
    await page.reload();
    await expect(page.getByRole('heading', { name: 'How it went' })).toBeVisible({
      timeout: 2_000,
    });
  }).toPass({ timeout: 40_000, intervals: [1_000] });
}
