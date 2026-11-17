#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
if [ -e .env ]; then
    echo '.env already exists; refusing to overwrite credentials.' >&2
    exit 1
fi
umask 077
postgres_password=$(openssl rand -hex 32)
redis_password=$(openssl rand -hex 32)
set -C
{
    printf 'POSTGRES_PASSWORD=%s\n' "$postgres_password"
    printf 'REDIS_PASSWORD=%s\n' "$redis_password"
} > .env
echo 'Created .env with fresh credentials. Do not commit this file.'
