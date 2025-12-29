FROM alpine:3 AS scripts

ARG SCRIPT_GROUP_ATTRIBUTE_WHITELIST

COPY ./scripts /scripts

RUN <<EOR
apk --no-cache add zip
cd /scripts
if [ -n "$SCRIPT_GROUP_ATTRIBUTE_WHITELIST" ]; then
    sed -i "s/SCRIPT_GROUP_ATTRIBUTE_WHITELIST/$SCRIPT_GROUP_ATTRIBUTE_WHITELIST/g" scripts/map-group-attribute.js
fi
zip -r /custom-scripts.jar META-INF *.js
EOR

FROM quay.io/keycloak/keycloak:26.4.7 AS builder

WORKDIR /opt/keycloak
COPY --chown=keycloak:keycloak --chmod=644 themes/dsfr-2.2.1.jar /opt/keycloak/providers/dsfr.jar
COPY --from=scripts --chown=keycloak:keycloak --chmod=644 /custom-scripts.jar /opt/keycloak/providers/custom-scripts.jar

ARG KC_METRICS_ENABLED=true
ARG KC_HEALTH_ENABLED=true
ARG KC_DB=postgres

RUN /opt/keycloak/bin/kc.sh build

FROM quay.io/keycloak/keycloak:26.4.7

COPY --from=builder /opt/keycloak/ /opt/keycloak/
ENTRYPOINT ["/opt/keycloak/bin/kc.sh"]

# https://www.keycloak.org/observability/health#_healthcheck
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD { printf 'HEAD /health/ready HTTP/1.0\r\n\r\n' >&0; grep 'HTTP/1.0 200'; } 0<>/dev/tcp/localhost/9000
