// Regenerate the native app icons and splash screens (make mobile-assets).
//
// Three steps, because `capacitor-assets generate` alone does not produce a
// clean result on this project:
//
//  1. Derive the source PNGs in assets/ from a single vector mark (the theme's
//     app icon, public/images/<theme>/). Hand-made sources drift: the previous ones were
//     a full marketing illustration pasted into a 2732x2732 square, and
//     capacitor-assets resizes splashes with sharp's default `fit: cover` — so
//     every device cropped that illustration somewhere different. A flat
//     background with a centred mark is crop-invariant by construction.
//  2. Run capacitor-assets for everything it does well (all the iOS/Android/PWA
//     densities and the asset catalog / adaptive-icon plumbing).
//  3. Patch what it gets wrong or does not cover at all:
//       - adaptive icon layers are emitted at the *legacy* launcher size
//         (192px at xxxhdpi instead of 432px), so Android upscales them and the
//         icon looks blurry — we rewrite the mipmaps at the 108dp scale;
//       - it never sets up the Android 12+ splash screen, whose icon comes from
//         windowSplashScreenAnimatedIcon (see values/styles.xml) and not from
//         the drawable-*/splash.png bitmaps it emits;
//       - those bitmaps do not work for Android <= 11 either, since a theme
//         background is stretched rather than centre-cropped — we replace the
//         whole set with a layer-list built on the Android 12+ icon.
//
// Usage: node scripts/generate-mobile-assets.mjs [options]
//   --theme <name>        theme whose app icon is the mark (default: anct)
//   --icon <path>         override the mark outright (SVG or PNG, transparent)
//   --icon-dark <path>    same, for the dark variant
//   --skip-sources        keep the PNGs already in assets/ (hand-authored sources)
//   --sources-only        stop after step 1, to iterate on the mark cheaply
import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

// sharp already ships inside @capacitor/assets. Resolving it from there rather
// than declaring it keeps a 30 MB native dependency out of package.json — and
// out of the node_modules budget enforced by scripts/check-node-modules.mjs.
const require = createRequire(import.meta.url);
const sharp = require(
  require.resolve("sharp", {
    paths: [dirname(require.resolve("@capacitor/assets/package.json"))],
  }),
);

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const assetsDir = join(root, "assets");
const resDir = join(root, "android/app/src/main/res");

// Single source of truth for the launch colors: they are baked into the splash
// bitmaps (iOS reads nothing else) *and* written to the Android colors.xml used
// by the Android 12+ system splash, which draws its background itself. Letting
// the two drift is what produces a coloured flash at launch.
const COLORS = {
  iconBackground: "#FFFFFF",
  iconBackgroundDark: "#161B28",
  splashBackground: "#FFFFFF",
  splashBackgroundDark: "#161B28",
};

// Mark size as a fraction of the canvas it sits on. Each value is a platform
// constraint, not a taste call:
//  - icon: leaves room for the iOS squircle mask and for Android's safe zone.
//  - iconForeground: capacitor-assets wraps the foreground in `inset 16.7%`, so
//    the mark lands at 0.68 * 0.666 ~= 45% of the 108dp canvas, i.e. ~49dp wide
//    inside the 72dp visible circle — the Material keyline for a round mask.
//  - splash: iOS and the PWA only (Android composes its launch screen from the
//    splash icon below). Expressed against the *square* canvas, which both
//    centre-crop to the device aspect ratio — on a tall phone that magnifies the
//    mark by ~2.2x, so 0.16 here reads as ~35% of the screen width.
const SCALE = {
  icon: 0.62,
  iconForeground: 0.68,
  splash: 0.16,
};

const ICON_SIZE = 1024;
const SPLASH_SIZE = 2732;
const SPLASH_ICON_SIZE = 1152; // 288dp at xxxhdpi
const SPLASH_ICON_BOX = 288; // dp, the canvas the system gives that icon

/**
 * Scale for the splash icon: Android 12+ masks it to a 192dp circle inside its
 * 288dp canvas, so what has to fit is the mark's *diagonal*, not its width — at
 * the canvas fraction the mask suggests (0.66) the corners fall outside and the
 * mark is visibly clipped. Derived from the mark itself so a wider or narrower
 * drawing stays inside on its own, minus 2% of slack for the OEM masks that cut
 * slightly tighter than a circle.
 *
 * @param {Buffer} mark trimmed mark, as returned by loadMark
 * @returns {Promise<number>} fraction of the 288dp canvas the mark may span
 */
const splashIconScale = async (mark) => {
  const { width, height } = await sharp(mark).metadata();
  const diagonal = Math.hypot(width, height) / Math.max(width, height);
  return ((192 / SPLASH_ICON_BOX) / diagonal) * 0.98;
};

// Adaptive icon layers live on a 108dp canvas, unlike the legacy launcher icon.
const ADAPTIVE_DENSITIES = {
  ldpi: 81,
  mdpi: 108,
  hdpi: 162,
  xhdpi: 216,
  xxhdpi: 324,
  xxxhdpi: 432,
};

const args = process.argv.slice(2);
const flag = (name) => args.includes(`--${name}`);
const option = (name, fallback) => {
  const index = args.indexOf(`--${name}`);
  return index === -1 ? fallback : args[index + 1];
};

const log = (message) => process.stderr.write(`${message}\n`);

/**
 * Rasterise the mark and crop it to its own ink, so the layout below depends
 * only on the drawing and not on whatever padding the source file carries.
 *
 * @param {string} path SVG or PNG with a transparent background
 * @returns {Promise<Buffer>} trimmed RGBA PNG
 */
const loadMark = (path) =>
  sharp(path, { density: 600 })
    .resize({ width: 2048, height: 2048, fit: "inside", withoutEnlargement: false })
    .ensureAlpha()
    .trim(1)
    .png()
    .toBuffer();

// 4:4:4 — the default 4:2:0 bleeds colour around a saturated mark on a flat
// background, which is exactly what a splash is. mozjpeg buys that back and
// then some: same quality, ~40% smaller than the baseline encoder here.
const encode = (pipeline, format) =>
  format === "jpeg"
    ? pipeline.jpeg({ quality: 92, chromaSubsampling: "4:4:4", mozjpeg: true })
    : pipeline.png();

/**
 * Centre the mark on a canvas, square unless width/height say otherwise.
 *
 * @param {Buffer} mark trimmed mark, as returned by loadMark
 * @param {{size?: number, width?: number, height?: number, scale: number,
 *          background?: string, format?: "png"|"jpeg"}} options
 *   scale 0 ⇒ plain canvas; background omitted ⇒ transparent canvas.
 *   The mark is scaled against the canvas' longest side, so a rectangular
 *   canvas gets the same mark a centre-cropped square one would have.
 * @returns {Promise<Buffer>} encoded canvas
 */
const compose = async (mark, { size, width = size, height = size, scale, background, format }) => {
  const canvas = sharp({
    create: {
      width,
      height,
      channels: 4,
      background: background ?? { r: 0, g: 0, b: 0, alpha: 0 },
    },
  });

  if (scale === 0) {
    return encode(canvas, format).toBuffer();
  }

  const box = Math.round(Math.max(width, height) * scale);
  const resized = await sharp(mark).resize({ width: box, height: box, fit: "inside" }).toBuffer();

  return encode(canvas.composite([{ input: resized, gravity: "centre" }]), format).toBuffer();
};

const write = async (path, buffer) => {
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, buffer);
  const { width, height } = await sharp(buffer).metadata();
  log(`  ${path.replace(`${root}/`, "")} (${width}x${height})`);
};

// The mark is the theme's own app icon, not a copy of it: re-skinning the app is
// then a theme change, with no second file to keep in sync. The default is the
// theme whose mark the published app currently ships, which is not the frontend
// default (white-label) — changing it here changes the app's identity.
//
// With --skip-sources the mark is not read at all (hand-authored PNG workflow):
// the transparent foreground becomes the mark, since steps 3a/3b still need one.
// It has no dark twin, so both variants share it.
const themeIcon = (variant) =>
  join(root, "public/images", option("theme", "anct"), `app-icon-${variant}.svg`);
const skipSources = flag("skip-sources");
const iconPath = skipSources
  ? join(assetsDir, "icon-foreground.png")
  : resolve(root, option("icon", themeIcon("light")));
const iconDarkPath = skipSources
  ? join(assetsDir, "icon-foreground.png")
  : resolve(root, option("icon-dark", themeIcon("dark")));

const [mark, markDark] = await Promise.all([loadMark(iconPath), loadMark(iconDarkPath)]);

// Step 1 — sources consumed by capacitor-assets (see its README for the names).
if (!skipSources) {
  log("Generating source assets from the vector mark…");
  const outputs = [
    // Opaque: the App Store rejects an icon with an alpha channel.
    ["icon-only.png", mark, { size: ICON_SIZE, scale: SCALE.icon, background: COLORS.iconBackground }],
    ["icon-dark.png", markDark, { size: ICON_SIZE, scale: SCALE.icon, background: COLORS.iconBackgroundDark }],
    ["icon-foreground.png", mark, { size: ICON_SIZE, scale: SCALE.iconForeground }],
    ["icon-background.png", mark, { size: ICON_SIZE, scale: 0, background: COLORS.iconBackground }],
    ["splash.png", mark, { size: SPLASH_SIZE, scale: SCALE.splash, background: COLORS.splashBackground }],
    ["splash-dark.png", markDark, { size: SPLASH_SIZE, scale: SCALE.splash, background: COLORS.splashBackgroundDark }],
  ];

  for (const [name, source, options] of outputs) {
    await write(join(assetsDir, name), await compose(source, options));
  }
}

if (flag("sources-only")) {
  log("Stopping after the sources (--sources-only).");
  process.exit(0);
}

// Step 2 — densities, asset catalogs and adaptive-icon XML.
log("Running capacitor-assets…");
execFileSync(
  "npx",
  [
    "capacitor-assets",
    "generate",
    "--ios",
    "--android",
    "--pwa",
    "--iconBackgroundColor", COLORS.iconBackground,
    "--iconBackgroundColorDark", COLORS.iconBackgroundDark,
    "--splashBackgroundColor", COLORS.splashBackground,
    "--splashBackgroundColorDark", COLORS.splashBackgroundDark,
  ],
  { cwd: root, stdio: "inherit" },
);

// Step 3a — adaptive icon layers at the 108dp scale (capacitor-assets emits
// them at the legacy launcher size, which Android then upscales).
log("Rewriting the adaptive icon layers at full resolution…");
for (const [density, size] of Object.entries(ADAPTIVE_DENSITIES)) {
  await write(
    join(resDir, `mipmap-${density}/ic_launcher_foreground.png`),
    await compose(mark, { size, scale: SCALE.iconForeground }),
  );
  await write(
    join(resDir, `mipmap-${density}/ic_launcher_background.png`),
    await compose(mark, { size, scale: 0, background: COLORS.iconBackground }),
  );
}

// Step 3b — the Android splash icon, the one asset both launch paths read: the
// Android 12+ system splash draws it over the theme background as
// windowSplashScreenAnimatedIcon (values/styles.xml), and step 3b-bis centres
// it for Android <= 11. Without it the system splash falls back to the launcher
// icon, disc and all.
log("Generating the Android 12+ splash icon…");
{
  // nodpi: one bitmap, scaled by the system to the 288dp canvas of every
  // density — a per-density set would only add files to keep in sync.
  for (const [source, dir] of [
    [mark, "drawable-nodpi"],
    [markDark, "drawable-night-nodpi"],
  ]) {
    const scale = await splashIconScale(source);
    await write(
      join(resDir, `${dir}/splash_icon.png`),
      await compose(source, { size: SPLASH_ICON_SIZE, scale }),
    );
  }
}

// Step 3b-bis — Android <= 11 launch background. capacitor-assets ships one
// splash bitmap per density and orientation, all of them ~2:3, and a bitmap set
// as a theme background is stretched to the window rather than centre-cropped:
// on any phone that is not 2:3 (i.e. every modern one) the mark comes out
// distorted. A layer-list paints the colour and centres the icon the Android
// 12+ splash already uses, in the same 288dp box — no stretching, and both eras
// render the mark at the same size. The bitmaps are then dead weight in the
// APK, so drop the ones capacitor-assets just wrote.
log("Writing the Android <= 11 launch background…");
{
  const path = join(resDir, "drawable/splash_legacy.xml");
  writeFileSync(
    path,
    [
      '<?xml version="1.0" encoding="utf-8"?>',
      "<!-- Generated by scripts/generate-mobile-assets.mjs (make mobile-assets) — do not edit. -->",
      '<layer-list xmlns:android="http://schemas.android.com/apk/res/android">',
      '    <item android:drawable="@color/splashScreenBackground" />',
      "    <item",
      `        android:width="${SPLASH_ICON_BOX}dp"`,
      `        android:height="${SPLASH_ICON_BOX}dp"`,
      '        android:gravity="center"',
      '        android:drawable="@drawable/splash_icon" />',
      "</layer-list>",
      "",
    ].join("\n"),
  );
  log(`  ${path.replace(`${root}/`, "")}`);

  for (const dir of readdirSync(resDir)) {
    if (!/^drawable(-|$)/.test(dir)) continue;
    const bitmap = join(resDir, dir, "splash.png");
    if (!existsSync(bitmap)) continue;
    rmSync(bitmap);
    // capacitor-assets creates these orientation/density folders for the splash
    // alone, so an emptied one is stale rather than merely unused.
    if (readdirSync(join(resDir, dir)).length === 0) rmSync(join(resDir, dir), { recursive: true });
  }
}

// Step 3c — the background the Android 12+ splash paints itself.
log("Writing the Android splash background colors…");
for (const [dir, color] of [
  ["values", COLORS.splashBackground],
  ["values-night", COLORS.splashBackgroundDark],
]) {
  const path = join(resDir, dir, "colors.xml");
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(
    path,
    [
      '<?xml version="1.0" encoding="utf-8"?>',
      "<!-- Generated by scripts/generate-mobile-assets.mjs (make mobile-assets) — do not edit. -->",
      "<resources>",
      `    <color name="splashScreenBackground">${color}</color>`,
      "</resources>",
      "",
    ].join("\n"),
  );
  log(`  ${path.replace(`${root}/`, "")}`);
}

// Step 3d — the PWA icons. capacitor-assets encodes PNG into files it names
// ".webp", then advertises `image/png` in the manifest: the name lies, and the
// icons weigh about twice what they should. Re-encode them for real.
log("Re-encoding the PWA icons as actual WebP…");
{
  const manifestPath = join(root, "public/manifest.json");
  const manifest = JSON.parse(readFileSync(manifestPath, "utf8"));

  for (const icon of manifest.icons) {
    const path = join(root, "public", icon.src);
    await write(path, await sharp(readFileSync(path)).webp({ quality: 90 }).toBuffer());
    icon.type = "image/webp";
  }

  // capacitor-assets also rewrites background_color from the CLI flag, which
  // upper-cases it; keep the manifest's own lowercase convention.
  manifest.background_color = COLORS.splashBackground.toLowerCase();
  writeFileSync(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`);
  log(`  ${manifestPath.replace(`${root}/`, "")}`);
}

// Step 3f — the launch images of the *installed PWA* on iOS. Safari ignores the
// manifest for these and reads the <link rel="apple-touch-startup-image"> set in
// index.html, one per device size and orientation — which is why they are easy
// to forget: they long outlived the marketing splash they were cut from.
// index.html is the source of truth for which sizes exist, so a link can never
// end up pointing at a file nobody generated.
log("Generating the iOS PWA launch images…");
{
  const links = readFileSync(join(root, "index.html"), "utf8");
  const sizes = new Map(
    [...links.matchAll(/apple-splash-(\d+)-(\d+)\.jpg/g)].map(([name, width, height]) => [
      name,
      { width: Number(width), height: Number(height) },
    ]),
  );

  for (const [name, { width, height }] of sizes) {
    await write(
      join(root, "public/images/pwa/splash", name),
      // JPEG, because that is the extension index.html asks for. Only the light
      // background: Safari matches these links on device metrics alone, so there
      // is no dark variant to select.
      await compose(mark, {
        width,
        height,
        scale: SCALE.splash,
        background: COLORS.splashBackground,
        format: "jpeg",
      }),
    );
  }
  log(`  ${sizes.size} launch images`);
}

log("Done. Review the diff, then commit assets/ along with android/ and ios/.");
