# Mobile release — OTA bundles & store builds

Operational companion to [`mobile.md`](./mobile.md), which covers onboarding
and the technical concepts (read it first — the OTA section there explains
*what* the client does; this file explains how to **ship**). Everything here
is release-time work: publishing OTA bundles, rolling back, building and
uploading store binaries, and the manual checklist that precedes a store
release.

Two independent release tracks:

| Track | Ships | Cadence | Tool |
| --- | --- | --- | --- |
| **OTA** | the web bundle (`dist/`) only | every web deploy (Scalingo) or on demand | `make mobile-ota-publish` / Scalingo hooks |
| **Store** | the native shell + a builtin bundle | manual, rare | `make mobile-android-release`, Xcode |

Anything native (a new Capacitor plugin, a permission, the Swift/Gradle side)
needs a store release; everything else reaches devices over the air.

## OTA releases

### Publishing

`make mobile-ota-publish [VERSION=x] [CHANNEL=x]` — the frontend node script
`src/frontend/scripts/publish-ota.mjs`, no Django involved — does, in order:

1. `capgo bundle zip` `dist/` (with `index.html` at the zip root);
2. `capgo bundle encrypt` it (→ encrypted zip + encrypted `checksum` +
   `ivSessionKey`);
3. upload the *encrypted* zip to `channels/<channel>/bundles/<version>.zip`;
4. archive the release metadata as `channels/<channel>/releases/<version>.json`
   (immutable — what makes the version re-pointable later, see *Rollback*);
5. write `channels/<channel>/manifest.json` = `{version, url, checksum,
   sessionKey, sequence, publishedAt}` (`checksum` + `sessionKey` feed native
   verification; `sequence` orders releases, see *Release sequence*).

Publishing refuses a version that does not order above the channel's current
manifest (mirror of the legacy client guard; the sanctioned downgrade path is
`make mobile-ota-rollback`, `--force` overrides).

`VERSION` defaults to `MOBILE_OTA_BUILD_ID` (the 8-char short sha, see *Bundle
versioning*), `CHANNEL` to `MOBILE_OTA_CHANNEL`. Local publishing needs the
full dev stack (`make start-full`, object storage on `:8906`) and the bucket
created once with `make mobile-ota-bucket`.

### Release channels

The bucket hosts one **self-contained folder per channel** —
`channels/<channel>/manifest.json` plus its `bundles/` — and each app follows
exactly one channel: the channel segment lives in the `MOBILE_OTA_MANIFEST_URL`
served by the backend the app talks to (`/config` endpoint). The deploy
pipeline publishes to `staging` and `prod`; local development uses `dev`
(commented default in `deploy/env/backend.defaults`), so experiments
never look like a release.

**A bundle is never copied or promoted across channels.** The `NEXT_PUBLIC_*`
vars (API origin, …) are inlined into the web bundle at build
time, so a staging build *is not* a prod build pointed elsewhere — it is a
different artifact targeting the staging backend, whose `/config` in turn pins
the staging channel. Releasing to prod means
rebuilding with the prod env and publishing to the prod channel. Keeping the
zips under their channel also prevents two channels publishing the same commit
(same id) from overwriting each other's bundle.

The publish target comes from `MOBILE_OTA_CHANNEL` (or `--channel` /
`make mobile-ota-publish CHANNEL=…`), and must match the channel the apps follow
through the backend `MOBILE_OTA_MANIFEST_URL`: publishing a bundle built for
another environment would strand the fleet on that other backend's config.

### Generating the signing key pair

Each deployment generates its **own** RSA-2048 pair once (the two halves must
stay a matched set — the app rejects any bundle it can't verify):

```bash
make mobile-ota-keygen
```

It prints the two values ready to paste into an env file / CI secret store
(the guidance goes to stderr, so stdout stays a clean pair):

```
MOBILE_OTA_SIGNING_PUBLIC_KEY_B64=…    # baked into the app build env (capacitor.config.ts)
MOBILE_OTA_SIGNING_PRIVATE_KEY_B64=…   # publish-time secret — CI only, never commit
```

Both are single-line base64 PEMs (PKCS1 — the format `capgo bundle encrypt`
expects) so they survive Docker `env_file` and CI secret stores. The **public
half** goes into the app build env; the **private half signs bundles at publish
time and must stay a CI secret**. Rotating the pair requires shipping a new store
build (the public key is baked in), so treat it as long-lived.

### Bundle versioning

The manifest `version` (and the `channels/<channel>/bundles/<version>.zip` key)
is the **bare short commit sha**, pinned to 8 characters — e.g. `a1b2c3d4`. It
carries *identity only*: release ordering lives entirely in the manifest
`sequence` (see *Release sequence*). The `version` field is a free-form string —
Capgo treats it as a *"version code/name"* and does **not** require semver — so
a commit sha is fine.

The Makefile derives it as `MOBILE_OTA_BUILD_ID` (`git rev-parse --short=8`);
`make mobile-ota-publish` uses it as the default `VERSION` (override with `VERSION=…`
to pin a release). Scalingo deploys have no `.git` and derive the very same id
as `${SOURCE_VERSION:0:8}` — the `--short=8` pin exists because the
builtin-vs-manifest freshness check is a plain string equality, so both sides
must produce identical ids for the same commit (see *Publishing from Scalingo
deploys*).

Earlier releases used a hybrid `<count>-<sha>` id whose leading commit count
carried the ordering. The count-based guards remain in the code and self-disable
on sha ids (`versionCount()` returns `null`); they still bite on hybrid-era
artifacts:

- **Legacy guard**: a manifest *without* a `sequence` (pre-sequence publish) is
  only applied when its count is strictly greater than the running bundle's.
  Sha ids can't be ordered and fall back to a plain inequality check.
- **Native floor** (retired de facto): with hybrid ids,
  `checkAndStageOtaUpdate()` refused to stage a bundle whose count was below
  the native builtin's. Sha ids carry no order, so this bound no longer
  applies — the compensating rule is operational: **cut store builds from a
  commit at or behind the deployed web prod**, so a fresh install's builtin is
  never ahead of the channel. A bundle older than its binary that fails to
  boot is still caught by the automatic revert + blacklist (see *Rollback*).
- **Publish-side accident guard**: `make mobile-ota-publish` refuses a *hybrid*
  version that does not order above the channel's current manifest. Sha
  publishes skip it — on the Scalingo path the guard's job is done structurally
  (the manifest only moves on a successful deployment), and the dev channel is
  disposable.

**Builtin stamping.** `make mobile-build` passes `MOBILE_OTA_BUILD_ID` to `cap sync`,
which stamps it as the store build's builtin bundle version
(`CapacitorUpdater.version` in `capacitor.config.ts`). Without it the builtin
reports the literal `"builtin"`, so the first launch after a store install always
re-downloads. With it, a first launch whose manifest points at the **same** commit
skips the download; a **newer** manifest still updates — the normal case, since
OTA runs ahead of the store.

### Release sequence

`sequence` is a per-channel **monotonic release counter** carried by the
manifest and bumped by *every* manifest write — publish, forced republish and
rollback alike. It decouples the order of *releases* from the order of *builds*:
devices persist the highest sequence they staged (WebView storage,
`ota-applied-sequence`) and accept any manifest with a strictly greater one,
**even when it points at an older build** — which is exactly what a rollback is.
A stale or replayed manifest can never drag a device backward (its sequence is
not above the floor), while a deliberate rollback always can (its sequence is).

A device with **no persisted floor** — pre-sequence client just updated, or
storage wiped — accepts the current manifest on trust-on-first-use, bounded by
the native floor and by bundle signatures; its floor starts there. Migration
caveat: a device still *running* a pre-sequence client ignores `sequence`
entirely and keeps the count-only guard, so **it will not follow a rollback
until it has received a sequence-aware build through a normal forward publish**.
Ship this feature to the fleet before relying on `make mobile-ota-rollback`.

### Rollback

There are two kinds, plus a per-device safety net:

- **Automatic (a bundle that fails to boot).** If the new bundle never calls
  `notifyAppReady()` (crash / white screen), the plugin reverts to the last
  good bundle — the builtin if there is none — on the next launch and records
  the version as its *last failed update*. `checkAndStageOtaUpdate()` mirrors
  that record (which self-clears on read) into WebView storage and refuses to
  re-apply the version, so a broken publish can't trap the app in a
  download → crash → revert → re-download loop. The record is boot-specific:
  a transient download failure does not blacklist the version, it is simply
  retried on the next check.
- **Deliberate (a bundle that boots but is bad).**
  `make mobile-ota-rollback VERSION=<id> [CHANNEL=<name>]` re-points the channel
  manifest at the archived release metadata
  (`channels/<channel>/releases/<version>.json`) under a **higher sequence**:
  no rebuild, devices follow at their next check (launch or foreground,
  30 min throttle) even though the build id goes backward. Limits:
  - Releases published **before** release archiving existed have no
    `releases/<version>.json` (their encrypted `checksum`/`sessionKey` died
    with the overwritten manifest) — for those, check out the old git ref and
    `make mobile-ota-publish` it again; the fresh sequence makes devices follow.
  - Devices still running a **pre-sequence client** ignore the rollback (see
    *Release sequence*).
  - A device that **blacklisted** the target version (it boot-looped there)
    skips it — re-staging it would just loop again. Those devices wait for the
    next forward publish. Related caveat: the blacklist is keyed by version id
    while `--force` can re-point an id at new content, so recovery publishes
    must use a fresh id (the default sha id does: a recovery publish comes from
    a new commit).

  Rolling *forward* (`git revert` + `make mobile-ota-publish`) remains the cleanest
  exit from an incident when build time is not the constraint: it reaches even
  blacklisted/pre-sequence devices.

  On a Scalingo-driven channel, the nominal rollback is neither of these:
  **redeploy the old commit** (see *Publishing from Scalingo deploys*).
- **Per-device safety net.** `CapacitorUpdater.reset()` returns a single device to
  the builtin (store) bundle, which is always bootable. Not fleet-wide; useful to
  wire onto a support/debug action.

**Never prune old bundles or `releases/*.json` from the bucket** — the plugin's
fallback, a rollback and any revert build may still reference them.

### Publishing from Scalingo deploys

On Scalingo the OTA release is not a separate pipeline: **every deployment of an
OTA-configured app publishes the exact `dist/` the web is about to serve**, in
two halves that bracket the deployment outcome:

1. **Stage, at build time** — the frontend `scalingo-postbuild` npm script
   runs `deploy/paas/scalingo_stage_ota` inside the Node.js buildpack compile,
   the only window where node and the devDependencies `publish-ota.mjs` needs
   are both available (gated on `MOBILE_OTA_S3_BUCKET`; with the gate set, any
   other missing `MOBILE_OTA_*` variable fails the build loudly). It runs
   `publish-ota.mjs --stage-only --version ${SOURCE_VERSION:0:8}`: zip, encrypt,
   upload the bundle and archive `releases/<sha8>.json` — but **never touch the
   channel manifest**. Any failure here fails the whole deployment, so a broken
   bundle can't ship; a deployment that fails *later* (python buildpack,
   container boot) leaves at worst orphaned bundle artifacts, never a moved
   pointer. A marker (`build/ota-release-id`) records the staged
   version+channel for step 2.
2. **Flip, at postdeploy** — the Procfile chains
   `python deploy/paas/scalingo_ota_promote.py` after `migrate`. Scalingo runs
   the `postdeploy` hook only when the deployment is otherwise successful, and
   a hook failure marks the deployment `hook-error` with the previous version
   kept serving ([postdeploy hook](https://doc.scalingo.com/platform/app/postdeploy-hook)).
   The script re-points `manifest.json` at the staged release under a bumped
   `sequence` (idempotent: a retry or a same-commit redeploy is a no-op).

The invariant this buys: **the channel manifest can only ever point at a
version that actually serves as the web prod** — mobile and web move as one, in
both directions:

- **Rollback = redeploy the old commit.** Nothing OTA-specific to do: the build
  re-stages that commit's bundle (`releases/<sha8>.json` usually already
  exists) and the flip re-points the manifest under a higher `sequence`;
  devices follow backward at their next check. `make mobile-ota-rollback` remains a
  dev/emergency tool — on a Scalingo-driven channel it can desync mobile from
  web, so reach for a redeploy instead.
- The flip runs seconds *before* the routing switch, so there is a short window
  where the manifest is new and the web still old — same order of magnitude as
  CDN propagation, and the best ordering Scalingo offers (nothing runs "after
  the switch").

Operational rules:

- **Cut store builds from a commit at or behind the deployed web prod.** The
  builtin stamp (`make mobile-build`, sha8) then matches a published manifest —
  no spurious first-launch download/toast — and a fresh install can never sit
  ahead of the channel (the native floor no longer guards this, see *Bundle
  versioning*).
- The vite build must inline `MOBILE_OTA_SIGNING_PUBLIC_KEY_B64` and
  `MOBILE_AUTH_SCHEME` — the hook refuses to publish without either, because
  each defaults to something that only breaks on device: a key-less bundle
  refuses every later update, and a bundle carrying the generic `stmessages`
  scheme cannot log in at all on an environment that uses its own (the backend
  answers `400` on `/authenticate/`, and the deep link would not route back to
  the app). Set `MOBILE_AUTH_SCHEME` on the PaaS app to the very value its
  store/dev builds were built with, even when that is the default: the web
  deploy is also the OTA publisher, so a variable that only exists in
  `frontend.local` reaches the native shells and never the bundle that
  replaces them.
- `NEXT_PUBLIC_API_ORIGIN` must be **absolute** — the same dist serves the
  mobile app from a `capacitor://` origin where a relative origin resolves
  nowhere.
- The backend of the same environment points devices at the channel:
  `MOBILE_OTA_MANIFEST_URL=<MOBILE_OTA_PUBLIC_BASE_URL>/channels/<channel>/manifest.json`.

## Publishing to Google Play

Everything below is **per-instance**: the app id, the signing key and the
Firebase config belong to the publishing organisation and are deliberately
absent from this repo. The commands run on the **host** (the Android SDK is
there), the web bundle is still built in the container.

### 1. Upload key (once, and never lose it)

Play App Signing splits the key in two: Google holds the *app signing key* that
end users verify, you hold an *upload key* that only proves uploads come from
you. A lost upload key can be reset by support; a lost app signing key without
Play App Signing would end the app.

One upload key per Play listing, shared by **every** track: the bundle sent to
internal testing and the one that reaches production are signed with the same
key — which is what lets a tested release be promoted rather than rebuilt. The
key generated for a first internal test *is* the production key, so it belongs
in the organisation's secret manager from day one, passwords included.

Generate the upload key:

```bash
keytool -genkeypair -v \
  -keystore ~/.android-keystores/messages-upload.jks \
  -alias messages-upload \
  -keyalg RSA -keysize 4096 -validity 10000
```

The parameters are constrained, not stylistic. **RSA is mandatory** — Play
requires "an RSA key of 2048 bits or more" for the upload key and rejects
EC/ECDSA, even though the APK signature format itself supports them; 4096
matches what Google generates for the app signing key. **Validity is ~27 years**
(10000 days) because Android recommends at least 25 and Play rejects any
certificate expiring before 22 October 2033. Nothing here should be shortened
out of TLS habit: this certificate is the app's *identity*, not a link in a
renewable trust chain — Android treats an app signed by another certificate as a
different app.

Keep the `.jks` itself **outside the repository** (`chmod 600`). `.gitignore`
stops commits, not Docker: `src/frontend/.dockerignore` only excludes
`node_modules`/`out`/`.next`, so anything under `src/frontend/android/` is sent
to the daemon as build context and lands in a layer through the Dockerfile's
`COPY . ./` — a keystore there would end up cached in a build image.

Store it (and the passwords) in the organisation's secret manager, then point
gradle at it through `src/frontend/android/keystore.properties` — gitignored,
alongside the project, never committed. Use an **absolute** `storeFile` path: a
relative one resolves from `src/frontend/android/app/`, not from where you
stand.

```properties
storeFile=/absolute/path/to/messages-upload.jks
storePassword=…
keyAlias=messages-upload
keyPassword=…
```

CI has no such file and uses `ANDROID_KEYSTORE_FILE` & co. instead
([env.md](./env.md#android-store-release-hostci-only)). With neither, release
builds fail up front rather than producing a bundle Play would reject.

### 2. Release configuration

In `deploy/env/frontend.local` (gitignored) — the container build reads it, and
`make mobile-android-release` re-reads `MOBILE_APP_ID` from it so the host
gradle build cannot diverge:

```bash
MOBILE_APP_ID=fr.gouv.example.messages   # frozen for the lifetime of the app
MOBILE_APP_NAME=Messages
MOBILE_FIREBASE_PROJECT_ID=messages-prod # the google-services.json must match
NEXT_PUBLIC_API_ORIGIN=https://<publicly reachable backend>
MOBILE_DEV_SERVER_URL=                   # empty: no hot reload in a store build
MOBILE_ALLOW_CLEARTEXT_FOR_DEV=          # empty: no cleartext in the manifest
```

The `applicationId` is **frozen once uploaded** — Play identifies the app by it
forever. Two more per-instance pieces, both silent when missing:

- `src/frontend/android/app/google-services.json` from the **production**
  Firebase project, containing a client for that exact id — otherwise push
  notifications simply never arrive (see *Push environment pairing* in the
  release checklist). Set `MOBILE_FIREBASE_PROJECT_ID` so a mismatched file
  fails the build instead.
- backend `MOBILE_AUTH_CALLBACK_SCHEMES=["stmessages"]`, or mobile login is
  disabled.

#### Keeping environments apart

Push isolation comes from **separate Firebase projects**, one per environment
(`PUSH_FCM_PROJECT_ID` backend side, `google-services.json` app side) — not from
a flag. FCM registration tokens are scoped to the project that issued them, so a
staging backend holding staging credentials *cannot* notify production devices
even if it somehow held their tokens: FCM rejects the mismatch. The residual
risk is purely a deployment one — production FCM credentials pasted into a
staging backend.

Sharing one `applicationId` across environments is fine for that isolation, but
it means only one build can be installed at a time, and a wrong
`google-services.json` still compiles (the package name matches). Giving each
environment its own id fixes both — and turns a mismatched Firebase file into a
build failure, since the `google-services` plugin finds no client for the
package name.

Two builds side by side also need **their own callback scheme**
(`MOBILE_AUTH_SCHEME`): two apps claiming one scheme make Android prompt the
user to pick an app in the middle of the login. The value flows from a single
variable to three places — `auth.ts` (inlined by Vite), the Android manifest
(gradle `manifestPlaceholders`), and the iOS `Info.plist` (the
`AUTH_CALLBACK_SCHEME` build setting) — and `sso-invariants.test.ts` pins that
wiring, including that their fallbacks agree. Because gradle runs on the host
and Vite in the container, both must see it: `make mobile-android-run` and
`make mobile-android-release` pass it through, but a bare `./gradlew` does not.

A staging `frontend.local` then reads:

```bash
MOBILE_APP_ID=org.acme.example.messages.local
MOBILE_APP_NAME=Messages (staging)
MOBILE_AUTH_SCHEME=stmessages.local
MOBILE_FIREBASE_PROJECT_ID=messages-local
```

Scheme shape follows RFC 3986 — a letter, then letters, digits, `+`, `-`, `.`
— and must be **lowercase**: Android matches the manifest scheme literally
against a lowercased URI, and Django's redirect validation compares against
`urlparse`, which lowercases too. Neither says anything when it does not match,
the login simply never returns. `_` is not in the grammar and fails silently on
the Python side (`urlparse` yields an empty scheme). Add every scheme in use to
the backend `MOBILE_AUTH_CALLBACK_SCHEMES` list.

What this does *not* solve: both apps still ship the same icon and near-identical
names, which is how a real mail gets sent from the staging build. Differentiated
icons mean generating from a second mark — the generator takes `--icon` /
`--icon-dark` for exactly that, but the output paths are fixed, so the two sets
cannot coexist in one checkout (see [`mobile-assets.md`](./mobile-assets.md)).

### 3. Build the bundle

```bash
make mobile-android-release
```

It runs `make mobile-build` (container: web bundle + `cap sync`) then
`gradlew bundleRelease` (host), and produces
`src/frontend/android/app/build/outputs/bundle/release/app-release.aab`.

#### App versioning

The **displayed version** (Android `versionName`, iOS `MARKETING_VERSION` /
`CFBundleShortVersionString`) has a single source of truth: the `appVersion`
property of `src/frontend/capacitor.config.ts`, **bumped manually** when
releasing:

```ts
const appVersion = "0.1.0";
```

`cap sync` copies it verbatim into each platform's `capacitor.config.json`,
and both native builds read it from there — gradle for the `versionName`, and
`scripts/generate-ios-xcconfig.mjs` for the xcconfig `MOBILE_VERSION_NAME`
(`make mobile-build`). Bumping the one line needs no other change on either
platform. It is a marketing string — users read it in the store listing — and
carries no ordering constraint.

It deliberately does **not** live in `package.json`: that field versions the
web app, which ships on its own cadence (a web deploy or an OTA bundle never
reaches the stores). The two numbers are therefore expected to diverge, and
the app shows them as distinct values — see *App versions* in
[`mobile.md`](./mobile.md#app-versions).

The **technical version** is separate and automatic: `MOBILE_VERSION_CODE`
defaults to the commit count, so it grows on its own; override it
(`make mobile-android-release MOBILE_VERSION_CODE=42`) for a pinned build.
Play refuses a `versionCode` it has already accepted, so every upload needs
a fresh one — including a rebuild of the same commit; App Store Connect
applies the same rule to `CFBundleVersion` within a given `MARKETING_VERSION`.

Both platforms read that one variable, by different routes. Gradle takes it
straight from the environment at `make mobile-android-release`. Xcode cannot:
release builds run from the IDE on the host, where the container's environment
never lands — so `make mobile-build` passes it to
`scripts/generate-ios-xcconfig.mjs`, which writes it into `generated.xcconfig`
as `MOBILE_VERSION_CODE`, and the Xcode project resolves
`CURRENT_PROJECT_VERSION = "$(MOBILE_VERSION_CODE:default=1)"` from there.
A build made without a prior `make mobile-build` therefore falls back to `1`
rather than failing — check the number before an upload.

Using the commit count (rather than a counter reset per marketing version)
keeps it globally monotonic, so it satisfies both stores without any
bookkeeping and reads identically on both platforms in a support report. It is
branch-dependent, though: two branches can produce the same count, so store
releases should always be cut from the same branch.

Five guards fail the build rather than shipping something broken: a leftover dev
`server.url`, cleartext traffic, a missing signing key, an `applicationId`
that does not match the `appId` `cap sync` baked into `capacitor.config.json`
(the `MOBILE_APP_ID`-exported-on-only-one-side trap), and a `versionName` that
is either the unsynced placeholder (`0.0.0`) or stale against the synced
`appVersion`.

### 4. Internal testing track

In the [Play Console](https://play.google.com/console): *Create app*, then
**Testing → Internal testing → Create new release** and upload the `.aab`.
Internal testing reaches up to 100 testers, needs no review wait, and skips the
closed-testing requirements that gate production.

Testers are Google accounts listed in an email list you attach to the track;
each opts in through the generated link before the app appears for them on Play.

Play still gates the *release* on the app-content declarations (privacy policy
URL, data safety form, ads, content rating, target audience). For a mail client
the data safety form is the substantive one: declare what the app collects and
transmits, matching what the backend actually stores.

Because the app is SSO-only, reviewers and testers cannot sign in without an
account on an instance — provide credentials in *App access* when the track ever
moves beyond internal testing.

## Release checklist (manual)

Some load-bearing behaviors cannot fail loudly: when they regress, **login
still works** and only the invisible part disappears, so no error ever
surfaces in development. Run this checklist before every store release, and
after any change to the native projects (`ios/`, `android/`), the auth plumbing
or the Capacitor version.

1. **iOS cross-app SSO** — *the* critical, silent one. It rests on
   `WebAuthSessionPlugin.swift` using `ASWebAuthenticationSession` with
   `prefersEphemeralWebBrowserSession = false`, its registration in
   `MainViewController.swift`, and a **persistent** IdP cookie. Regenerating the
   iOS project or "simplifying" back to the default `Browser` plugin
   (SFSafariViewController — cookie store isolated from Safari) silently turns
   the second app's silent login back into a credential prompt.
   Run the two-app procedure: install a second build with its own
   `MOBILE_APP_ID` and `MOBILE_AUTH_SCHEME` (see *Keeping environments apart*),
   log in on the first, then open the second — it must land in the mailbox
   **without** showing the IdP login form. Confirm objectively in the IdP
   (Keycloak *Events*: a `LOGIN` without a preceding `LOGIN` form for the
   second client) and with a negative control (log out of the IdP, the second
   app must prompt again). The `sso-invariants.test.ts` tripwire pins the files
   (flag, registration, schemes) so the most likely mechanical regressions
   turn CI red, but it cannot prove the runtime behavior — this manual test
   stays mandatory. For a **store release, run it against the production
   IdP**: the IdP-side half of the contract (ProConnect silently reusing its
   session for `acr_values=eidas1`, persistent cookie) lives outside this repo
   and no CI or dev-realm check can stand in for it.
2. **Android cross-app SSO** — same two-app procedure through Chrome Custom
   Tabs. Beware the false negative: an emulator without Play services falls
   back to an isolated-cookie WebView (see *Prerequisites* in
   [`mobile.md`](./mobile.md)).
3. **Thread rendering** — open a thread: the message body iframe
   (`srcDoc` + `sandbox` + CSP) must render. This is what killed the previous
   React Native attempt; a WebView/Capacitor upgrade can regress it.
4. **OTA chain on the release channel** — publish to the channel the build
   follows, relaunch, verify the new bundle applies; then confirm a manifest
   with a lower `sequence` is refused (downgrade guard).
5. **Native file paths** — download/share an attachment and a raw `.eml`
   (native HTTP session), upload an attachment (CSRF token path).
6. **Logout → re-login** — logout ends both the Django session and the IdP
   session (RP-initiated logout in the system browser); the following login
   must stop on the IdP login form, allowing an account switch. A silent
   re-login means the IdP session survived: that is a regression.
7. **No dev server baked in** — in dev, `MOBILE_DEV_SERVER_URL` bakes the Vite
   dev server URL into `capacitor.config.json` (hot reload, see
   [`mobile.md`](./mobile.md#hot-reload)). Before archiving, set it empty in
   `frontend.local` and rerun `make mobile-build`. Android release builds fail
   on a leftover `server.url` (gradle guard in `android/app/build.gradle`);
   Xcode has no equivalent guard, so **check manually for iOS** (no
   `server.url` in `ios/App/App/capacitor.config.json`).
8. **Push environment pairing** — nothing fails loudly on a mismatch, pushes
   just never arrive (or hit `BadDeviceToken` in the sender logs). For a store
   release: the backend serving those users must run
   `PUSH_APNS_USE_SANDBOX=False` (a distribution-signed build holds
   *production* APNs tokens — Xcode rewrites `aps-environment` at export, no
   manual step); the bundled `google-services.json` must come from the
   **production** Firebase project and contain a client for the release
   `MOBILE_APP_ID`; `PUSH_APNS_BUNDLE_ID` must equal that same id. Then run the
   smoke test of [*Push notifications in dev*](./mobile.md#push-notifications-in-dev-optional)
   against the release build.

## See also

- [`mobile.md`](./mobile.md) — onboarding, build & run workflow, and the
  technical concepts behind auth, networking and OTA.
- [`mobile-assets.md`](./mobile-assets.md) — app icons and splash screens.
- [`push-notifications.md`](./push-notifications.md) — push architecture.
- [`env.md`](./env.md) — full environment-variable reference.
