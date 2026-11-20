// Renders every PNG icon from public/icon.svg (the single source).
// Run with `npm run gen:icons`; the outputs are committed.
import { readFile, writeFile } from 'node:fs/promises';
import sharp from 'sharp';

const svg = await readFile(new URL('../public/icon.svg', import.meta.url));
const out = (name) => new URL(`../public/${name}`, import.meta.url);

// "any" icons get rounded corners; the artwork sits inside the maskable safe zone,
// so the full-bleed render doubles as the maskable icon.
async function rounded(size) {
  const mask = Buffer.from(
    `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}"><rect width="${size}" height="${size}" rx="${size * 0.22}"/></svg>`,
  );
  return sharp(svg, { density: 384 })
    .resize(size, size)
    .composite([{ input: mask, blend: 'dest-in' }])
    .png()
    .toBuffer();
}
const square = (size) => sharp(svg, { density: 384 }).resize(size, size).png().toBuffer();

await writeFile(out('icon-192.png'), await rounded(192));
await writeFile(out('icon-512.png'), await rounded(512));
await writeFile(out('icon-maskable-512.png'), await square(512));
await writeFile(out('apple-touch-icon.png'), await square(180));
await writeFile(out('favicon-32.png'), await rounded(32));
console.log('icons written');
