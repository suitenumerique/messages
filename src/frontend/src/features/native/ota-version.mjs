/**
 * Ordering of OTA bundle ids, shared by the on-device client (ota.ts) and the
 * node publishing scripts (scripts/publish-ota.mjs). Plain ESM with JSDoc so a
 * single file can be imported from both a Vite/TypeScript build and node.
 *
 * Current ids are bare short commit shas and carry identity only: release
 * ordering lives in the manifest `sequence`. Earlier releases used a hybrid
 * `<count>-<sha>` id whose leading commit count ordered releases (see
 * docs/mobile-release.md, "Bundle versioning"); the helpers below only
 * exist so hybrid-era bundles, manifests and store builds keep ordering
 * correctly until they leave the fleet.
 */

/**
 * Leading commit count of a hybrid `<count>-<sha>` id, or null for any id
 * that carries no count (bare sha, the literal "builtin", a pinned label).
 *
 * @param {string | undefined} version
 * @returns {number | null}
 */
const versionCount = (version) => {
  const match = /^(\d+)-/.exec(version ?? "");
  return match ? Number(match[1]) : null;
};

/**
 * Order two ids by their hybrid count: negative when `version` is older than
 * `reference`, zero when equal, positive when newer — and null as soon as one
 * of them carries no count, since such ids cannot be ordered. Callers treat
 * null as "no opinion" so count guards self-disable on sha ids.
 *
 * @param {string | undefined} version
 * @param {string | undefined} reference
 * @returns {number | null}
 */
export const compareVersionCounts = (version, reference) => {
  const a = versionCount(version);
  const b = versionCount(reference);
  if (a === null || b === null) {
    return null;
  }
  return a - b;
};
