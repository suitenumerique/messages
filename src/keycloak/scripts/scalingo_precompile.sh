#!/bin/bash

set -e

KEYCLOAK_VERSION="26.2.1"
KEYCLOAK_DIST="https://github.com/keycloak/keycloak/releases/download/${KEYCLOAK_VERSION}/keycloak-${KEYCLOAK_VERSION}.tar.gz"

echo "-----> Downloading Keycloak $KEYCLOAK_VERSION"
curl -L $KEYCLOAK_DIST -o keycloak.tgz

tar -xvf keycloak.tgz
mv keycloak-${KEYCLOAK_VERSION} keycloak
rm keycloak.tgz

# echo "-----> Building Keycloak"
# cd keycloak
# ./bin/kc.sh build
