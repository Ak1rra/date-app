import { expect, test, type Page } from '@playwright/test';
import {
  localDateTime,
  schedulePastDate,
  signIn,
  switchProfile,
  waitForCompleted,
} from './helpers';

// The two paths NFR-QA-1 asks for: the solo planner, then the swipe-and-match flow. Serial: the
// second test reuses the household (names, home area) the first one sets up, though it also
// runs alone, since signIn() does the setup wizard whenever the app opens it. Every test gets a
// fresh browser context, so each one picks its profile again.
test.describe.serial('smoke', () => {
  test('solo planner: profiles, plan, schedule, complete, planner read', async ({ page }) => {
    await signIn(page, 'Husband');

    // Both profiles: my own, and the partner profile the planner maintains.
    await page.goto('/us');
    const mine = page.getByRole('region', { name: 'You', exact: true });
    await mine.getByRole('button', { name: 'Edit my profile' }).click();
    await mine.getByLabel('Loves', { exact: true }).fill('Long walks and good coffee');
    await mine.getByRole('button', { name: 'Save profile' }).click();
    await expect(mine.getByText('Long walks and good coffee')).toBeVisible();

    const partner = page.getByRole('region', { name: /^Here's what you know about / });
    await partner.getByRole('button', { name: /^Edit .+'s profile$/ }).click();
    await partner.getByLabel('Loves', { exact: true }).fill('Live music and dessert');
    await partner.getByRole('button', { name: 'Save profile' }).click();
    await expect(partner.getByText('Live music and dessert')).toBeVisible();

    // Plan a surprise: options, pick the first, build.
    await page.goto('/plan');
    const tomorrow = (await localDateTime(page, 24)).slice(0, 10);
    await page.getByLabel('Date', { exact: true }).fill(tomorrow);
    await page.getByRole('button', { name: 'Make it a surprise' }).click();
    await page.getByRole('button', { name: 'Show me 3 options' }).click();
    await page
      .getByRole('checkbox', { name: /^Select / })
      .first()
      .check();
    await page.getByRole('button', { name: 'Build this date' }).click();
    await page.waitForURL(/\/itineraries\/[^/]+$/, { timeout: 45_000 });

    // Schedule it in the past so the worker completes it at once.
    await schedulePastDate(page, 'Surprise');
    await waitForCompleted(page);

    // The planner's private read of how it went.
    await page.getByLabel('Enjoyment 4 of 5').check({ force: true });
    await page.getByLabel('What they loved').fill('The dessert stop');
    await page.getByRole('button', { name: 'Save my read' }).click();
    await expect(page.getByText('Enjoyment:')).toBeVisible();
    await expect(page.getByText('4/5')).toBeVisible();
  });

  test('match: both swipe yes, make it a date, both rate', async ({ page }) => {
    // The partner only joins the couple once her profile has been picked, and until then the
    // deck stays empty and "Invite your partner" shows. So pick Wife first, then Husband.
    await signIn(page, 'Wife');
    await switchProfile(page, 'Husband');

    // Husband generates a deck for both of them and says yes to every card.
    await page.goto('/ideas');
    await page.getByRole('button', { name: 'Generate ideas', exact: true }).first().click();
    const sheet = page.getByRole('dialog', { name: 'Generate ideas' });
    await sheet.getByRole('button', { name: 'For us to swipe' }).click();
    await sheet.getByRole('button', { name: 'Generate ideas', exact: true }).click();
    const done = sheet.getByRole('button', { name: 'Done' });
    await expect(async () => {
      if (await done.isVisible()) await done.click();
      await expect(sheet).toBeHidden({ timeout: 1_000 });
    }).toPass({ timeout: 40_000 });

    const yes = page.getByRole('button', { name: 'Yes please', exact: true });
    // The deck refetches after generating; wait for the new cards rather than read a stale empty deck.
    await expect.poll(() => cardsLeft(page), { timeout: 15_000 }).toBeGreaterThan(0);
    let left = await cardsLeft(page);
    while (left > 0) {
      const before = left;
      await yes.click();
      await expect.poll(() => cardsLeft(page), { timeout: 15_000 }).toBeLessThan(before);
      left = await cardsLeft(page);
    }

    // Wife says yes until one of them matches.
    await switchProfile(page, 'Wife');
    await page.goto('/ideas');
    const celebration = page.getByRole('dialog', { name: /It's a match/ });
    left = await cardsLeft(page);
    for (let i = 0; i < 12 && left > 0; i++) {
      await yes.click();
      const matched = await celebration
        .waitFor({ state: 'visible', timeout: 3_000 })
        .then(() => true)
        .catch(() => false);
      if (matched) break;
      left = await cardsLeft(page);
    }
    await expect(celebration).toBeVisible();
    const title = (await celebration.locator('strong').textContent()) ?? '';
    expect(title).not.toBe('');
    await celebration.getByRole('button', { name: 'Keep swiping' }).click();

    // Turn the match into a date and schedule it in the past.
    await page.getByRole('tab', { name: /^Matches/ }).click();
    await page.getByRole('button', { name: `Make ${title} a date` }).click();
    const makeDate = page.getByRole('dialog', { name: 'Make it a date' });
    await makeDate.getByRole('button', { name: 'Make it a date', exact: true }).click();
    await page.waitForURL(/\/itineraries\/[^/]+$/, { timeout: 45_000 });
    const itineraryUrl = page.url();
    await schedulePastDate(page, 'Announce');
    await waitForCompleted(page);

    // Wife rates 5 stars, then Husband rates 4.
    await rate(page, 5);
    await switchProfile(page, 'Husband');
    await page.goto(itineraryUrl);
    await expect(page.getByRole('heading', { name: 'How it went' })).toBeVisible();
    await rate(page, 4);

    // Both ratings are in, so the partner's is revealed.
    await expect(page.getByText(/Hidden until you've both rated/)).toBeHidden();
    await expect(page.getByRole('img', { name: '4 out of 5 stars' })).toBeVisible();
    await expect(page.getByRole('img', { name: '5 out of 5 stars' })).toBeVisible();
  });
});

/** How many cards are still in the deck (0 once "You're all caught up" shows). */
async function cardsLeft(page: Page): Promise<number> {
  const status = page
    .getByText(/ideas waiting for your verdict|Last one for now|You're all caught up/)
    .first();
  const text = (await status.textContent({ timeout: 10_000 })) ?? '';
  const waiting = /^(\d+) ideas waiting/.exec(text);
  if (waiting) return Number(waiting[1]);
  return text.startsWith('Last one') ? 1 : 0;
}

/** Rate a completed date from its page: stars, "Yes, again", save. */
async function rate(page: Page, stars: number) {
  await page.getByRole('button', { name: 'Rate this date' }).click();
  const sheet = page.getByRole('dialog', { name: 'How was your date?' });
  await sheet.getByLabel(`${stars} stars`, { exact: true }).check({ force: true });
  await sheet.getByRole('button', { name: 'Yes, again' }).click();
  await sheet.getByRole('button', { name: 'Save rating' }).click();
  await expect(sheet).toBeHidden();
}
