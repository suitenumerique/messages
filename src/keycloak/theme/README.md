# lst-dsfr Keycloak theme

## What the theme is

`lst-dsfr` is a Keycloak theme. It uses Keycloakify 11.16.3 and `@codegouvfr/react-dsfr` 1.36.0.

- It has the look of ProConnect Identité.
- It has a login theme and a Single-Page account theme.
- It supports French and English.
- It has no email theme.

The account app is our own React code. It does not use `@keycloakify/keycloak-account-ui`.
The package is installed only because `keycloakify build` asserts its presence for a
Single-Page account theme. Its peer dependencies pin React 18 for this project.

The passkey register page replaces `window.prompt` from the stock `webauthnRegister.js`
with a DSFR field. The override lives in `src/login/pages/WebauthnRegister.tsx`.

The jar ships its own copies of `webauthnRegister.js`, `webauthnAuthenticate.js`,
`passkeysConditionalAuth.js`, `authChecker.js` and `rfc4648.js` under `login/resources/js/`.
Keycloakify copies them from the Keycloak version it targets. A Keycloak bump does not
change them. A Keycloakify bump does.

## Layout

- `src/login/` holds the login pages and `Template.tsx`.
- `src/account/` holds the account app: `auth.ts`, `api.ts`, `router.ts` and the pages.
- `src/shared/` holds the header, the footer and the brand block.
- `src/kc.gen.tsx` is generated. Run `npm run kc-gen` to update it.

## Build

Run `make build-keycloak-theme`. The output is
`dist_keycloak/keycloak-theme-for-kc-all-other-versions.jar`.

Run `make lint-keycloak-theme` to lint and type-check the project.

The production image builds the jar in the `theme-build` stage of `src/keycloak/Dockerfile`.

## Development loop

- Run `npm run dev` to render a mocked login page.
- The dev realm selects the built-in Keycloak themes. To see `lst-dsfr` in
  the dev stack, build the jar. Then set `KC_SPI_THEME__DEFAULT=lst-dsfr` in
  the gitignored file `deploy/env/keycloak.local` and run `make restart`,
  because a plain restart does not read the env files again. The option also
  applies to the email theme, which `lst-dsfr` does not have, so Keycloak logs
  a fallback warning when it sends a mail.
- Set the `DSFR_THEME_*` variables in the same file, then run `make restart`.
- The theme has no language picker. Keycloak selects the language of the login
  pages from the `KEYCLOAK_LOCALE` cookie, the `ui_locales` parameter of the
  authorization request, the `Accept-Language` header, then the realm default
  locale. A login sets the cookie from the `locale` attribute of the user. The
  account page writes that attribute.
- Run `make test-keycloak-theme` to run the Playwright specs of the theme
  against Keycloak alone.
- Rebuild the jar, then restart the `keycloak` service to see your changes.
- `start-dev` picks up the jar at boot.
- "Informations personnelles" is the landing page. A Keycloak action returns
  to `connection-and-account`.
- The second-factor key option of the choice page shows only when the browser flow
  enables the "WebAuthn Authenticator" step. The stock flow has it disabled.
- The logout returns to the account URL, which shows the sign-in page. The
  `account-console` client accepts no other URL after a logout.
  `DSFR_THEME_HIDE_LOGOUT_BUTTON=true` hides the sign-out button and the email
  in the header.

## Environment variables

The theme reads these variables at runtime through `theme.properties`.

| Variable | Default | Description |
| --- | --- | --- |
| `DSFR_THEME_HOME_URL` | `/` | Absolute URL of the service. When unset or `/`, the login pages and the account console both link to the account console of the realm. |
| `DSFR_THEME_SERVICE_TITLE` | `Messages` | Service title. |
| `DSFR_THEME_BRAND_TOP` | `République<br/>Française` | HTML of the Marianne brand block. |
| `DSFR_THEME_HIDE_LOGOUT_BUTTON` | `false` | When `true`, `yes`, `y` or `1`, the account console shows no sign-out button and no email in the header. The applications handle the logout. |
| `DSFR_THEME_TWO_FACTOR_HELP_URL` | empty | Absolute URL of a help page. When set, the "Double authentification" section of the account console shows a "Help" link that opens a new window. |
| `DSFR_THEME_FORGOT_PASSWORD_URL` | empty | Absolute URL of a support page. When set, the login page shows the "Forgot password" link to this URL, even when the realm does not allow the password reset. |
| `DSFR_THEME_LAGAUFRE_WIDGET_CONFIG` | empty | JSON object with the keys `api_url` (La Suite API that lists the services) and `path` (URL of the `lagaufre.js` widget script), like `FRONTEND_LAGAUFRE_WIDGET_CONFIG` of the backend. When set, the account console shows the "Other services" button of La Suite numérique in the header. The script runs in the Keycloak origin, so the origin must be trusted. |

## Upgrade checklist for each Keycloak bump

1. Bump `keycloakify`. Run `npm run kc-gen`.
2. Check that the `keycloak-js` major version matches the server.
3. Diff the script copies in the built jar (`theme/lst-dsfr/login/resources/js/`)
   against the scripts of the Keycloak server version (`themes/.../base/login/resources/js/`):
   - `webauthnRegister.js` (the register page overrides `window.prompt` to get the label from a field);
   - `webauthnAuthenticate.js`;
   - `passkeysConditionalAuth.js`;
   - `authChecker.js`;
   - `rfc4648.js`.
4. Check the account REST API:
   - the paths `/`, `/credentials` and `/supportedLocales`;
   - the `kc_action` aliases `UPDATE_PASSWORD`, `CONFIGURE_TOTP`, `webauthn-register`,
     `webauthn-register-passwordless` and `delete_credential:<id>`.
5. Diff the field names in the base `.ftl` files for the 14 implemented pages.

## Licence

The Marianne typeface and the State brand block are reserved to French State services.
A self-hoster outside the State must change `DSFR_THEME_BRAND_TOP` and the fonts.
`@codegouvfr/react-dsfr` is MIT.
