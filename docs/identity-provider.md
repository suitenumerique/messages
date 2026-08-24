# Identity Provider Setup Guide

Messages uses OpenID Connect in two distinct parts. Do not confuse them.

1. **Authentication.** Messages delegates each user login to an OIDC
   provider. The provider can be ProConnect, Keycloak, or any
   other OIDC provider. Every Messages instance needs one, this is
   managed by the `OIDC_**` environment vars.
2. **Mailbox identities and Maildomains.** A mail domain decides how a user gets a
   mailbox. This part is independent of the authentication provider.
   This is managed by the `IDENTITY_PROVIDER` environment variable,
   for now Messages only supports Keycloak.

This guide covers the second part only. It shows how to set up Keycloak as
the store that holds the mailbox identities. If you are looking for the
user login, see [authentication-provider.md](authentication-provider.md).

## How a User Gets a Mailbox

A mail domain has two independent fields. They give three configurations:

| Mail domain | Who creates the mailbox | IDENTITY_PROVIDER needed |
|-------------|-------------------------|-----------------|
| `oidc_autojoin` on | Messages, at each login | No |
| `identity_sync` on | The administrator. Messages then pushes the mailbox to `IDENTITY_PROVIDER` | Yes |
| Neither field on | The administrator, shared mailbox only | No |

**With `oidc_autojoin`.** Each user who logs in with an address on the
domain gets a mailbox at once. Messages creates the mailbox locally. You do
_not_ need the `IDENTITY_PROVIDER` for this.

**With `identity_sync`.** The administrator creates the mailbox
`user1@example.fr`. Messages synchronizes it to Keycloak as a user. The
user then logs in through ProConnect, or through another provider
(the `OIDC_**` vars), with `user1@example.fr`. The user gets access
to the mailbox.

**With neither field.** The administrator creates shared mailboxes only.
Each user logs in through the OIDC provider. The administrator then gives
the user access to a shared mailboxes manually.

You therefore need this guide only when you want `identity_sync`.

## When the Login Address Differs from the Mailbox

Messages matches a user to a mailbox by the address. The two addresses can
differ. The administrator creates the mailbox `user1@aaa.fr`, but the user
logs in with `user1@bbb.fr`. Messages does not link them.

In this case, give the user an explicit access to the mailbox. Grant
`user1@bbb.fr` an access to the `user1@aaa.fr` mailbox.

## Keycloak Setup Steps

For now, Messages only supports Keycloak as an `IDENTITY_PROVIDER`.

1. **Get the Keycloak image.** Use the
   [ghcr.io/suitenumerique/messages-keycloak](https://ghcr.io/suitenumerique/messages-keycloak)
   image that this project publishes to the GitHub container registry.
   You can also build the image from `src/keycloak`.
   The image bundles providers that
   Messages needs:
   - the `map-group-attribute` script mapper, which puts group attributes
     into the token claims;
   - the `bulk-role-membership` admin extension, which makes a bulk
     role-membership check faster.

   A plain upstream `quay.io/keycloak/keycloak` image does not have these
   providers. Do not deploy a plain upstream image.

2. **Deploy Keycloak.** Deploy the image with the
   [st-cli](https://github.com/suitenumerique/st-ansible/tree/main/cli).
   st-cli wraps the same collection in a simpler command-line tool. st-cli
   is the recommended tool for a self-hoster.
   You can also deploy it with
   [st-ansible](https://github.com/suitenumerique/st-ansible) collection or
   yourself with the docker compose examples.

3. **Create a realm.** Create a dedicated Keycloak realm for Messages, for
   example `messages`.

4. **Create the `serviceaccount-messages` client.** This client holds the service account
   that Messages uses for the provisioning calls.
   Turn "Client authentication" on. Turn "Authorization" on.
   Turn "Service accounts roles" on. Turn the "Standard flow" off.
   You can setup the Root URL to the Messages URL.

5. **Assign the realm-management roles.** On the user in the "Service
   account roles" tab, assign the client roles `query-users`,
   `manage-users`, `view-users` and `view-realm`.

6. **Set the provisioning variables.** Set `IDENTITY_PROVIDER` to
   `keycloak`. Set `KEYCLOAK_URL`, `KEYCLOAK_REALM` and
   `KEYCLOAK_GROUP_PATH_PREFIX`. Set `KEYCLOAK_CLIENT_ID` to `serviceaccount-messages`.
   Set `KEYCLOAK_CLIENT_SECRET` to the secret of that client. See the
   [Identity Provider (Keycloak)](env.md#identity-provider-keycloak)
   section of `env.md` for the full variable table.

7. **Enable `identity_sync` on each mail domain.** Enable this field on
   every mail domain that Messages must sync to Keycloak. Set the field in
   the Django admin, on the `MailDomain` change form.

8. **Verify the setup.** Run the resync command:

   ```bash
   python manage.py identity resync-all
   ```

   The command syncs each mail domain and each mailbox again. It reports
   the number of synced domains and the number of synced mailboxes.

## The Authentication Provider

The steps above do not set up a user login. Messages needs an
authentication provider as well. You can use Keycloak for that too, or
keep another provider such as ProConnect. See
[authentication-provider.md](authentication-provider.md).
