import { compile } from 'svelte/compiler';
import fs from 'node:fs';

const files = process.argv.slice(2);

for (const f of files) {
  const source = fs.readFileSync(f, 'utf8');
  try {
    const result = compile(source, { filename: f, generate: 'client' });
    const warnings = result.warnings.filter((w) => w.code !== 'css_unused_selector');
    console.log('OK  ' + f);
    for (const w of warnings) {
      console.log('  warn ' + w.code + ': ' + w.message + (w.start ? ` (line ${w.start.line})` : ''));
    }
  } catch (e) {
    console.log('FAIL ' + f);
    console.log('  ' + (e.message || String(e)).split('\n').slice(0, 6).join('\n  '));
  }
}
