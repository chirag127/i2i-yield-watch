// scraper/test/verify_syntax.js
// Quick syntax-only check for every JS file in
// the scraper project. Run with:
//   node scraper/test/verify_syntax.js
// Exits 0 if all files parse, 1 otherwise.
//
// Walks the new SOLID folder structure:
//   scraper/src/core/      scraper/src/notifiers/
//   scraper/src/utils/     scraper/src/browser/

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.join(__dirname, '..');
const DIRS = [
  path.join(ROOT, 'src', 'core'),
  path.join(ROOT, 'src', 'notifiers'),
  path.join(ROOT, 'src', 'utils'),
  path.join(ROOT, 'src', 'browser'),
];
const SKIP = new Set(['verify_syntax.js']);

let ok = 0;
let bad = 0;

for (const dir of DIRS) {
  if (!fs.existsSync(dir)) continue;
  const files = fs.readdirSync(dir)
    .filter((f) => f.endsWith('.js') && !SKIP.has(f));
  for (const file of files) {
    const full = path.join(dir, file);
    try {
      const src = fs.readFileSync(full, 'utf-8');
      new vm.Script(src, { filename: file });
      const rel = path.relative(ROOT, full)
        .replace(/\\/g, '/');
      console.log(`OK   ${rel}`);
      ok++;
    } catch (err) {
      const rel = path.relative(ROOT, full)
        .replace(/\\/g, '/');
      console.log(`FAIL ${rel}: ${err.message}`);
      bad++;
    }
  }
}

console.log(`\n${ok} ok, ${bad} failed`);
process.exit(bad === 0 ? 0 : 1);
