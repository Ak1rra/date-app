import axe from 'axe-core';

/**
 * Fails when axe finds an accessibility violation inside `container`. jsdom can't compute colours,
 * and some tests render a page without the shell's landmarks, so those two rules are off.
 */
export async function expectNoAxeViolations(container: Element) {
  const { violations } = await axe.run(container, {
    rules: { 'color-contrast': { enabled: false }, region: { enabled: false } },
  });
  if (violations.length === 0) return;
  const report = violations
    .map((violation) => {
      const nodes = violation.nodes.map((node) => `    ${node.target.join(' ')}`).join('\n');
      return `${violation.id}: ${violation.help}\n${nodes}`;
    })
    .join('\n\n');
  throw new Error(`${violations.length} accessibility violation(s):\n\n${report}`);
}
