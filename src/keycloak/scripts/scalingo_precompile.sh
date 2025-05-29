#!/bin/bash

KEYCLOAK_VERSION="26.2.0"
KEYCLOAK_DIST="https://github.com/keycloak/keycloak/releases/download/${KEYCLOAK_VERSION}/keycloak-${KEYCLOAK_VERSION}.zip"

echo "-----> Downloading Keycloak $KEYCLOAK_VERSION"
curl -L $KEYCLOAK_DIST -o keycloak.zip

echo "-----> Unzipping Keycloak"
unzip -q keycloak.zip -d .
mv keycloak-${KEYCLOAK_VERSION} keycloak
rm keycloak.zip

# echo "-----> Building Keycloak"
# cd keycloak
# ./bin/kc.sh build

exit 0
