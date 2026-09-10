# Mobile apps (iOS / Android)

The Messages mobile apps are **the existing web frontend wrapped in a
[Capacitor](https://capacitorjs.com/) native shell**. There is no second
codebase: the same React/Vite bundle that serves `localhost:8900` runs inside a
`WKWebView` (iOS) / Android `WebView`, and a thin native layer supplies what a
browser cannot — a shared-cookie login, a native HTTP stack, file sharing and
over-the-air (OTA) bundle updates.

- **Part 1 — Getting started**: what to install and run to work on the apps
  today. Read this first, it is short.
- **Part 2 — Technical concepts**: how authentication, networking, OTA and
  versioning actually work, and why. Read the section you touch.
- Shipping (OTA publishing, rollbacks, store builds, release checklist) lives in
  [`mobile-release.md`](./mobile-release.md).

---

# Part 1 — Getting started

## What you need

**Everyone**

- The dev stack: `make bootstrap` once, then `make start` (backend on `:8901`,
  Keycloak on `:8902`). `make start-full` additionally brings object storage on
  `:8906`, only needed to test OTA locally.
- **No Node on the host.** The web bundle is built inside the `frontend-mobile`
  container; the host only runs the native toolchains below. If you use a host
  `npm` anyway, it must be **Node 22** (`>=22 <23`) — any other version
  corrupts the lockfile.
- Nothing to configure on the backend: the default
  `MOBILE_AUTH_CALLBACK_SCHEMES=["stmessages"]` already allows the dev app to
  log in.

**Android** (any OS) — if you have never set up an Android toolchain, follow
[Capacitor's guide](https://capacitorjs.com/docs/getting-started/environment-setup#android-requirements)
first; this is what the project specifically needs:

- [Android Studio](https://developer.android.com/studio/install) (latest stable)
  with **SDK 36** + build-tools (`compileSdk 36` / `targetSdk 36`, `minSdk 24`),
  and JDK 17+ (bundled).
- [`adb`](https://developer.android.com/tools/adb) on the host `PATH`.
- An emulator image **with Play services** (Google Play / Google APIs). A bare
  AOSP image has no Chrome, so login falls back to an isolated-cookie WebView and
  cross-app SSO silently breaks. A physical device always ships Chrome.

**iOS** (macOS only) — same,
[Capacitor's guide](https://capacitorjs.com/docs/getting-started/environment-setup#ios-requirements)
first, then:

- [Xcode](https://developer.apple.com/xcode/) 16+ with the iOS 16+ SDK
  (deployment target iOS 15). Dependencies come through Swift Package Manager
  (`ios/App/CapApp-SPM/Package.swift`), resolved on first open — **no
  CocoaPods**.
- For a physical iPhone: an Apple developer account and a signing team in Xcode.

## First run

Every `make mobile-*` target builds the web bundle **in the container** (so the
`NEXT_PUBLIC_*` / `MOBILE_*` vars from `deploy/env/frontend.{defaults,local}`
are inlined) and then runs the native step **on the host**.

**Android** — start an emulator (or plug a device with USB debugging on), then:

```bash
make mobile-android-run
```

It builds the bundle, runs `gradlew assembleDebug`, installs the APK and opens
the `adb reverse` tunnel (ports 8900, 8901, 8902, 8906) the WebView uses to
reach the dev stack. Prefer the IDE? `make mobile-android` builds and opens the
project in Android Studio instead.

**iOS**:

```bash
make mobile-ios
```

It builds the bundle and opens Xcode: run the `App` scheme on a simulator. No
tunnel needed, the simulator reaches the host's `localhost` directly.

Log in with any dev Keycloak account: the login opens in the system browser and
comes back to the app by deep link.

## Daily workflow

- **Hot reload is on by default.** The dev build loads the app straight from the
  Vite dev server (`MOBILE_DEV_SERVER_URL=http://localhost:8900` in
  `frontend.defaults`), so JS/CSS/SCSS changes apply through HMR without
  rebuilding or reinstalling. The dev stack must be up or the app is blank.
- **Rebuild + reinstall** (`make mobile-android-run` / `make mobile-ios`) only
  for native changes: a Capacitor plugin, `capacitor.config.ts`, anything under
  `ios/` or `android/`, or a `MOBILE_*` variable.
- **After a fresh checkout or a pull that bumps a Capacitor plugin**, run
  `make mobile-build`: `cap sync` regenerates the gitignored native scaffolding
  Gradle needs and the plugin paths baked into the native projects.
- **Android tunnel**: `adb reverse` is dropped on every emulator reboot / adb
  reconnection and Android Studio does not re-apply it. When the app suddenly
  can't reach the backend, run `make mobile-android-reverse`. Several devices
  attached? `export ANDROID_SERIAL=<serial>` (`adb devices` to list).
- **Physical iPhone**: no tunnel exists, so point the app at the Mac's LAN IP in
  `deploy/env/frontend.local`: `MOBILE_DEV_SERVER_URL=http://<mac-ip>:8900`.
- **Tests**: the native layer is plain TypeScript covered by the frontend unit
  tests (`make test-front`). `sso-invariants.test.ts` is a CI tripwire on the
  native declarations (scheme, iOS plugin flag) — it fails when something in
  `ios/` / `android/` diverges from `auth.ts`.

### Hot reload

Hot reload works because `cap sync` bakes `MOBILE_DEV_SERVER_URL` into the app
as Capacitor's `server.url`. Two consequences worth knowing:

- The startup OTA check is skipped in a hot-reload session (`ota.ts` skips when
  `import.meta.env.DEV` **and** `MOBILE_DEV_SERVER_URL` are set) — applying a
  downloaded bundle would yank the WebView off the dev server.
- To test the **embedded bundle** (what a store build ships) or the OTA chain
  end to end, disable it by setting the variable **empty** in the gitignored
  `deploy/env/frontend.local`, then rebuild and reinstall:

  ```bash
  # deploy/env/frontend.local
  MOBILE_DEV_SERVER_URL=
  ```

  A leftover `server.url` fails Android **release** builds (gradle guard); iOS
  has no such guard, see the release checklist in
  [`mobile-release.md`](./mobile-release.md#release-checklist-manual).

## Command reference

| Command | What it does |
| --- | --- |
| `make mobile-build` | web build (container) + `cap sync` into `ios/` and `android/` |
| `make mobile-android-run` | `mobile-build` + `gradlew assembleDebug` + `adb install` + `adb reverse` (host) |
| `make mobile-android` | `mobile-build`, then open the Android project in Android Studio (host) |
| `make mobile-android-reverse` | (re)apply the `adb reverse` port mapping |
| `make mobile-ios` | `mobile-build`, then open the Xcode project (host, macOS) |
| `make mobile-assets` | regenerate native icons & splashscreens from the vector mark — see [`mobile-assets.md`](./mobile-assets.md) |
| `make mobile-android-release` | signed `.aab` for Play — see [`mobile-release.md`](./mobile-release.md#publishing-to-google-play) |
| `make mobile-ota-keygen` / `-bucket` / `-publish` / `-rollback` | OTA operations — see [`mobile-release.md`](./mobile-release.md#ota-releases) |

## Where the code lives

Everything native-specific on the web side sits in
`src/frontend/src/features/native/`; the single source of truth for "am I in the
app?" is `isNativePlatform()` (`platform.ts`). `main.tsx` also tags
`<html class="native">` so stylesheets can opt into mobile-only chrome.

| Concern | Location |
| --- | --- |
| Capacitor config (appId, `appVersion`, plugins, HTTP, SystemBars, OTA signing key) | `src/frontend/capacitor.config.ts` |
| Platform detection | `src/frontend/src/features/native/platform.ts` |
| Native login / logout, PKCE, system-browser session | `src/frontend/src/features/native/auth.ts`, `pkce.ts`, `auth-session.ts` |
| Deep-link dispatcher (single `appUrlOpen` owner) | `src/frontend/src/features/native/deep-link.ts` |
| Native CSRF token store | `src/frontend/src/features/native/csrf.ts` |
| Mutations bypassing the patched fetch | `src/frontend/src/features/native/fetch.ts` |
| Native download → share sheet | `src/frontend/src/features/native/download.ts` |
| Native push client | `src/frontend/src/features/native/push.ts`, `src/frontend/src/features/push/shared.ts` |
| OTA client | `src/frontend/src/features/native/ota.ts`, `src/frontend/src/features/hooks/use-ota-update-toast.tsx` |
| Startup wiring (OTA, deep links, `native` html class) | `src/frontend/src/bootstrap.tsx` |
| CSRF / API origin headers | `src/frontend/src/features/api/utils.ts` |
| Login/logout routing | `src/frontend/src/features/auth/index.tsx` |
| Versions shown to users | `src/frontend/src/features/hooks/use-app-version.ts` |
| SSO invariants tripwire (CI) | `src/frontend/src/features/native/sso-invariants.test.ts` |
| iOS project, `ASWebAuthenticationSession` plugin + registration | `src/frontend/ios/`, `App/App/WebAuthSessionPlugin.swift`, `MainViewController.swift` |
| iOS push entitlement, APNs bridge, banner strings | `src/frontend/ios/App/App/App.entitlements`, `AppDelegate.swift`, `{en,fr}.lproj/Localizable.strings` |
| Android project (safe-area / keyboard insets in `MainActivity.java`) | `src/frontend/android/` |
| Android push banner strings | `src/frontend/android/app/src/main/res/values{,-fr}/strings.xml` |
| Backend mobile-aware OIDC views | `src/backend/core/authentication/views.py` |
| Backend token → session exchange & mobile logout | `src/backend/core/api/viewsets/mobile_auth.py` |
| OTA publish scripts | `src/frontend/scripts/publish-ota.mjs`, `create-ota-bucket.mjs`, `ota-lib.mjs` |
| Scalingo OTA staging + flip | `deploy/paas/scalingo_stage_ota`, `deploy/paas/scalingo_ota_promote.py` |

## When things go wrong

| Symptom | Likely cause → fix |
| --- | --- |
| App is blank | Dev stack down (`make start`), or on Android the tunnel dropped (`make mobile-android-reverse`) |
| Requests fail after an emulator reboot | Tunnel dropped → `make mobile-android-reverse` |
| Gradle / Xcode can't find a Capacitor plugin | Plugin paths point into `src/frontend/node_modules/.store/…` → `make install-frozen-front` then `make mobile-build` (see *Why the plugin paths* below) |
| Login opens the browser and never comes back | `MOBILE_AUTH_SCHEME` mismatch between Vite and the native build (use the `make` targets, never a bare `./gradlew`), scheme missing from the backend `MOBILE_AUTH_CALLBACK_SCHEMES`, or not lowercase |
| Second La Suite app asks for credentials again (Android) | Emulator without Play services → use a Google Play / Google APIs image |
| Second La Suite app asks for credentials again (iOS) | IdP cookie is not persistent (tick "Remember me"), or the `eidas1` ACR mapping was removed from the dev realm |
| `localhost:8900` "logs in silently" so SSO must work | False positive: that is the Django session cookie, not IdP SSO. Only the mobile flow proves it |
| Mutations fail with "Referer checking failed" | Only against an HTTPS backend: they must go through `nativeFetch()` (see *Networking & session*) |
| OTA never triggers | By design in a hot-reload session; disable `MOBILE_DEV_SERVER_URL` to test it |
| Push registration fails on the emulator | Same Play-services requirement as SSO |

**Why the plugin paths.** The frontend dependencies live in
`src/frontend/node_modules` **on the host**, installed by the container through
the bind mount (`make bootstrap`, `make update`, …). Gradle and SPM resolve the
Capacitor plugins through relative paths into that tree, so it is deliberately
kept inside the bind mount rather than masked by a Docker volume (see
`compose.yaml`). npm installs with `install-strategy=linked`
(`src/frontend/.npmrc`), so those paths point at
`node_modules/.store/<pkg>@<ver>-<hash>/…`; the hash derives from the lockfile,
so the committed paths are valid on any machine with the same
`package-lock.json` and only change when a plugin dependency is bumped —
`make mobile-build` regenerates them. The binaries in that tree are the
container's (Linux) ones: Gradle and Xcode only read sources from them, never
point a host `npm`/`vite` at it.

## Push notifications in dev (optional)

Push is **off by default** (`PUSH_ENABLED=False`): the apps build, run and hide
the notification settings without any of this. Full architecture:
[push-notifications.md](./push-notifications.md). What ships in the repo
(entitlements, loc-key banner strings, permission, conditional google-services
apply) needs no setup; what follows is the per-developer credential part.

The app self-configures per environment where it can — the client picks its
transport at runtime (`apns` on iOS / `fcm` on Android), and a dev-signed iOS
build automatically registers against Apple's *sandbox* gateway
(`aps-environment = development` in `App.entitlements`; Xcode's distribution
export rewrites it to `production`). What it **cannot** infer is the backend
half: the gateway credentials and the sandbox flag below must match the build
you install.

**Android (FCM)**

1. Create a (free) dev Firebase project and register an **Android app whose
   package name is exactly the `applicationId`** of your build — the
   `MOBILE_APP_ID` default, `local.suitenumerique.messages`. A
   `google-services.json` for another package fails the Android build.
2. Download `google-services.json` into `src/frontend/android/app/`
   (gitignored, per-instance). Rebuild/reinstall.
3. In Firebase console → project settings → service accounts, generate a
   service-account key and set in `deploy/env/backend.local`:
   `PUSH_ENABLED=True`, `PUSH_FCM_CREDENTIALS` (the JSON, single line),
   `PUSH_FCM_PROJECT_ID`. Restart the backend + celery worker.
4. Emulator: a **Play-services image** is required — FCM registration fails on
   a bare AOSP image (the UI then shows `registration_failed`, by design).

**iOS (APNs)**

1. **Physical iPhone required**: simulators never get a real APNs token
   (`xcrun simctl push` only injects local payloads).
2. Apple developer account: enable the **Push Notifications capability on the
   App ID** matching your bundle id, and create an **APNs auth key** (`.p8`).
3. In `deploy/env/backend.local`: `PUSH_ENABLED=True`,
   `PUSH_APNS_KEY` (the `.p8` PEM), `PUSH_APNS_KEY_ID`, `PUSH_APNS_TEAM_ID`,
   `PUSH_APNS_BUNDLE_ID` (= your `MOBILE_APP_ID`), and
   **`PUSH_APNS_USE_SANDBOX=True`** — dev-signed builds hold sandbox tokens;
   the production gateway rejects them as `BadDeviceToken`.
   Restart the backend + celery worker.

**Smoke test (both platforms)**

1. In the app: account menu → Notifications → *Enable notifications on this
   device* → accept the OS prompt. The device must appear in the list.
2. Kill the app, send the mailbox a message from another account: a
   content-free "New message / Nouveau message" banner must show (rendered by
   the OS from the loc-key strings — a blank banner means those strings are
   missing from the build).
3. Tap it: the app must open on the thread (deep-link path).

---

# Part 2 — Technical concepts

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

## Authentication

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
   (`generateCodeVerifier`), computes its S256 challenge, **persists the
   verifier** (see *Surviving the background* below), and opens
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

### Surviving the background

The whole flow runs while the app is **backgrounded** — the system browser is
on top — and a backgrounded WebView is not a safe place to keep state. Two
things can wipe the JS context mid-flow: the native updater installing a staged
OTA bundle (`appMovedToBackground()` → `installNext()` reloads the WebView,
whatever `autoUpdate` says), and Android reclaiming the process. Either one
used to strand the login *and* poison the following attempts, because
`@capacitor/app` notifies `appUrlOpen` with `retainUntilConsumed`: a callback
nobody was listening for is retained and replayed to the **next** listener that
subscribes, so the next attempt exchanged the previous attempt's expired token
— forever, until the app was killed. Three rules keep that shut:

- **The verifier outlives its context.** `nativeLogin()` persists
  `{codeVerifier, startedAt}` before opening the browser, and the exchange
  reads it back from storage. It is the only value the exchange cannot
  re-derive. The record expires after 15 min (the user's time on the IdP, not
  the 60 s life of the token) and is cleared on every exit path.
- **One deep-link listener, for the whole app lifetime.** `deep-link.ts` owns
  the single `appUrlOpen` subscription, registered at boot in `bootstrap.tsx`;
  flows *capture* routing from it while they run. Nothing is ever left
  unconsumed for a later attempt to inherit — a link arriving outside a flow
  either resumes a pending login (`resumeNativeLogin`, which is how a login
  whose context died still completes) or is dropped.
- **OTA installs are held for the duration.** `openAuthSession()` brackets the
  browser round-trip with `holdOtaInstall()` / `releaseOtaInstall()`, a Capgo
  delay condition that makes `installNext()` return early. The staged bundle
  applies at the next background *after* the flow instead.

### Cross-app SSO conditions

Both bit us during the initial validation:

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
primitives). OTA replaces the *web* bundle only: anything native (a new
Capacitor plugin, a permission, the Swift/Gradle side) still requires a store
release. How bundles are published, versioned and rolled back is covered in
[`mobile-release.md`](./mobile-release.md#ota-releases); this section is the
client side.

**Every bundle is encrypted and signed.** Because the bucket is world-readable,
bundles use Capgo v2 (RSA+AES) with a per-instance key: the public half is baked
into the app at `cap sync` time (`capacitor.config.ts`, `publicKey` ←
`MOBILE_OTA_SIGNING_PUBLIC_KEY_B64`), the private half signs at publish time
(`MOBILE_OTA_SIGNING_PRIVATE_KEY_B64`, CI-only). A substituted zip therefore
fails native verification instead of running arbitrary code. `ota.ts` refuses to
apply a manifest when the build embeds no public key, so an OTA-enabled app can
never apply an unverified bundle.

**Consume** (`src/features/native/ota.ts`, called at startup and on app
foreground with a 30 min throttle):

1. `notifyOtaAppReady()` first — confirms the running bundle booted, so a broken
   update auto-rolls-back on next launch.
2. `checkAndStageOtaUpdate()` polls the manifest URL served by the backend
   (`MOBILE_OTA_MANIFEST_URL` setting, `/config` endpoint, resolved in
   `bootstrap.tsx`) and accepts the advertised bundle only when it clears the
   guards: different from `CapacitorUpdater.current()`, a *strictly greater*
   `sequence` (a per-channel monotonic release counter the device persists —
   which is what lets a deliberate rollback point at an older build while a
   replayed manifest can never drag a device backward), never older than the
   native builtin bundle, and not recorded as a prior failed boot (a bundle
   that boot-looped is blacklisted on that device).
3. It downloads (passing `checksum` + `sessionKey`, verified against the
   baked-in public key) and **stages** it via `next()` — no mid-session reload.
   A persistent, non-dismissible toast (`use-ota-update-toast.tsx`, mounted by
   the main layout) offers the single action "Update", which `reload()`s onto
   the staged bundle; never tapping it is fine, Capgo applies the staged bundle
   when the app next goes to the background or relaunches (except during a
   login flow, see *Surviving the background*).

**Channels.** Each app follows exactly one channel through the manifest URL its
backend serves (`channels/<channel>/manifest.json`): `dev` locally, `staging`
and `prod` in the pipeline. The `NEXT_PUBLIC_*` vars are inlined into the bundle
at build time, so a bundle is never promoted across channels — each is rebuilt
for its environment.

## App versions

Two numbers coexist on a native platform, because OTA moves the web bundle ahead
of the installed app between two store releases — a single number could not
stand for both:

| Shown | Source | Read |
| --- | --- | --- |
| `Version 1.2.0` (native) | `appVersion` in `capacitor.config.ts`, bumped manually per store release | at runtime from the OS, via `@capacitor/app` |
| `Web interface 0.1.0` (native only) | `version` in `package.json` | baked in at build time (`__WEB_APP_VERSION__`) |
| `Version 0.1.0` (web) | `version` in `package.json` | idem |

The version is readable in-app, as the last entry of the help/support menu
(`SurveyButton`, `src/frontend/src/features/ui/components/feedback-button/`),
through `useAppVersion()`. Clicking the entry copies a one-line report — `app
1.2.0 (42) · web 0.1.0 (a1b2c3d) · ios` — including the build stamp of the
running bundle, so a bug report pins the exact code.

That stamp is the `SOURCE_VERSION` the build received: Scalingo's buildpack
sets it natively, the CI image build passes it as a build-arg, and the `make`
targets inject the host's `MOBILE_OTA_BUILD_ID` (the container itself can't
derive it — the bind mount carries no `.git` and the image no `git` binary).
Only a bare `npm run build` in the container gets the `t<timestamp>` fallback,
still unique per build but not traceable to a commit.

The store build number (`MOBILE_VERSION_CODE`) and how to bump the displayed
version are release concerns: see
[`mobile-release.md`](./mobile-release.md#app-versioning).

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
| `NEXT_PUBLIC_API_ORIGIN` | API base URL — **must be set explicitly and absolute** for mobile builds (no meaningful `window.location.origin` in the WebView) |
| `MOBILE_OTA_MANIFEST_URL` | Backend setting served through `/config`: OTA channel manifest polled at startup and on app foreground (30 min throttle) — the followed channel changes without a new native build; unset disables OTA (deprecated build-time fallback: `NEXT_PUBLIC_MOBILE_OTA_MANIFEST_URL`) |
| `MOBILE_OTA_CHANNEL` | Release channel `mobile-ota-publish` targets (`dev` locally, `staging`/`prod` in the pipeline); must match the channel the build follows |
| `MOBILE_OTA_S3_*`, `MOBILE_OTA_PUBLIC_BASE_URL` | OTA publish: S3 write credentials/endpoint (frontend env, not Django) and the device-reachable public base URL written into the manifest |
| `MOBILE_OTA_SIGNING_PUBLIC_KEY_B64` | Base64 PEM public key baked into the app (`capacitor.config.ts`, native verification) and inlined by Vite (`ota.ts` refuses a server-provided manifest URL without it); required for any OTA-enabled build |
| `MOBILE_OTA_SIGNING_PRIVATE_KEY_B64` | Base64 PEM private key that signs bundles at publish time (`publish-ota.mjs`, CI-only) |

## Production hardening / known gaps

The following are POC-scoped shortcuts that must be resolved before shipping.
Treat this list as the "definition of ready for production".

- **OTA over HTTPS.** Bundle signing/encryption (Capgo v2, RSA+AES), the
  monotonic `sequence` floor persisted per device (anti-replay) and the
  native-builtin floor (never below the store build) are in place, so a
  substituted or replayed old zip is refused. What remains for production is to
  serve the bucket/CDN over **HTTPS** (dev uses cleartext RustFS).
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

## See also

- [`mobile-release.md`](./mobile-release.md) — OTA publishing, channels,
  signing keys, rollback, Scalingo integration, Google Play, release checklist.
- [`mobile-assets.md`](./mobile-assets.md) — app icons and splash screens: the
  vector mark everything is derived from, the platform safe zones, and what each
  OS actually reads at launch.
- [`push-notifications.md`](./push-notifications.md) — push architecture.
- [`env.md`](./env.md) — full environment-variable reference.
