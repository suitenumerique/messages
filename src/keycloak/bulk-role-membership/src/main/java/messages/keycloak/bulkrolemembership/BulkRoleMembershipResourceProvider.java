package messages.keycloak.bulkrolemembership;

import org.keycloak.models.KeycloakSession;
import org.keycloak.services.resource.RealmResourceProvider;

public class BulkRoleMembershipResourceProvider implements RealmResourceProvider {

    private final KeycloakSession session;

    public BulkRoleMembershipResourceProvider(KeycloakSession session) {
        this.session = session;
    }

    @Override
    public Object getResource() {
        return new BulkRoleMembershipResource(session);
    }

    @Override
    public void close() {}
}
