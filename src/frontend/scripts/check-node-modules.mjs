#!/usr/bin/env node
// Guardrail for the frozen dependency install.
//
// Fails loudly if node_modules regresses to the pre-cleanup bloat, i.e. if:
//   1. the tree grows past LIMIT_GB (see below), or
//   2. any Adobe React Spectrum package reappears (@adobe/react-spectrum,
//      @react-spectrum/*, @spectrum-icons/*). These are dragged in only by
//      mispackaged @react-types/* deps and are removed via package.json
//      "overrides" — this check is what keeps them removed.
//
// Only relevant at *freeze* time, when the lockfile can actually change: `make
// install-front` runs `npm install` then `npm run check:deps`. Plain `npm ci`
// (install-frozen-front, the Dockerfile) just reproduces an already-validated
// lockfile, so it deliberately does NOT re-run this. Exit non-zero => freeze fails.

import { readdirSync, lstatSync } from 'node:fs';
import { join, sep } from 'node:path';

const ROOT = 'node_modules';

// Size ceiling for node_modules. Bump this deliberately (with review) if the
// tree legitimately needs to grow; the point is to catch accidental bloat.
const LIMIT_GB = 1.0;
const LIMIT_BYTES = LIMIT_GB * 1024 ** 3;

// Package names that must never come back.
const FORBIDDEN = ['@adobe/react-spectrum', '@react-spectrum', '@spectrum-icons'];
// Same, as encoded in the linked strategy's `.store/` dir names (scope+name@ver).
const FORBIDDEN_STORE = [/^@adobe\+react-spectrum@/, /^@react-spectrum\+/, /^@spectrum-icons\+/];

let bytes = 0;
const offenders = new Set();
// Directories we failed to read. A truncated scan could hide bloat and make an
// over-limit tree look fine, so these are surfaced as a hard failure below.
const scanErrors = [];

function flagIfForbidden(fullPath, entryName) {
  const posix = fullPath.split(sep).join('/');
  for (const f of FORBIDDEN) {
    if (posix.includes(`/${f}/`) || posix.endsWith(`/${f}`)) offenders.add(f);
  }
  for (const re of FORBIDDEN_STORE) {
    if (re.test(entryName)) offenders.add(entryName.replace(/@[^@]*$/, '').replace(/\+/g, '/'));
  }
}

function walk(dir) {
  let entries;
  try {
    entries = readdirSync(dir, { withFileTypes: true });
  } catch (err) {
    // Record and keep going so the scan covers as much as possible, but mark it
    // incomplete so the result can't be trusted as "under limit".
    scanErrors.push(`${dir}: ${err.code ?? err.message}`);
    return;
  }
  for (const e of entries) {
    const p = join(dir, e.name);
    if (e.isSymbolicLink()) continue; // don't follow: avoids double-counting + cycles
    if (e.isDirectory()) {
      flagIfForbidden(p, e.name);
      walk(p);
    } else if (e.isFile()) {
      try {
        bytes += lstatSync(p).size;
      } catch {
        /* transient/broken entry — ignore */
      }
    }
  }
}

walk(ROOT);

const gb = bytes / 1024 ** 3;
const problems = [];
if (bytes > LIMIT_BYTES) {
  problems.push(`node_modules is ${gb.toFixed(2)} GB — over the ${LIMIT_GB} GB limit.`);
}
if (offenders.size) {
  problems.push(`forbidden packages present: ${[...offenders].sort().join(', ')}`);
}
if (scanErrors.length) {
  const shown = scanErrors.slice(0, 5).join('; ');
  const more = scanErrors.length > 5 ? ` (+${scanErrors.length - 5} more)` : '';
  problems.push(
    `scan incomplete — ${scanErrors.length} director${scanErrors.length === 1 ? 'y' : 'ies'} ` +
      `could not be read, so the ${gb.toFixed(2)} GB total may be understated: ${shown}${more}`,
  );
}

if (problems.length) {
  console.error('\n\x1b[1;31m✗ dependency guardrail failed\x1b[0m');
  for (const p of problems) console.error(`  • ${p}`);
  console.error(
    '\nThe Adobe React Spectrum bloat (~2.3 GB) has regressed. It is kept out by\n' +
      'the react-aria / react-stately / @react-aria/calendar / @react-aria/datepicker\n' +
      'pins in package.json "overrides". Verify those are intact and run\n' +
      '`npm run check:deps` after regenerating the lockfile.\n',
  );
  process.exit(1);
}

console.error(`\x1b[32m✓ node_modules OK\x1b[0m — ${gb.toFixed(2)} GB, no Adobe React Spectrum packages.`);
