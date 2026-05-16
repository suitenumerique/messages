# bulk-role-membership

Keycloak provider exposing a single admin REST endpoint that answers
"for these N usernames, which have realm role R?" in one indexed DB
query.

Avoids the two shapes Keycloak's stock admin API forces on you:
- `/users/{id}/role-mappings/realm` called N times — O(N) round trips.
- `/roles/{name}/users` fetched in full and intersected client-side —
  O(role-size) wire payload, plus the cache layer needed to amortize
  that cost across page renders.

## Endpoint

`POST /realms/{realm}/bulk-role-membership/check`

Headers: `Authorization: Bearer <admin token>` — same token used for any
other `/admin/realms/{realm}/*` call. The endpoint enforces the same
`users:query` realm-management permission as the upstream user-listing
endpoints (`AdminPermissions.evaluator(...).users().requireQuery()`).

Request body:

```json
{
  "role_id": "<uuid of the realm role>",
  "usernames": ["alice@example.com", "bob@example.com", ...]
}
```

Response 200:

```json
{ "members": ["alice@example.com", ...] }
```

`members` is the subset of the input `usernames` that have a direct
mapping to `role_id`. Order is not preserved. Username comparison is
case-insensitive (Keycloak's stored canonical form is lowercase).
Composite-role and group-inherited memberships are **not** expanded —
same semantics as Keycloak's own `GET /roles/{name}/users`.

Errors:
- `400` — missing `role_id` or `usernames`.
- `401` — no bearer token / bad token.
- `403` — token lacks `users:query` on this realm.
- `404` — `role_id` not found in this realm.

## Build

The pre-built JAR is committed at
`src/keycloak/bulk-role-membership/bulk-role-membership.jar`, so
`docker compose up` works on a fresh checkout without any Java
tooling. The JAR has no shaded dependencies — every pom dependency is
`provided`, i.e. expected to already be on Keycloak's classpath. The
file contains only this project's three classes plus the SPI service
descriptor (~8 KB).

When you edit the Java source, rebuild via:

```sh
make build-keycloak
```

This runs Maven inside a `maven:3.9-eclipse-temurin-21` container,
emits the JAR at `target/bulk-role-membership.jar`, and copies it to
the committed path. Commit the new JAR alongside your code change.

`compose.yaml` mounts the committed JAR into
`/opt/keycloak/providers/`; `docker compose restart keycloak` picks
up a rebuilt JAR. For production, `src/keycloak/Dockerfile` `COPY`s
the same file into the image before `kc.sh build` runs.

## Direct query rationale

Internal Keycloak SPIs (`RoleProvider`, `UserProvider`) have no bulk
membership method — `user.hasRole(role)` is a per-user lookup. The
endpoint therefore queries the JPA-backed role-mapping and user-entity
tables directly:

```sql
SELECT ue.USERNAME
  FROM USER_ROLE_MAPPING urm
  JOIN USER_ENTITY ue ON ue.ID = urm.USER_ID
 WHERE urm.ROLE_ID = :rid AND LOWER(ue.USERNAME) IN (:unames)
```

The table names and columns (`USER_ROLE_MAPPING` composite PK on
`ROLE_ID, USER_ID`; `USER_ENTITY` PK on `ID` with `USERNAME` indexed)
are unchanged since Keycloak 1.0 and remain in 26.x. Lookup is
O(log N) per probe via the PK / unique indexes.

Caveats:
- Bypasses Keycloak's user cache. For our use case (admin listing
  reads, not auth-hot path) that's the desired property.
- Couples to the JPA storage schema. If you ever move users to a
  federation backend (LDAP, custom UserStorageProvider), this query
  returns nothing for federated users — the table only holds users
  whose primary store is the JPA `UserEntity`.
- Composite roles are not expanded. Make the TOTP-style flag a
  flat realm role.

## Test

`make test-keycloak` builds the JARs, brings the dev Keycloak service
up via compose, and runs every script in `src/keycloak/tests/test_*.py`
inside the backend-dev container. The bulk-role-membership test creates
a temporary realm role and three users, assigns the role to two of
them, hits the endpoint, asserts the response, then deletes everything
it created. Drop additional `test_*.py` files in `src/keycloak/tests/`
to add coverage for other custom providers.
