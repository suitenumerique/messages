package messages.keycloak.bulkrolemembership;

import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;

import jakarta.persistence.EntityManager;
import jakarta.ws.rs.Consumes;
import jakarta.ws.rs.NotAuthorizedException;
import jakarta.ws.rs.POST;
import jakarta.ws.rs.Path;
import jakarta.ws.rs.Produces;
import jakarta.ws.rs.core.MediaType;
import jakarta.ws.rs.core.Response;

import org.keycloak.connections.jpa.JpaConnectionProvider;
import org.keycloak.models.KeycloakSession;
import org.keycloak.models.RealmModel;
import org.keycloak.models.RoleModel;
import org.keycloak.services.ErrorResponse;
import org.keycloak.services.managers.AppAuthManager;
import org.keycloak.services.managers.AuthenticationManager;
import org.keycloak.services.resources.admin.AdminAuth;
import org.keycloak.services.resources.admin.fgap.AdminPermissionEvaluator;
import org.keycloak.services.resources.admin.fgap.AdminPermissions;

public class BulkRoleMembershipResource {

    // Hard cap on input size. PostgreSQL caps a single prepared statement at
    // ~32k parameters; well before that, an unbounded IN-list signals a
    // caller bug we'd rather surface as 400 than as a 500 deep inside JDBC.
    // Our admin pagination ships ≤ ~100 in practice; 1000 is generous.
    private static final int MAX_USERNAMES = 1000;

    private final KeycloakSession session;

    public BulkRoleMembershipResource(KeycloakSession session) {
        this.session = session;
    }

    @POST
    @Path("check")
    @Consumes(MediaType.APPLICATION_JSON)
    @Produces(MediaType.APPLICATION_JSON)
    public Response check(BulkCheckRequest req) {
        if (req == null || req.roleId == null || req.usernames == null) {
            throw ErrorResponse.error(
                    "role_id and usernames are required", Response.Status.BAD_REQUEST);
        }

        if (req.usernames.size() > MAX_USERNAMES) {
            throw ErrorResponse.error(
                    "usernames exceeds the " + MAX_USERNAMES + " cap",
                    Response.Status.BAD_REQUEST);
        }

        RealmModel realm = session.getContext().getRealm();
        requireUserQueryPermission(realm);

        RoleModel role = realm.getRoleById(req.roleId);
        if (role == null || !realm.getId().equals(role.getContainerId())) {
            throw ErrorResponse.error("realm role not found", Response.Status.NOT_FOUND);
        }

        // Keycloak stores usernames in lowercase canonical form (since 21+);
        // lowercase on both sides defends against any case-mismatch at the
        // call site without depending on a specific Keycloak normalization.
        // ``filter(nonNull)`` keeps a stray null from blowing up the stream.
        // ``Locale.ROOT`` keeps the casing rule locale-independent (avoids the
        // Turkish-locale ``I → ı`` surprise on non-ASCII usernames).
        List<String> lowered = req.usernames.stream()
                .filter(Objects::nonNull)
                .map(s -> s.toLowerCase(Locale.ROOT))
                .toList();

        if (lowered.isEmpty()) {
            return Response.ok(Map.of("members", List.of())).build();
        }

        // Single indexed query joining role-mapping ↔ user-entity. Both tables
        // and their (ROLE_ID, USER_ID) / (ID, USERNAME) columns are stable
        // since Keycloak 1.0. Result is the subset of input usernames whose
        // user has a direct mapping to role_id — composite-role and
        // group-inherited memberships are NOT expanded (matches the
        // semantics of the upstream /roles/{name}/users endpoint).
        //
        // The ``REALM_ID`` filter is defense in depth: the role-scope check
        // above already implies same-realm membership (Keycloak invariants),
        // but joining on REALM_ID explicitly removes any room for a foreign
        // username collision to surface a user from another realm. Matches
        // the post-KEYCLOAK-4559 upstream convention for raw JPA queries.
        EntityManager em = session.getProvider(JpaConnectionProvider.class).getEntityManager();
        @SuppressWarnings("unchecked")
        List<String> matched = em.createNativeQuery(
                "SELECT ue.USERNAME FROM USER_ROLE_MAPPING urm "
                        + "JOIN USER_ENTITY ue ON ue.ID = urm.USER_ID "
                        + "WHERE urm.ROLE_ID = :rid "
                        + "AND ue.REALM_ID = :realmId "
                        + "AND LOWER(ue.USERNAME) IN (:unames)")
                .setParameter("rid", req.roleId)
                .setParameter("realmId", realm.getId())
                .setParameter("unames", lowered)
                .getResultList();

        return Response.ok(Map.of("members", matched)).build();
    }

    private void requireUserQueryPermission(RealmModel realm) {
        AppAuthManager.BearerTokenAuthenticator authenticator =
                new AppAuthManager.BearerTokenAuthenticator(session);
        AuthenticationManager.AuthResult result = authenticator.authenticate();
        if (result == null) {
            throw new NotAuthorizedException("Bearer token required");
        }
        AdminAuth admin = new AdminAuth(
                realm, result.getToken(), result.getUser(), result.getClient());
        AdminPermissionEvaluator eval = AdminPermissions.evaluator(session, realm, admin);
        eval.users().requireQuery();
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public static class BulkCheckRequest {
        @JsonProperty("role_id")
        public String roleId;

        @JsonProperty("usernames")
        public List<String> usernames;
    }
}
