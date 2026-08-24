# Authentication Provider Setup Guide

Messages uses OpenID Connect in two distinct parts. Do not confuse them.

1. **Authentication.** Messages delegates each user login to an OIDC
   provider. The provider can be ProConnect, Keycloak, or any
   other OIDC provider. Every Messages instance needs one, this is
   managed by the `OIDC_**` environment vars.
2. **Mailbox identities and Maildomains.** A mail domain decides how a user gets a
   mailbox. This part is independent of the authentication provider.
   This is managed by the `IDENTITY_PROVIDER` environment variable,
   for now Messages only supports Keycloak.

This guide covers the first part only. If you are looking for the mailbox
and maildomain provisioning, see [identity-provider.md](identity-provider.md).

## What Messages Expects from the Provider

Messages acts as a standard OIDC relying party. Any compliant provider
works. Messages needs:

- the authorization code flow, with a confidential client;
- an `email` claim in the userinfo response. Messages matches a user to a
  mailbox by this address;
- the five endpoints listed below. Most providers publish them at
  `/.well-known/openid-configuration`.

## Setup Steps

1. **Create a client at your provider.**

   Set the redirect URI, for example
   `https://messages.example.fr/api/v1.0/callback/`.

   Set the Logout redirect URI, for example
   `https://messages.example.fr/api/v1.0/logout-callback/`.

2. **Set the client variables.** Set `OIDC_RP_CLIENT_ID` and
   `OIDC_RP_CLIENT_SECRET` to the credentials of that client.

3. **Set the endpoint variables.** Read them from the provider's
   `/.well-known/openid-configuration` document:

   | Variable | Discovery document key |
   |----------|------------------------|
   | `OIDC_OP_AUTHORIZATION_ENDPOINT` | `authorization_endpoint` |
   | `OIDC_OP_TOKEN_ENDPOINT` | `token_endpoint` |
   | `OIDC_OP_USER_ENDPOINT` | `userinfo_endpoint` |
   | `OIDC_OP_JWKS_ENDPOINT` | `jwks_uri` |
   | `OIDC_OP_LOGOUT_ENDPOINT` | `end_session_endpoint` |

   The browser reaches the authorization endpoint and the logout endpoint.
   Give them a public URL. The backend reaches the other three. A private
   URL is enough for those.

4. **Set the redirect hosts.** Add each host that serves Messages to
   `OIDC_REDIRECT_ALLOWED_HOSTS`. Set `OIDC_REDIRECT_REQUIRE_HTTPS` to
   `True` in production.

5. **Check the scopes.** `OIDC_RP_SCOPES` must request the claims Messages
   needs. The default is `openid email`.

See the [OIDC Configuration](env.md#oidc-configuration) and
[OIDC Advanced Settings](env.md#oidc-advanced-settings) sections of
`env.md` for the full variable tables.

### ProConnect as the Authentication Provider

For ProConnect Integration you can create your application at
[https://partenaires.proconnect.gouv.fr](https://partenaires.proconnect.gouv.fr)
and can use the following variables :

```env
OIDC_RP_CLIENT_ID=XX
OIDC_RP_CLIENT_SECRET=XX

OIDC_OP_AUTHORIZATION_ENDPOINT=https://fca.integ01.dev-agentconnect.fr/api/v2/authorize
OIDC_OP_JWKS_ENDPOINT=https://fca.integ01.dev-agentconnect.fr/api/v2/jwks
OIDC_OP_LOGOUT_ENDPOINT=https://fca.integ01.dev-agentconnect.fr/api/v2/session/end
OIDC_OP_TOKEN_ENDPOINT=https://fca.integ01.dev-agentconnect.fr/api/v2/token
OIDC_OP_USER_ENDPOINT=https://fca.integ01.dev-agentconnect.fr/api/v2/userinfo
OIDC_AUTH_REQUEST_EXTRA_PARAMS={"acr_values": "eidas1"}
```

ProConnect requires a Level of Assurance in the authorization request.
`OIDC_AUTH_REQUEST_EXTRA_PARAMS` carries it, for example
`{"acr_values": "eidas1"}`. The mobile application depends on this value
too. See [mobile.md](mobile.md).

For ProConnect Production, contact the ProConnect team.

### Keycloak as the Authentication Provider

You can use Keycloak for this part too. Create a second client, next to the
service-account client of [identity-provider.md](identity-provider.md).

1. **Create the `messages` client.** Configure it as a confidential
   client. Turn the standard flow on. Turn the direct access grants off.
   Turn the service accounts off.

   Set the redirect URIs to your own host names.

   The dev realm (`src/keycloak/realm.json`) shows example values. Adapt
   the values to your own host names. Do not reuse the example values.

2. **Set the login variables.** Set `OIDC_RP_CLIENT_ID` and
   `OIDC_RP_CLIENT_SECRET` from the `messages` client. Set the five
   endpoint variables from the realm, at
   `https://<KEYCLOAK_URL>/realms/<realm>/.well-known/openid-configuration`.

## How a User Gets a Mailbox After the Login

The login alone does not give a user a mailbox. The mail domain decides
that, and it is a separate mechanism.

In short:
- a mail domain with `oidc_autojoin` gives each user connected through
  the authentication provider a mailbox at login, you don't need to
  setup anything else;
- a mail domain with `identity_sync` lets you create the mailboxes as you
  need, but you'll then need an `IDENTITY_PROVIDER`, see
  [identity-provider.md](identity-provider.md).;
- a mail domain with neither field can manage shared mailboxes only, the
  admin must configure the access on every mailbox to an authenticated user
  manually.
