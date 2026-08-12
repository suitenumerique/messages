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
   stashes `{scheme, code_challenge, state, created_at}` in the Django session.
   The `state` is the one generated for this OIDC round-trip: the callback only
   consumes the flag when its own state matches, and a flag older than 10 min
   is ignored, so an abandoned or overlapping mobile attempt can't hijack a web
   flow running in the same browser (same binding for the mobile logout).
3. **IdP authenticates** — interactively the first time, silently afterwards
   (see *Cross-app SSO conditions* below).
4. **Callback mints a one-time token.** `OIDCAuthenticationCallbackView` caches
   `{session_key, code_challenge}` under `mobile-auth-token:<token>` with a
   `MOBILE_AUTH_TOKEN_TTL` (60 s) timeout and hands the browser back to
   `stmessages://auth?token=…` — through a small **hand-off page**
   (auto-redirect + "open the app" button), not a plain 302 to the scheme.
   The direct redirect gets blocked by the **CSP of the IdP login page**:
   Chrome enforces its `form-action` on the whole redirect chain of the
   credential form submission, and `*` only matches network schemes, so the
   final custom-scheme hop violates it and the sheet stays stuck on the IdP.
   ProConnect sends such a CSP; the dev Keycloak does not, which hides the
   bug in dev. Ending the chain on a 200 page satisfies the policy, and the
   deep link then leaves from our own page. iOS is indifferent —
   `ASWebAuthenticationSession` intercepts the scheme navigation either way —
   and the logout keeps its direct scheme redirects: its round-trip involves
   no form submission, so no `form-action` ever applies.
5. **App exchanges the token.** `MobileSessionExchangeView` (anonymous, single
   use) deletes the cache key *before* verifying it (a failed attempt can't be
   retried), checks `S256(code_verifier) == code_challenge` with
   `secrets.compare_digest`, rehydrates the session, and emits the
   `Set-Cookie: sessionid` header plus a `csrf_token` in the body.

The token is a bearer secret for ~60 s; **PKCE is what makes a stolen deep link
useless** (the attacker lacks the verifier), which matters because custom URL
schemes can be claimed by other apps.

The return scheme (`MOBILE_AUTH_SCHEME`, default `stmessages`) must be
registered natively on **both** platforms — both substitute it at build time
from that one variable, and `sso-invariants.test.ts` pins the wiring (source of
truth: `AUTH_CALLBACK_SCHEME` in `auth.ts`): an `intent-filter` in
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

### Logout ends the session everywhere (Django **and** IdP)

`nativeLogout()` runs the RP-initiated logout (`/api/v1.0/logout/` with
`mobile_scheme`) in the system browser, which holds both the Django session
cookie handed over at login and the IdP SSO cookie: the round-trip terminates
both and ends on a `scheme://logout` deep link that closes the sheet. Keeping
the IdP session alive is not an option — it silently signs the same identity
back in on the next login and ProConnect ignores `prompt=login`, so tearing it
down is the only way to let the user switch accounts. The app then POSTs to
`/api/v1.0/mobile/auth/logout/` as a safety net (the browser round-trip only
ends the app-side session when the browser still holds the same session
cookie), clears the native cookies and the cached CSRF token. By design this
also ends the SSO session shared with other La Suite apps.

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

**CSRF `Origin` on HTTPS.** Against a secure backend (staging/prod), Django
additionally requires an `Origin` or `Referer` on every mutation ("Referer
checking failed - no Referer" otherwise) — headers the native HTTP client never
sends on its own. `getHeaders()` injects `Origin: <API origin>` on native
(same-origin for the backend, so no `CSRF_TRUSTED_ORIGINS` entry is needed),
but the bridge's patched `window.fetch` normalizes headers through
`new Request()`, whose browser "request" guard silently drops forbidden names —
`Origin` included. Mutations (POST/PUT/PATCH/DELETE) therefore bypass the patch
and call the `CapacitorHttp` plugin directly via `nativeFetch()`
(`src/features/native/fetch.ts`), which passes headers verbatim to the same
native stack; reads stay on the patched fetch and keep request cancellation.
The plain-HTTP dev backend never triggers the check, which is why this only
shows up outside dev.

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
(commented default in `deploy/env/backend.defaults`), so experiments
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
| Native push client (enable / on-launch refresh / tap deep-link) | `src/frontend/src/features/native/push.ts` |
| Push opt-in marker + token-hash contract (shared web/native) | `src/frontend/src/features/push/shared.ts` |
| OTA client | `src/frontend/src/features/native/ota.ts` |
| Startup wiring (OTA, `native` html class) | `src/frontend/src/main.tsx` |
| CSRF / API origin wiring | `src/frontend/src/features/api/utils.ts` |
| Login/logout routing | `src/frontend/src/features/auth/index.tsx` |
| iOS ASWebAuthenticationSession plugin | `src/frontend/ios/App/App/WebAuthSessionPlugin.swift` |
| iOS plugin registration | `src/frontend/ios/App/App/MainViewController.swift` |
| iOS push entitlement + APNs bridge + banner strings | `src/frontend/ios/App/App/App.entitlements`, `AppDelegate.swift`, `{en,fr}.lproj/Localizable.strings` |
| Android push banner strings (FCM loc-keys) | `src/frontend/android/app/src/main/res/values{,-fr}/strings.xml` |
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
- **The frontend dependencies live in `src/frontend/node_modules` on the host**,
  installed there by the container through the bind mount (`make update`,
  `make install-front`, …). Gradle and SPM resolve the Capacitor plugins through
  relative paths into that tree (`../node_modules/@capacitor/<plugin>/android`,
  `../../../node_modules/@capacitor/<plugin>`), so it must not be masked by a
  Docker volume — that is why `node_modules` is deliberately kept inside the bind
  mount, see `compose.yaml`. If those paths are missing, run
  `make install-frozen-front`. The installed binaries are the container's (Linux)
  ones: Gradle and Xcode only read sources from them, but never point a host
  `npm`/`vite` at that tree.
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
`NEXT_PUBLIC_*` vars from `deploy/env/frontend.{defaults,local}` are
inlined at build time (Vite `envPrefix: 'NEXT_PUBLIC_'`). Building on the host
with a bare `npm run build` would inline none of them. The native compile, IDE,
`adb` and Xcode steps run on the **host**.

> **Always run `make mobile-build` after a fresh checkout.** `cap sync` (not a
> bare copy) also regenerates the gitignored
> `capacitor-cordova-android-plugins/` scaffolding that Gradle needs.

| Command | What it does |
| --- | --- |
| `make mobile-build` | web build (container) + `cap sync` into `ios/` and `android/` |
| `make mobile-assets` | regenerate native icons & splashscreens from the vector mark — see [`mobile-assets.md`](./mobile-assets.md) |
| `make mobile-android` | `mobile-build`, then open the Android project in Android Studio (host) |
| `make mobile-android-run` | `mobile-build` + `gradlew assembleDebug` + `adb install` + `adb reverse` (host) |
| `make mobile-android-release` | `mobile-build` + `gradlew bundleRelease` → signed `.aab` for Play (host, see [Publishing to Google Play](#publishing-to-google-play)) |
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
in `deploy/env/frontend.defaults` — is baked by `cap sync` into the app
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
`deploy/env/frontend.local` (gitignored, overrides the defaults):

```bash
# deploy/env/frontend.local
MOBILE_DEV_SERVER_URL=
```

then rerun `make mobile-build` (or any target that wraps it) and reinstall the
app. A leftover `server.url` fails Android **release** builds (gradle guard in
`android/app/build.gradle`); see the release checklist for iOS.

## Push notifications in dev (optional)

Push is **off by default** (`PUSH_ENABLED=False`): the apps build, run and hide
the notification settings without any of this. Full architecture:
[push-notifications.md](./push-notifications.md). What ships in the repo
(entitlements, loc-key banner strings, permission, conditional google-services
apply) needs no setup; what follows is the per-developer credential part.

**The app self-configures per environment where it can** — the client picks its
transport at runtime (`apns` on iOS / `fcm` on Android), and a dev-signed iOS
build automatically registers against Apple's *sandbox* gateway
(`aps-environment = development` in `App.entitlements`; Xcode's distribution
export rewrites it to `production`). What it **cannot** infer is the backend
half: the gateway credentials and the sandbox flag below must match the build
you install.

### Android (FCM)

1. Create a (free) dev Firebase project and register an **Android app whose
   package name is exactly the `applicationId`** of your build — the
   `MOBILE_APP_ID` default, `local.suitenumerique.messages`. A
   `google-services.json` for another package fails the Android build at the
   google-services step.
2. Download `google-services.json` into `src/frontend/android/app/`
   (gitignored, per-instance). Rebuild/reinstall.
3. In Firebase console → project settings → service accounts, generate a
   service-account key and set in `deploy/env/backend.local`:
   `PUSH_ENABLED=True`, `PUSH_FCM_CREDENTIALS` (the JSON, single line),
   `PUSH_FCM_PROJECT_ID`. Restart the backend + celery worker.
4. Emulator: use the same **Play-services image** the SSO setup already
   requires (see *Prerequisites*) — FCM registration fails on a bare AOSP
   image (the UI then shows the `registration_failed` message, by design).

### iOS (APNs)

1. **Physical iPhone required** for the end-to-end path: simulators never get a
   real APNs token, so registration against Apple's gateway can't be exercised
   there (`xcrun simctl push` only injects local payloads).
2. Apple developer account: enable the **Push Notifications capability on the
   App ID** matching your bundle id, and create an **APNs auth key** (`.p8`).
3. In `deploy/env/backend.local`: `PUSH_ENABLED=True`,
   `PUSH_APNS_KEY` (the `.p8` PEM), `PUSH_APNS_KEY_ID`, `PUSH_APNS_TEAM_ID`,
   `PUSH_APNS_BUNDLE_ID` (= your `MOBILE_APP_ID`), and
   **`PUSH_APNS_USE_SANDBOX=True`** — dev-signed builds hold sandbox tokens;
   against the production gateway they are rejected as `BadDeviceToken`.
   Restart the backend + celery worker.

### Smoke test (both platforms)

1. In the app: account menu → Notifications → *Enable notifications on this
   device* → accept the OS prompt. The device must appear in the list.
2. Kill the app, send the mailbox a message from another account: a
   content-free "New message / Nouveau message" banner must show (rendered by
   the OS from the loc-key strings — a blank banner means those strings are
   missing from the build).
3. Tap it: the app must open on the thread (deep-link path).

## Configuration

Mobile-specific environment variables (full reference in [env.md](./env.md)):

| Variable | Purpose |
| --- | --- |
| `MOBILE_APP_ID` | Store/OS bundle identifier (default `local.suitenumerique.messages`). Read by `cap sync` (container) **and** the native builds — gradle reads the host env (the `mobile-android-*` targets export it), Xcode reads the gitignored `ios/App/generated.xcconfig` written by `make mobile-build`. Release builds fail on a divergence from the synced config on both platforms (gradle guard / "Check synced Capacitor identity" build phase). Independent of the auth scheme (`MOBILE_AUTH_SCHEME`) |
| `MOBILE_APP_NAME` | Displayed application name (default `ST Messages`, a neutral placeholder an organisation overrides with its own). Reaches the native builds through the same two channels as `MOBILE_APP_ID` (gradle `resValue app_name` / iOS `PRODUCT_DISPLAY_NAME` in the generated xcconfig), with the same release-time divergence guards |
| `MOBILE_AUTH_SCHEME` | OIDC deep-link scheme (default `stmessages`). Read by Vite **and** the native builds (Android `manifestPlaceholders` from the host env, iOS `AUTH_CALLBACK_SCHEME` from the generated xcconfig). Give each environment its own so two builds can coexist on a device |
| `MOBILE_AUTH_CALLBACK_SCHEMES` | Backend allowlist: JSON list of accepted deep-link schemes (e.g. `["stmessages"]`); empty disables mobile login. Must contain every `MOBILE_AUTH_SCHEME` in use |
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
- **Cleartext transport is dev-only, build-gated on both platforms.** On
  Android, `preReleaseBuild` fails when the synced `capacitor.config.json`
  carries a dev `server.url` or `server.cleartext` (i.e. when
  `MOBILE_DEV_SERVER_URL` / `MOBILE_ALLOW_CLEARTEXT_FOR_DEV` was in the
  `cap sync` env). On iOS, the "Strip dev ATS exception" build phase deletes
  the `NSAppTransportSecurity` dict (`NSAllowsLocalNetworking`) from the built
  product in every non-Debug configuration, so it never ships in an Archive.
- **Session renewal.** The 12 h Django session has no refresh-token renewal
  yet. Confirm ProConnect SSO session duration and persistent-cookie behaviour
  (esp. iOS) in production.
- **Safe-area insets.** Disabling Capacitor's `SystemBars` inset handling (to fix
  the double keyboard inset, Capacitor #8181) means Android no longer receives
  the `--safe-area-inset-*` CSS variables; `MainActivity.java` re-injects them
  from the window insets (system bars + display cutout). The same listener also
  owns the keyboard resize on **Android 15+**: the OS draws every app edge to
  edge there, and an edge-to-edge window is never resized by the keyboard, so
  `windowSoftInputMode=adjustResize` does nothing and the composer toolbar would
  hide behind the keyboard — the listener applies the `ime()` inset as padding,
  and only above API 34 (below it the system resize still runs, and adding
  padding is exactly what #8181 was). iOS resolves `env(safe-area-inset-*)`
  natively and resizes through `@capacitor/keyboard`. The app
  shell folds the top inset into `--header-height` (`globals.scss`), so
  anything laid out from it clears the status bar / notch automatically.
- **Iframe subresources.** Inline images proxied through the API use the WebView
  network stack, not the native one, and may not load in dev; the HTML body
  itself renders.
- **App Store guideline 4.2.** A pure web wrapper needs native-feeling
  differentiators (push notifications, share targets…) to pass review.

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
Add every scheme in use to the backend's `MOBILE_AUTH_CALLBACK_SCHEMES` list.

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
`CFBundleShortVersionString`) has a single source of truth: the `version`
field of `src/frontend/package.json`, **bumped manually** when releasing.
Gradle reads the file directly; iOS receives it through the generated
xcconfig (`make mobile-build`), so bumping it needs no other change on
either platform. It is a marketing string — users read it in the store
listing — and carries no ordering constraint.

The **technical version** (`versionCode`) is separate and automatic: it
defaults to the commit count, so it grows on its own; override it
(`make mobile-android-release MOBILE_VERSION_CODE=42`) for a pinned build.
Play refuses a `versionCode` it has already accepted, so every upload needs
a fresh one — including a rebuild of the same commit. The iOS equivalent
(`CURRENT_PROJECT_VERSION`, the build number) is still fixed at `1` in the
Xcode project and will need the same treatment when TestFlight enters the
picture.

Four guards fail the build rather than shipping something broken: a leftover dev
`server.url`, cleartext traffic, a missing signing key, and an `applicationId`
that does not match the `appId` `cap sync` baked into `capacitor.config.json`
(the `MOBILE_APP_ID`-exported-on-only-one-side trap).

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
6. **Logout → re-login** — logout ends both the Django session and the IdP
   session (RP-initiated logout in the system browser); the following login
   must stop on the IdP login form, allowing an account switch. A silent
   re-login means the IdP session survived: that is a regression.
7. **No dev server baked in** — in dev, `MOBILE_DEV_SERVER_URL` bakes the Vite
   dev server URL into `capacitor.config.json` (hot reload, see *Build & run
   workflow*). Before archiving, set it empty in `frontend.local` and rerun
   `make mobile-build`. Android release builds fail on a leftover `server.url`
   (gradle guard in `android/app/build.gradle`); Xcode has no equivalent guard,
   so **check manually for iOS** (no `server.url` in
   `ios/App/App/capacitor.config.json`).
8. **Push environment pairing** — nothing fails loudly on a mismatch, pushes
   just never arrive (or hit `BadDeviceToken` in the sender logs). For a store
   release: the backend serving those users must run
   `PUSH_APNS_USE_SANDBOX=False` (a distribution-signed build holds
   *production* APNs tokens — Xcode rewrites `aps-environment` at export, no
   manual step); the bundled `google-services.json` must come from the
   **production** Firebase project and contain a client for the release
   `MOBILE_APP_ID`; `PUSH_APNS_BUNDLE_ID` must equal that same id. Then run the
   smoke test of *Push notifications in dev* against the release build.

## See also

- [`mobile-poc.md`](./mobile-poc.md) — validation procedures: backend-only smoke
  test with curl, running on emulators/devices, re-testing cross-app SSO with a
  throwaway second app, and objective SSO proofs (Keycloak events, negative
  controls).
- [`mobile-assets.md`](./mobile-assets.md) — app icons and splash screens: the
  vector mark everything is derived from, the platform safe zones, and what each
  OS actually reads at launch.
- [`env.md`](./env.md) — full environment-variable reference.
