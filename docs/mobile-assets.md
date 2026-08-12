# Mobile app icons & splash screens

How to produce the launcher icons and launch screens of the Android and iOS
apps, and how those files reach the native projects.

Everything is derived from **one vector mark**. You never hand-crop a PNG, and
you never edit anything under `android/app/src/main/res/` or
`ios/App/App/Assets.xcassets/` — those are generated and overwritten.

## TL;DR

```sh
# 1. edit the mark — the theme's own app icon (see "The mark" below)
$EDITOR src/frontend/public/images/anct/app-icon-light.svg
$EDITOR src/frontend/public/images/anct/app-icon-dark.svg

# 2. regenerate everything (runs in the frontend-mobile container)
make mobile-assets

# 3. review the diff and commit assets/, android/, ios/ and public/ together
git add src/frontend/{assets,android,ios,public}
```

`make mobile-assets` is idempotent: running it twice in a row produces no diff.

## What you author vs. what is generated

| File | Status |
| --- | --- |
| `src/frontend/public/images/<theme>/app-icon-{light,dark}.svg` | **authored** — the mark, shared with the web app |
| `src/frontend/assets/*.png` | generated — the sources `capacitor-assets` consumes |
| `src/frontend/android/app/src/main/res/**` | generated (except `styles.xml`, see below) |
| `src/frontend/ios/App/App/Assets.xcassets/**` | generated |
| `src/frontend/public/assets/icons/*.webp` | generated — the PWA icons |
| `src/frontend/public/images/pwa/splash/*.jpg` | generated — the iOS PWA launch images |
| `src/frontend/public/manifest.json` | partly generated — `icons` and `background_color` |

`public/images/pwa/icons/ios/` is *not* generated: six hand-made PNGs
(16, 32, 144, 152, 167, 180) wired into `index.html` as the favicon, the Apple
touch icons and the Windows tile. Twenty more sizes used to sit there
unreferenced and have been removed; if you add one, add its `<link>` too.

`public/assets/icons/icon-mono-72.png` is the push-notification silhouette; it
is authored separately and left alone (see `docs/push-notifications.md`).

⚠️ `assets/logo.png` and `assets/logo-dark.png`, if present, trigger a *second*
mode in `capacitor-assets`: it generates a full icon + splash set from that
single file. Every one of those outputs is then overwritten by the explicit
`icon-*` / `splash-*` sources, so the files are dead weight — but they make the
final rendering depend on an undocumented iteration order, and a mark that
differs from the theme's would silently become the app's identity if that order
ever changed. Keep them out, or keep them identical to the theme mark.

## The mark

Everything is derived from the **theme's own app icon**,
`public/images/<theme>/app-icon-{light,dark}.svg` — the same file the web app
renders. Deliberately not a copy: an icon duplicated into `assets/` drifts from
the brand the moment one of the two is updated.

`--theme` selects the folder; it defaults to the theme whose mark the published
app currently ships, which is **not** the frontend's default theme
(`white-label`). Changing that default changes the app's identity, so it is an
explicit decision rather than something that follows `THEME_CONFIG`:

```sh
docker compose run --rm frontend-mobile npm run mobile:assets -- --theme white-label
```

Two properties are required of whatever mark you point at:

- **No text, no badge.** A launcher icon is rendered as small as 24 px, where a
  wordmark is an unreadable smudge.
- **No padding.** The generator trims the mark to its own ink and re-centres it,
  so any margin in the file is discarded — padding baked into the source would
  only fight the platform safe zones below.

For a mark that lives outside the themes entirely, `--icon` / `--icon-dark`
take a path to any transparent SVG or PNG.

## The layout rules

Sizes are expressed as a fraction of the canvas the mark sits on. They are
platform constraints, not taste — they live in `SCALE` in
`src/frontend/scripts/generate-mobile-assets.mjs`:

| Target | Mark size | Why |
| --- | --- | --- |
| App icon (iOS, Android legacy) | 62 % | leaves room for the iOS squircle mask |
| Adaptive icon foreground | 68 % | `capacitor-assets` then wraps it in `inset 16.7%`, landing the mark at ~49 dp inside the 72 dp visible circle — the Material keyline for a round mask |
| Splash (iOS) | 16 % | of the **square** canvas, which iOS centre-crops to the device ratio; on a tall phone that magnifies the mark ~2.2×, so it reads as ~35 % of the screen width |
| Android splash icon | ~48 % | the system masks the icon to a 192 dp circle inside a 288 dp canvas, so what must fit is the mark's *diagonal* — computed from the mark rather than fixed, see `splashIconScale()` |

The splash is a **flat background with a centred mark**, and that is the single
most important property here: every platform resizes the square splash with a
centre crop (`fit: cover`), so anything that is not centred, or any background
that is not uniform, gets cut at a different place on every device.

## Colors

All launch colors live in `COLORS` at the top of the generator. They are baked
into the splash bitmaps *and* written to the Android `colors.xml`, because the
Android 12+ splash paints its own background instead of reading the bitmap.
Editing them anywhere else makes the two drift, which shows up as a coloured
flash at launch.

| Key | Default | Where it lands |
| --- | --- | --- |
| `iconBackground` | `#FFFFFF` | icon background, all platforms |
| `splashBackground` | `#FFFFFF` | splash bitmaps + `values/colors.xml` |
| `splashBackgroundDark` | `#161B28` | dark splash bitmaps + `values-night/colors.xml` |

## What each platform actually reads

Worth knowing before debugging a launch screen: these paths are independent, and
only iOS reads a full-screen splash bitmap.

**iOS.** `Base.lproj/LaunchScreen.storyboard` shows the `Splash` image set full
screen in `scaleAspectFill`; the asset catalog carries a light and a dark
2732×2732 variant. The app icon is a single opaque 1024×1024 (the App Store
rejects an alpha channel).

**Android 12+.** The system splash draws `windowSplashScreenBackground` and, on
top, `windowSplashScreenAnimatedIcon` — `@drawable/splash_icon`, with its
`drawable-night-nodpi` variant. Without that icon the system falls back to the
launcher icon, disc and all, which is why `values/styles.xml` declares it
explicitly. **This is the only native file in this pipeline you edit by hand.**

**Android ≤ 11.** `AppTheme.NoActionBarLaunch` sets
`android:background="@drawable/splash_legacy"`, a generated layer-list that
paints `@color/splashScreenBackground` and centres that very same
`@drawable/splash_icon` in a 288 dp box — so both Android eras show the mark at
the same size, and the night variants of the colour and the icon are picked up
by qualifier. It deliberately does *not* use a bitmap: a bitmap set as a theme
background is stretched to the window rather than centre-cropped, which distorts
the mark on every screen whose aspect ratio is not the bitmap's. The generator
therefore deletes the `drawable-port-*` / `drawable-land-*` splash bitmaps that
`capacitor-assets` emits.

**Android launcher icon.** `mipmap-anydpi-v26/ic_launcher.xml` composes an
adaptive icon from `ic_launcher_foreground` + `ic_launcher_background`, each
inset by 16.7 %; `ic_launcher.png` is the legacy fallback for API < 26.

**PWA icons.** `public/manifest.json` lists the icons under
`public/assets/icons/`, all declared `any maskable`. `public/sw.js` reuses
`icon-192.webp` as the notification artwork and `icon-mono-72.png` as its badge.

**PWA launch screen (iOS).** Safari ignores the manifest here and reads the 38
`<link rel="apple-touch-startup-image">` in `index.html`, one per device size and
orientation, matched on device metrics alone — so there is no dark variant to
select, and a device with no matching link simply gets a blank screen. The
generator reads that link set as its source of truth for which files to produce:
add a device by adding its `<link>`, then regenerate.

## Verifying

The generator prints every file it writes with its dimensions. Beyond reading
that output:

```sh
# the adaptive layers must be at the 108dp scale (432px at xxxhdpi), not 192px —
# an upscaled layer is the usual cause of a blurry launcher icon
file src/frontend/android/app/src/main/res/mipmap-xxxhdpi/ic_launcher_foreground.png

# the Android splash icon must fit a 192dp circle inside its 288dp canvas: the
# mark's diagonal, not its width — its corners are what the mask clips
open src/frontend/android/app/src/main/res/drawable-nodpi/splash_icon.png
```

On a device, the three things worth a look: the launcher icon in the app drawer
*and* in a themed/round launcher, the launch screen in light and dark mode, and
the Android 12+ splash (`make mobile-android-run`) — which is the one that
silently falls back when misconfigured.

## Known upstream quirks

`@capacitor/assets` is thin and lightly maintained; the generator works around
these behaviours, all documented inline in the script:

1. **Adaptive icon layers are emitted at the legacy launcher size** (192 px at
   xxxhdpi instead of 432 px), so Android upscales them and the icon looks
   blurry. The script rewrites the mipmaps afterwards.
2. **It has no notion of the Android 12+ splash screen.** The `splash_icon`
   drawables and the `styles.xml` entry are ours.
3. **Its Android splash bitmaps cannot be used as a launch background at all.**
   They are all ~2:3 and a theme background is stretched, not cropped, so the
   mark comes out distorted on every phone that is not 2:3. The script deletes
   them and generates `drawable/splash_legacy.xml` instead.
4. **It writes PNG data into files it names `.webp`**, then advertises
   `image/png` in the manifest. The script re-encodes the PWA icons as actual
   WebP (roughly a quarter of the size) and restores the manifest.
5. **It reformats `AndroidManifest.xml`** while rewriting the two icon
   attributes it owns, moving self-closing `/>` onto their own line. Cosmetic
   and stable — it does not grow with each run — so it is left alone; do not
   revert it by hand or the next run brings it back.

It also `require()`s packages it does not declare (`kleur`, `fs-extra`, …).
Those resolve fine in the repo's default hoisted `node_modules`; if a future
install strategy breaks that, declare the missing packages in the frontend
`devDependencies` rather than working around them in the script.

## See also

- `docs/mobile.md` — build & run workflow, OTA updates, release checklist
- `docs/push-notifications.md` — the notification icon, which follows other rules
- `docs/theme-customization.md` — the web theme, whose logos are *not* the app mark
