# Mobile apps — technical architecture & onboarding

The Messages mobile apps (iOS + Android) are **the existing web frontend wrapped
in a [Capacitor](https://capacitorjs.com/) native shell**. There is no second
codebase: the same React/Vite bundle that serves `localhost:8900` runs inside a
`WKWebView` (iOS) / Android `WebView`, and a thin native layer supplies what a
browser cannot — a shared-cookie login, a native HTTP stack, file sharing and
over-the-air bundle updates.

This document is the onboarding reference for developers who need to work on the
apps: the architecture, where the code lives, and the prerequisites to build and
run on each platform.

> This is the production-facing companion to [`mobile-poc.md`](./mobile-poc.md),
> which records how the architecture was **validated** (smoke tests, cross-app
> SSO re-testing, negative controls). Read this file first; reach for the POC doc
> when you need the validation procedures.

## Why Capacitor (and not React Native)

The whole product value — rendering arbitrary email HTML safely — depends on an
`iframe` with `srcDoc` + `sandbox` + CSP. A previous React Native attempt broke
on exactly that. Capacitor keeps a real browser engine in the app, so the web
frontend renders identically to the desktop, and **one team maintains one UI**.
The cost is a set of WebView limitations the native layer must paper over
(session cookies, downloads, deep-link auth) — that layer is the interesting
part of this codebase and the rest of this doc.

## Architecture at a glance

```
┌─────────────────────────────────────────── native shell (iOS / Android) ──┐
│                                                                            │
│   ┌──────────────────────── WebView ───────────────────────┐              │
│   │  the web bundle (dist/) — React / TanStack / BlockNote  │              │
│   │                                                         │              │
│   │  window.fetch ──────────┐   (patched by CapacitorHttp)  │              │
│   └─────────────────────────┼───────────────────────────────┘             │
│                             ▼                                              │
│   ┌──────────────── native bridge (Capacitor plugins) ─────────────────┐  │
│   │  CapacitorHttp   → native HTTP stack, native cookie jar            │  │
│   │  WebAuthSession  → ASWebAuthenticationSession (iOS, app-local)     │  │
│   │  Browser         → Chrome Custom Tabs (Android)                    │  │
│   │  Filesystem/Share→ downloads to OS share sheet                     │  │
│   │  CapacitorUpdater→ OTA bundle download / swap                      │  │
│   └────────────────────────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────────────────────┘
        │ HTTPS (prod) / cleartext localhost (dev)      │ system browser
        ▼                                               ▼
   Django backend (confidential OIDC client)       Identity provider
   /api/v1.0/…                                     ProConnect (prod) / Keycloak (dev)
```

Four concerns make the shell more than a browser:

1. **Networking & session** — `window.fetch` is routed through the native HTTP
   layer so cookies live in the native jar, not the WebView.
2. **Authentication** — the OIDC flow runs in the *system browser*, never the
   WebView, which is what enables cross-app SSO across all La Suite apps.
3. **File I/O** — downloads and shares go through native plugins because an
   `<a download>` escapes the WebView and loses the session.
4. **OTA updates** — the JS bundle can be replaced without a store release.

Each is detailed below.

## Authentication architecture

This is the load-bearing design decision. **The user who logs in on one La Suite
app (mail, calendar, …) must not re-enter credentials on the others.**

The OIDC flow runs in the **system browser** — `ASWebAuthenticationSession` on
iOS, Chrome Custom Tabs on Android — following RFC 8252. The system browser
shares its cookie jar across apps, so the IdP session cookie (ProConnect in
production, Keycloak in development) provides the cross-app SSO: the second
app's login completes silently.

The backend stays the **confidential OIDC client** (`django-lasuite`). The IdP
only ever sees the ordinary web flow with the backend's HTTPS callback, so **no
IdP-side configuration is needed for mobile**. The Django session is then handed
to the app through a one-time token, bound to the app by PKCE:

```
App                         System browser                  Backend                IdP
 │  openAuthSession()            │                             │                    │
 │──────────────────────────────▶│ GET /api/v1.0/authenticate/ │                    │
 │   (+ mobile_scheme,           │────────────────────────────▶│  302 authorize     │
 │      code_challenge=S256)     │─────────────────────────────┼───────────────────▶│
 │                               │            login form (or silent SSO redirect)   │
 │                               │ GET /api/v1.0/callback/     │◀───────────────────│
 │   stmessages://auth?token=…   │◀────────────────────────────│ session + one-time │
 │◀──────────────────────────────│                             │ token (60 s TTL)   │
 │  POST /api/v1.0/mobile/auth/exchange/ {token, code_verifier}│                    │
 │────────────────────────────────────────────────────────────▶│                    │
 │◀── Set-Cookie sessionid + csrftoken, body {csrf_token} ─────│                    │
```

Step by step:

1. **App starts the flow.** `nativeLogin()` generates a PKCE verifier
   (`generateCodeVerifier`), computes its S256 challenge, and opens
   `/api/v1.0/authenticate/?mobile_scheme=stmessages&code_challenge=…` in the
   system browser.
2. **Backend flags the session.** `OIDCAuthenticationRequestView` checks the
   scheme against `MOBILE_AUTH_CALLBACK_SCHEMES` (rejects unknown schemes) and
   stashes `{scheme, code_challenge, created_at}` in the Django session. A flag
   older than 10 min is later ignored, so an abandoned mobile attempt can't
   hijack a subsequent web login in the same browser.
3. **IdP authenticates** — interactively the first time, silently afterwards
   (see *Cross-app SSO conditions* below).
4. **Callback mints a one-time token.** `OIDCAuthenticationCallbackView` caches
   `{session_key, code_challenge}` under `mobile-auth-token:<token>` with a
   `MOBILE_AUTH_TOKEN_TTL` (60 s) timeout and deep-links back to
   `stmessages://auth?token=…`.
5. **App exchanges the token.** `MobileSessionExchangeView` (anonymous, single
   use) deletes the cache key *before* verifying it (a failed attempt can't be
   retried), checks `S256(code_verifier) == code_challenge` with
   `secrets.compare_digest`, rehydrates the session, and emits the
   `Set-Cookie: sessionid` header plus a `csrf_token` in the body.

The token is a bearer secret for ~60 s; **PKCE is what makes a stolen deep link
useless** (the attacker lacks the verifier), which matters because custom URL
schemes can be claimed by other apps.

The `stmessages` return scheme must be registered natively on **both** platforms
(source of truth: `AUTH_CALLBACK_SCHEME` in `auth.ts`): an `intent-filter` in
`AndroidManifest.xml`, and `CFBundleURLTypes` in the iOS `Info.plist` — the
latter is required because `ASWebAuthenticationSession` runs with
`prefersEphemeralWebBrowserSession = false` (needed to share the IdP cookie), and
in that mode iOS only delivers the callback for an app-registered scheme. Both
are independent of `MOBILE_APP_ID`.

### Cross-app SSO conditions (both bit us during the POC)

- **Requested ACR must be satisfiable.** The backend sends
  `OIDC_AUTH_REQUEST_EXTRA_PARAMS={"acr_values": "eidas1"}` (required by
  ProConnect). The IdP only skips the form if its existing session already meets
  that Level of Assurance. The dev Keycloak realm therefore **maps `eidas1`**
  (`acr.loa.map` on the `messages` client in `src/keycloak/realm.json`); with an
  empty map Keycloak forces re-authentication on every flow and silently breaks
  cross-app SSO. Do not remove that mapping.
- **The IdP session cookie must be persistent on iOS.**
  `ASWebAuthenticationSession` only shares Safari's *persistent* cookies; the
  Keycloak identity cookie is a session cookie unless "Remember me" is ticked.

> **False positive to avoid:** visiting `localhost:8900` may "log in silently"
> simply because the Django session cookie is still valid — that never hits
> `/authorize` and does **not** prove IdP SSO. Always exercise the mobile flow.

### Logout keeps the IdP session alive

`nativeLogout()` does **not** call `/logout/`, which would trigger RP-initiated
IdP logout and tear down the cross-app SSO session. It POSTs to
`/api/v1.0/mobile/auth/logout/` instead, which flushes only the server-side
Django session, then clears the native cookies and the cached CSRF token.
IdP-level logout is a follow-up.

## Networking & session

`CapacitorHttp` (enabled in `capacitor.config.ts`) patches `window.fetch` so
every API call goes through the **native HTTP stack**, and the session cookies
live in the **native cookie jar**:

- No `SameSite` / ITP restriction, no WebView CORS.
- The plain-HTTP dev backend works (`server.cleartext` on Android — gated by
  `MOBILE_ALLOW_CLEARTEXT_FOR_DEV`, set in `frontend.defaults` — and
  `NSAllowsLocalNetworking` on iOS; both dev-only).

The trade-off is that the WebView can no longer read the `csrftoken` cookie from
`document.cookie`. So the CSRF token is delivered out-of-band by the session
exchange and cached in `localStorage`:

- `csrf.ts` stores/reads it under `messages_native-csrf-token`.
- `getCSRFToken()` (`src/features/api/utils.ts`) returns the native token on
  native platforms and the web token otherwise; `getHeaders()` echoes it as
  `X-CSRFToken`. This works with the backend's `CSRF_USE_SESSIONS` (the secret
  lives in the session, replayed by the native cookie jar).

**Downloads** can't use `<a download>`: on native it escapes the WebView into the
system browser, which has no session and gets a 401. `nativeDownloadFile()`
fetches the bytes through `CapacitorHttp` (carrying the session), writes them to
`Directory.Cache` and hands them to the OS share sheet. Used by the thread-view
attachment components.

## OTA (over-the-air) updates

The JS bundle can be replaced without a store release, driven entirely from JS
against a **public S3 bucket — no Capgo server** (`autoUpdate: false`; the
`@capgo/capacitor-updater` plugin is used only for its native download/set/reload
primitives). Because the bucket is world-readable, every bundle is **encrypted
and signed** (Capgo v2, RSA+AES) with a per-instance key: the public half is
baked into the app at `cap sync` time (`capacitor.config.ts`, `publicKey` ←
`MOBILE_OTA_SIGNING_PUBLIC_KEY_B64`), the private half signs at publish time
(`MOBILE_OTA_SIGNING_PRIVATE_KEY_B64`, CI-only). A substituted zip therefore fails
native verification instead of running arbitrary code. The key is mandatory as
soon as OTA is on: `ota.ts` refuses to apply a manifest when the build embeds no
`MOBILE_OTA_SIGNING_PUBLIC_KEY_B64` (and `cap sync` still fails when the
deprecated baked `NEXT_PUBLIC_MOBILE_OTA_MANIFEST_URL` is set without it), so an
OTA-enabled app can never apply an unverified bundle.

- **Publish** (`make ota-publish [VERSION=x] [CHANNEL=x]`, i.e. the frontend
  node script `src/frontend/scripts/publish-ota.mjs` — no Django involved):
  `capgo bundle zip` `dist/` (with `index.html` at the zip root), `capgo bundle
  encrypt` it (→ encrypted zip + encrypted `checksum` + `ivSessionKey`), upload
  the *encrypted* zip to `channels/<channel>/bundles/<version>.zip`, and write
  `channels/<channel>/manifest.json` = `{version, url, checksum, sessionKey}`
  (the last two feed native verification). Publishing also refuses a version
  that does not order above the channel's current manifest (mirror of the
  client-side downgrade guard, see *Bundle versioning*; `--force` overrides).
- **Consume** (`src/features/native/ota.ts`, called at startup):
  `notifyOtaAppReady()` first (confirms the running bundle booted, so
  a broken update auto-rolls-back on next launch), then
  `checkAndApplyOtaUpdate()` polls the manifest URL served by the backend
  `MOBILE_OTA_MANIFEST_URL` setting (`/config` endpoint, resolved in
  `bootstrap.tsx`) and applies the
  advertised bundle **only if it is genuinely newer** — it must differ from
  `CapacitorUpdater.current()`, carry a *strictly greater* version count (see
  *Bundle versioning*), and not be recorded as a prior failed
  boot (see *Rollback*). It then downloads (passing `checksum` + `sessionKey`,
  verified against the baked-in public key) and `set()`s it, which reloads the
  WebView.

OTA replaces the *web* bundle only. Anything native (a new Capacitor plugin, a
permission, the Swift/Gradle side) still requires a store release.

### Release channels

The bucket hosts one **self-contained folder per channel** —
`channels/<channel>/manifest.json` plus its `bundles/` — and each app follows
exactly one channel: the channel segment lives in the `MOBILE_OTA_MANIFEST_URL`
served by the backend the app talks to (`/config` endpoint). The deploy
pipeline publishes to `staging` and `prod`; local development uses `dev`
(commented default in `env.d/development/backend.defaults`), so experiments
never look like a release.

**A bundle is never copied or promoted across channels.** The `NEXT_PUBLIC_*`
vars (API origin, …) are inlined into the web bundle at build
time, so a staging build *is not* a prod build pointed elsewhere — it is a
different artifact targeting the staging backend, whose `/config` in turn pins
the staging channel. Releasing to prod means
rebuilding with the prod env and publishing to the prod channel. Keeping the
zips under their channel also prevents two channels publishing the same commit
(same `<count>-<sha>` id) from overwriting each other's bundle.

The publish target comes from `MOBILE_OTA_CHANNEL` (or `--channel` /
`make ota-publish CHANNEL=…`), and must match the channel the apps follow
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
is a **hybrid id**, `<count>-<sha>` — e.g. `1234-a1b2c3d`:

- `<count>` = `git rev-list --count HEAD`, a **monotonic** commit count that
  orders releases;
- `<sha>` = `git rev-parse --short HEAD`, tracing the exact source commit.

The Makefile derives it once as `MOBILE_OTA_BUILD_ID`; `make ota-publish` uses it as the
default `VERSION` (override with `VERSION=…` to pin a release). The `version`
field is a free-form string — Capgo treats it as a *"version code/name"* and does
**not** require semver — so a commit-based id is fine. We use `-` (not the semver
`+` build-metadata separator) to keep the id safe in the bundle URL/S3 key.

The count drives **ordering, and the client enforces it**: `checkAndApplyOtaUpdate()`
applies a manifest only when its count is *strictly greater* than the running
bundle's, so **republishing an older build cannot downgrade the fleet** (an
accidental old publish or a replayed old bundle is refused). A bare SHA would
carry no such order. Ids without the numeric prefix — the literal `"builtin"`, or
a manually pinned non-hybrid version — can't be ordered and fall back to a plain
inequality check. Because the count comes from `git rev-list --count HEAD`, it is
only monotonic **along a single line of history**: always publish OTA from the
release branch, or two diverging branches can mint colliding counts.

**Builtin stamping.** `make mobile-build` passes `MOBILE_OTA_BUILD_ID` to `cap sync`,
which stamps it as the store build's builtin bundle version
(`CapacitorUpdater.version` in `capacitor.config.ts`). Without it the builtin
reports the literal `"builtin"`, so the first launch after a store install always
re-downloads. With it, a first launch whose manifest points at the **same** commit
skips the download; a **newer** manifest still updates — the normal case, since
OTA runs ahead of the store.

### Rollback

There are two kinds, plus a per-device safety net:

- **Automatic (a bundle that fails to boot).** If the new bundle never calls
  `notifyAppReady()` (crash / white screen), the plugin reverts to the last
  good bundle — the builtin if there is none — on the next launch and records
  the version as its *last failed update*. `checkAndApplyOtaUpdate()` mirrors
  that record (which self-clears on read) into WebView storage and refuses to
  re-apply the version, so a broken publish can't trap the app in a
  download → crash → revert → re-download loop. The record is boot-specific:
  a transient download failure does not blacklist the version, it is simply
  retried on the next check.
- **Deliberate (a bundle that boots but is bad).** You **cannot** point the
  manifest back at the older, lower-count bundle — the downgrade guard refuses
  it. Roll *forward* instead: `git revert` the bad commit(s) and
  `make ota-publish`. The revert has a **higher** count, so it passes the guard
  and the fleet converges onto the (restored) good code with a clean git trail.
  Escape hatch if you can't revert: `make ota-publish VERSION=<count-above-current>-<oldsha>`
  from the old build — but that breaks the count↔commit invariant, so prefer the
  revert.
- **Per-device safety net.** `CapacitorUpdater.reset()` returns a single device to
  the builtin (store) bundle, which is always bootable. Not fleet-wide; useful to
  wire onto a support/debug action.

**Never prune old bundles from the bucket** — the plugin's fallback and any
revert build may still reference them.

## Codebase map

| Concern | Location |
| --- | --- |
| Capacitor config (appId via `MOBILE_APP_ID`, plugins, HTTP, SystemBars, OTA signing key) | `src/frontend/capacitor.config.ts` |
| Platform detection | `src/frontend/src/features/native/platform.ts` |
| PKCE helpers | `src/frontend/src/features/native/pkce.ts` |
| System-browser session | `src/frontend/src/features/native/auth-session.ts` |
| Native login / logout | `src/frontend/src/features/native/auth.ts` |
| Native CSRF token store | `src/frontend/src/features/native/csrf.ts` |
| Native download → share | `src/frontend/src/features/native/download.ts` |
| OTA client | `src/frontend/src/features/native/ota.ts` |
| Startup wiring (OTA, `native` html class) | `src/frontend/src/main.tsx` |
| CSRF / API origin wiring | `src/frontend/src/features/api/utils.ts` |
| Login/logout routing | `src/frontend/src/features/auth/index.tsx` |
| iOS ASWebAuthenticationSession plugin | `src/frontend/ios/App/App/WebAuthSessionPlugin.swift` |
| iOS plugin registration | `src/frontend/ios/App/App/MainViewController.swift` |
| SSO invariants tripwire (CI guard on the native declarations) | `src/frontend/src/features/native/sso-invariants.test.ts` |
| Android project | `src/frontend/android/` |
| Backend mobile-aware OIDC views | `src/backend/core/authentication/views.py` |
| Backend token → session exchange & mobile logout | `src/backend/core/api/viewsets/mobile_auth.py` |
| OTA publish scripts | `src/frontend/scripts/publish-ota.mjs`, `create-ota-bucket.mjs`, `ota-lib.mjs` |

**Native/web branching contract:** the single source of truth is
`isNativePlatform()`. `main.tsx` also tags `<html class="native">` so stylesheets
can opt into mobile-only chrome without every component re-deriving the platform.

## Prerequisites

### Common (all developers)

- The **dev stack** running: `make bootstrap` once, then `make start` (or
  `make start-minimal`). Backend on `:8901`, Keycloak on `:8902`, object storage
  on `:8906`.
- **No host Node toolchain is required**: the web bundle is built inside the
  `frontend-mobile` container — see *Build & run workflow*. The host only needs
  the native toolchains below. If you do run `npm` on the host anyway, it must
  be **Node 22** (`>=22 <23`): any other version corrupts the lockfile and
  breaks the container build.
- Backend settings must allowlist the scheme:
  `MOBILE_AUTH_CALLBACK_SCHEMES=["stmessages"]` (empty list = mobile login
  disabled). See [env.md](./env.md).

### Android

If you have never set up an Android toolchain, follow [Capacitor's environment
setup guide](https://capacitorjs.com/docs/getting-started/environment-setup#android-requirements)
end-to-end first; the list below is what this project specifically needs.

- **Android Studio** (latest stable) with the Android SDK —
  [install guide](https://developer.android.com/studio/install).
- **SDK levels**: `compileSdk 36` / `targetSdk 36`, `minSdk 24`. Install SDK 36
  + build-tools via the [SDK Manager](https://developer.android.com/studio/intro/update#sdk-manager).
- **JDK 17+** (bundled with recent Android Studio).
- **[`adb`](https://developer.android.com/tools/adb)** on the `PATH` (host), for
  install + port forwarding. Heads-up: the `adb reverse` tunnel to the dev stack
  is dropped on **every emulator reboot** — rerun `make mobile-android-reverse`
  (details under *Build & run workflow*).
- An **[emulator image](https://developer.android.com/studio/run/managing-avds)
  with Play services** (Google Play / Google APIs). Chrome
  Custom Tabs needs it; a bare AOSP image falls back to an isolated-cookie
  WebView and **breaks cross-app SSO** (a common false negative). A physical
  device always ships Chrome, so it can't hit this.

### iOS

If you have never set up an iOS toolchain, follow [Capacitor's environment
setup guide](https://capacitorjs.com/docs/getting-started/environment-setup#ios-requirements)
end-to-end first; the list below is what this project specifically needs.

- A **Mac** — non-negotiable for iOS builds.
- **[Xcode](https://developer.apple.com/xcode/) 16+** with the iOS 16+ SDK.
  Deployment target is **iOS 15**.
- Dependencies are managed by **Swift Package Manager** (pinned in
  `ios/App/CapApp-SPM/Package.swift`) — **no CocoaPods / Podfile**. Xcode
  resolves the packages on first open.
- For a physical device: an Apple developer account and a signing team
  configured in Xcode — see [running your app on a device](https://developer.apple.com/documentation/xcode/running-your-app-in-simulator-or-on-a-device).

## Build & run workflow

The web bundle is built **in a container** (`frontend-mobile`) so the
`NEXT_PUBLIC_*` vars from `env.d/development/frontend.{defaults,local}` are
inlined at build time (Vite `envPrefix: 'NEXT_PUBLIC_'`). Building on the host
with a bare `npm run build` would inline none of them. The native compile, IDE,
`adb` and Xcode steps run on the **host**.

> **Always run `make mobile-build` after a fresh checkout.** `cap sync` (not a
> bare copy) also regenerates the gitignored
> `capacitor-cordova-android-plugins/` scaffolding that Gradle needs.

| Command | What it does |
| --- | --- |
| `make mobile-build` | web build (container) + `cap sync` into `ios/` and `android/` |
| `make mobile-assets` | regenerate native icons & splashscreens from `src/frontend/assets/` |
| `make mobile-android` | `mobile-build`, then open the Android project in Android Studio (host) |
| `make mobile-android-run` | `mobile-build` + `gradlew assembleDebug` + `adb install` + `adb reverse` (host) |
| `make mobile-android-reverse` | (re)apply the `adb reverse` port mapping |
| `make mobile-ios` | `mobile-build`, then open the Xcode project (host, macOS) |
| `make mobile-ota-keygen` | generate a per-instance OTA signing key pair (base64 PEMs) |
| `make mobile-ota-bucket` | create the public `messages-ota` bucket |
| `make ota-publish [VERSION=x] [CHANNEL=x]` | build + publish a signed OTA bundle and its channel manifest (VERSION defaults to `<count>-<sha>`, CHANNEL to `MOBILE_OTA_CHANNEL`) |

**Android port forwarding.** The in-app WebView reaches the dev stack through an
`adb reverse` tunnel for ports **8900, 8901, 8902, 8906** (frontend, backend,
Keycloak, object storage). It is dropped on every emulator reboot / adb
reconnection and is **not** re-applied by Android Studio — rerun
`make mobile-android-reverse` whenever the app suddenly can't reach the backend.
The same tunnel works over USB for a physical device (enable Developer options +
USB debugging first). With several devices attached, pin one with
`export ANDROID_SERIAL=<serial>` (`adb devices` to list).

**iOS** needs no tunnel: the simulator reaches the host's `localhost` directly.
Run the `App` scheme after `make mobile-ios`.

### Hot reload (on by default in dev)

`MOBILE_DEV_SERVER_URL` — set to `http://localhost:8900` (the Vite dev server)
in `env.d/development/frontend.defaults` — is baked by `cap sync` into the app
as Capacitor's `server.url`: the WebView loads the app straight from Vite
instead of the embedded `dist/`, so JS/CSS changes apply through HMR without
rebuilding or reinstalling. Since every `make mobile-*` target runs in a
container carrying the frontend env files, **any dev build gets hot reload out
of the box**. Requirements and caveats:

- The dev stack must be up (`make run` / the `frontend-dev` service): the app
  is blank otherwise. `localhost:8900` is routed by the same `adb reverse`
  tunnel as the backend on Android, and by the shared loopback on the iOS
  simulator. For a **physical iPhone** (no tunnel), point it at the Mac's LAN
  IP in `frontend.local`: `MOBILE_DEV_SERVER_URL=http://<mac-ip>:8900` (ATS
  exempts raw IP literals, so plain HTTP works).
- Native changes (plugins, `ios/`, `android/`, `capacitor.config.ts`) still
  need a rebuild — hot reload only covers the web bundle.
- The startup OTA check is skipped during a hot reload session (`ota.ts` skips
  when `import.meta.env.DEV` **and** `MOBILE_DEV_SERVER_URL` are set): applying
  a downloaded bundle would yank the WebView off the dev server mid-session.

**Disabling it** — to test the embedded bundle (what a store build ships), or
the OTA chain end to end: set the variable **empty** in
`env.d/development/frontend.local` (gitignored, overrides the defaults):

```bash
# env.d/development/frontend.local
MOBILE_DEV_SERVER_URL=
```

then rerun `make mobile-build` (or any target that wraps it) and reinstall the
app. A leftover `server.url` fails Android **release** builds (gradle guard in
`android/app/build.gradle`); see the release checklist for iOS.

## Configuration

Mobile-specific environment variables (full reference in [env.md](./env.md)):

| Variable | Purpose |
| --- | --- |
| `MOBILE_APP_ID` | Store/OS bundle identifier (default `local.suitenumerique.messages`). Read by `cap sync` (container) **and** the native builds (gradle `applicationId`, iOS `PRODUCT_BUNDLE_IDENTIFIER`), so it must be exported in both contexts. Independent of the `stmessages` auth scheme |
| `MOBILE_AUTH_CALLBACK_SCHEMES` | JSON list of allowlisted deep-link schemes (e.g. `["stmessages"]`); empty disables mobile login |
| `MOBILE_DEV_SERVER_URL` | Dev only: Vite dev server URL baked as Capacitor `server.url` at `cap sync` (hot reload). Set to `http://localhost:8900` in `frontend.defaults`; disable with an empty value in `frontend.local`; never set for release builds (see *Hot reload*) |
| `MOBILE_ALLOW_CLEARTEXT_FOR_DEV` | Dev only: baked as Capacitor `server.cleartext` at `cap sync` (`android:usesCleartextTraffic`), allowing plain HTTP to the dev backend / Vite / RustFS. Set to `1` in `frontend.defaults`; never set for release builds — the manifest then stays cleartext-free |
| `MOBILE_AUTH_TOKEN_TTL` | Lifetime (s) of the one-time exchange token (default 60) |
| `NEXT_PUBLIC_API_ORIGIN` | API base URL — **must be set explicitly** for mobile builds (no meaningful `window.location.origin` in the WebView) |
| `MOBILE_OTA_MANIFEST_URL` | Backend setting served through `/config`: OTA channel manifest polled at startup — the followed channel changes without a new native build; unset disables OTA (deprecated build-time fallback: `NEXT_PUBLIC_MOBILE_OTA_MANIFEST_URL`) |
| `MOBILE_OTA_CHANNEL` | Release channel `ota-publish` targets (`dev` locally, `staging`/`prod` in the pipeline); must match the channel the build follows (see *Release channels*) |
| `MOBILE_OTA_S3_*`, `MOBILE_OTA_PUBLIC_BASE_URL` | OTA publish: S3 write credentials/endpoint (frontend env, not Django) and the device-reachable public base URL written into the manifest |
| `MOBILE_OTA_SIGNING_PUBLIC_KEY_B64` | Base64 PEM public key baked into the app (`capacitor.config.ts`, native verification) and inlined by Vite (`ota.ts` refuses a server-provided manifest URL without it); required for any OTA-enabled build |
| `MOBILE_OTA_SIGNING_PRIVATE_KEY_B64` | Base64 PEM private key that signs bundles at publish time (`publish-ota.mjs`, CI-only) |

## Production hardening / known gaps

The following are POC-scoped shortcuts that must be resolved before shipping.
Treat this list as the "definition of ready for production".

- **OTA over HTTPS.** Bundle signing/encryption (Capgo v2, RSA+AES) and a
  strictly-increasing version guard are in place (see the OTA section), so a
  substituted or replayed old zip is refused. What remains for production is to
  serve the bucket/CDN over **HTTPS** (dev uses cleartext RustFS). A hard
  minimum-version *floor* baked into the app — rejecting anything below a known
  release regardless of the running bundle — would further harden a device stuck
  on a very old build, but the monotonic guard already covers accidental
  downgrades.
- **Move off custom URL schemes.** Custom schemes can be claimed by other apps
  (mitigated today by the one-time token + PKCE). Production should move to
  **Universal Links (iOS) / App Links (Android)**.
- **CSRF `Origin` on HTTPS.** Native requests carry no `Origin`/`Referer`, which
  Django requires on secure requests. The fetch wrapper must inject an `Origin`
  listed in `CSRF_TRUSTED_ORIGINS`.
- **Cleartext transport is dev-only, build-gated on both platforms.** On
  Android, `preReleaseBuild` fails when the synced `capacitor.config.json`
  carries a dev `server.url` or `server.cleartext` (i.e. when
  `MOBILE_DEV_SERVER_URL` / `MOBILE_ALLOW_CLEARTEXT_FOR_DEV` was in the
  `cap sync` env). On iOS, the "Strip dev ATS exception" build phase deletes
  the `NSAppTransportSecurity` dict (`NSAllowsLocalNetworking`) from the built
  product in every non-Debug configuration, so it never ships in an Archive.
- **IdP logout & session renewal.** Logout ends the Django session but not
  the IdP one; the 12 h Django session has no refresh-token renewal yet.
  Confirm ProConnect SSO session duration and persistent-cookie behaviour
  (esp. iOS) in production.
- **Safe-area insets.** Disabling Capacitor's `SystemBars` inset handling (to fix
  the double keyboard inset, Capacitor #8181) means Android no longer receives
  the `--safe-area-inset-*` CSS variables; `MainActivity.java` re-injects them
  from the window insets (system bars + display cutout), without touching the
  keyboard behavior. iOS resolves `env(safe-area-inset-*)` natively. The app
  shell folds the top inset into `--header-height` (`globals.scss`), so
  anything laid out from it clears the status bar / notch automatically.
- **Iframe subresources.** Inline images proxied through the API use the WebView
  network stack, not the native one, and may not load in dev; the HTML body
  itself renders.
- **App Store guideline 4.2.** A pure web wrapper needs native-feeling
  differentiators (push notifications, share targets…) to pass review.

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
   Run the [two-app procedure](./mobile-poc.md#re-testing-cross-app-sso-with-a-second-app)
   and its objective proofs (Keycloak events, negative control). The
   `sso-invariants.test.ts` tripwire pins the files (flag, registration,
   schemes) so the most likely mechanical regressions turn CI red, but it
   cannot prove the runtime behavior — this manual test stays mandatory.
   For a **store release, run it against the production IdP**: the IdP-side
   half of the contract (ProConnect silently reusing its session for
   `acr_values=eidas1`, persistent cookie) lives outside this repo and no
   CI or dev-realm check can stand in for it.
2. **Android cross-app SSO** — same two-app procedure through Chrome Custom
   Tabs. Beware the false negative: an emulator without Play services falls
   back to an isolated-cookie WebView (see *Prerequisites*).
3. **Thread rendering** — open a thread: the message body iframe
   (`srcDoc` + `sandbox` + CSP) must render. This is what killed the previous
   React Native attempt; a WebView/Capacitor upgrade can regress it.
4. **OTA chain on the release channel** — publish to the channel the build
   follows, relaunch, verify the new bundle applies; then confirm a lower-count
   manifest is refused (downgrade guard).
5. **Native file paths** — download/share an attachment and a raw `.eml`
   (native HTTP session), upload an attachment (CSRF token path).
6. **Logout → re-login** — logout ends the Django session only; the
   following login must complete silently (IdP session preserved).
7. **No dev server baked in** — in dev, `MOBILE_DEV_SERVER_URL` bakes the Vite
   dev server URL into `capacitor.config.json` (hot reload, see *Build & run
   workflow*). Before archiving, set it empty in `frontend.local` and rerun
   `make mobile-build`. Android release builds fail on a leftover `server.url`
   (gradle guard in `android/app/build.gradle`); Xcode has no equivalent guard,
   so **check manually for iOS** (no `server.url` in
   `ios/App/App/capacitor.config.json`).

## See also

- [`mobile-poc.md`](./mobile-poc.md) — validation procedures: backend-only smoke
  test with curl, running on emulators/devices, re-testing cross-app SSO with a
  throwaway second app, and objective SSO proofs (Keycloak events, negative
  controls).
- [`env.md`](./env.md) — full environment-variable reference.
