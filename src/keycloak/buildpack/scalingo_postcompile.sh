#!/bin/bash

set -e

KEYCLOAK_VERSION="26.5.4"
KEYCLOAK_DIST="https://github.com/keycloak/keycloak/releases/download/${KEYCLOAK_VERSION}/keycloak-${KEYCLOAK_VERSION}.tar.gz"

echo "-----> Downloading Keycloak $KEYCLOAK_VERSION"
curl -L $KEYCLOAK_DIST -o keycloak.tgz

tar -xvf keycloak.tgz
mv keycloak-${KEYCLOAK_VERSION} keycloak
rm keycloak.tgz

# Copy themes
cp -r themes/* keycloak/providers/

# Package scripts
# if variable SCRIPT_GROUP_ATTRIBUTE_WHITELIST if defined, replace it in the .js file
if [ -n "$SCRIPT_GROUP_ATTRIBUTE_WHITELIST" ]; then
    sed -i "s/SCRIPT_GROUP_ATTRIBUTE_WHITELIST/$SCRIPT_GROUP_ATTRIBUTE_WHITELIST/g" scripts/map-group-attribute.js
fi
cd scripts && zip -r ../keycloak/providers/custom-scripts.jar META-INF *.js && cd ..

echo "-----> Building Keycloak"
PATH=$HOME/.scalingo/with_jstack/bin:$PATH ./keycloak/bin/kc.sh build
