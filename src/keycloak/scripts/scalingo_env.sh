#!/usr/bin/env bash

host=$( echo $SCALINGO_POSTGRESQL_URL | cut -d "@" -f2 | cut -d ":" -f1 )
username=$( echo $SCALINGO_POSTGRESQL_URL | cut -d "/" -f3 | cut -d ":" -f1 )
port=$( echo $SCALINGO_POSTGRESQL_URL | cut -d ":" -f4 | cut -d "/" -f1 )
password=$( echo $SCALINGO_POSTGRESQL_URL | cut -d "@" -f1 | cut -d ":" -f3 )
database=$( echo $SCALINGO_POSTGRESQL_URL | cut -d "?" -f1 | cut -d "/" -f4 )

export KC_DB="postgres"
export KC_DB_USERNAME="${username}"
export KC_DB_PASSWORD="${password}"
export KC_DB_URL="jdbc:postgresql://${host}:${port}/${database}"
