#!/bin/sh
set -eu

: "${POSTGRES_HOST:?POSTGRES_HOST is required}"
: "${POSTGRES_PORT:=5432}"
: "${POSTGRES_USER:?POSTGRES_USER is required}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}"
: "${PGBOUNCER_MAX_CLIENT_CONN:=400}"
: "${PGBOUNCER_DEFAULT_POOL_SIZE:=40}"
: "${PGBOUNCER_MIN_POOL_SIZE:=5}"
: "${PGBOUNCER_RESERVE_POOL_SIZE:=10}"
: "${PGBOUNCER_MAX_DB_CONNECTIONS:=60}"
: "${PGBOUNCER_MAX_PREPARED_STATEMENTS:=200}"

if printf '%s%s' "$POSTGRES_USER" "$POSTGRES_PASSWORD" | grep -q '[\\"[:cntrl:]]'; then
    echo "POSTGRES_USER and POSTGRES_PASSWORD cannot contain quotes, backslashes, or control characters" >&2
    exit 64
fi

mkdir -p /tmp/pgbouncer
chmod 0700 /tmp/pgbouncer
umask 077

printf '"%s" "%s"\n' "$POSTGRES_USER" "$POSTGRES_PASSWORD" \
    > /tmp/pgbouncer/userlist.txt

cat > /tmp/pgbouncer/pgbouncer.ini <<EOF
[databases]
* = host=${POSTGRES_HOST} port=${POSTGRES_PORT}

[pgbouncer]
listen_addr = 0.0.0.0
listen_port = 6432
unix_socket_dir = /tmp/pgbouncer
pidfile = /tmp/pgbouncer/pgbouncer.pid

auth_type = scram-sha-256
auth_file = /tmp/pgbouncer/userlist.txt
admin_users = ${POSTGRES_USER}
stats_users = ${POSTGRES_USER}

pool_mode = transaction
max_client_conn = ${PGBOUNCER_MAX_CLIENT_CONN}
default_pool_size = ${PGBOUNCER_DEFAULT_POOL_SIZE}
min_pool_size = ${PGBOUNCER_MIN_POOL_SIZE}
reserve_pool_size = ${PGBOUNCER_RESERVE_POOL_SIZE}
reserve_pool_timeout = 3
max_db_connections = ${PGBOUNCER_MAX_DB_CONNECTIONS}
max_prepared_statements = ${PGBOUNCER_MAX_PREPARED_STATEMENTS}

server_connect_timeout = 5
server_check_query = select 1
server_check_delay = 30
server_idle_timeout = 600
server_lifetime = 3600
query_wait_timeout = 15
query_timeout = 35
client_idle_timeout = 900
idle_transaction_timeout = 60
ignore_startup_parameters = extra_float_digits

log_connections = 0
log_disconnections = 0
log_pooler_errors = 1
stats_period = 60
EOF

exec pgbouncer /tmp/pgbouncer/pgbouncer.ini
