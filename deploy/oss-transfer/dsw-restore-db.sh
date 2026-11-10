#!/usr/bin/env bash
# Restore the verified OSS snapshot into a private native PostgreSQL 16 cluster on DSW.
set -euo pipefail
umask 077
base=/mnt/workspace/hc-platform
cluster=/mnt/workspace/hc-platform-pg16
port=55432
if [[ "${HC_DSW_RESTORE_MODE:-preview}" == live ]]; then
  base=/mnt/workspace/hc-live-backup
  cluster=/mnt/workspace/hc-live-pg16
  port=55433
fi
backup=$base/db-backups
cfg=$base/config
data=$cluster/data
socket=$cluster/socket
pg=/usr/lib/postgresql/16/bin
die() { echo "$*" >&2; exit 1; }
[[ $EUID == 0 && -s "$base/.snapshot-id" && -s "$cfg/oss.env" ]] || die 'Run as root after unpacking the snapshot and copying oss.env.'
cd /
exec 9>"$cfg/.postgres-restore.lock"
flock -n 9 || die 'Another database restore is running.'
chmod 600 "$cfg/oss.env"
id=$(cat "$base/.snapshot-id")
[[ "$id" =~ ^[a-f0-9]{64}$ ]] || die 'Invalid snapshot ID.'
manifest=$backup/transfer.sha256
if [[ ! -f "$manifest" ]]; then
  original=/root/hc-dsw-transfer/SHA256SUMS
  [[ "$(sha256sum "$original" | cut -d' ' -f1)" == "$id" ]] || die 'Snapshot manifest mismatch.'
  install -m 600 "$original" "$manifest"
fi
[[ "$(sha256sum "$manifest" | cut -d' ' -f1)" == "$id" ]] || die 'Unexpected backup manifest.'
for item in hc_data.dump temporal.dump temporal_visibility.dump dataset-heads.txt; do
  (cd "$backup"; awk -v f="$item" '$2==f {print; n++} END {if(n!=1) exit 1}' "$manifest" | sha256sum --strict -c -)
done

if [[ ! -x "$pg/pg_ctl" || ! -x "$pg/pg_restore" ]]; then
  . /etc/os-release
  [[ "$ID" == ubuntu && "$VERSION_CODENAME" == jammy ]] || die 'Expected Ubuntu 22.04 on DSW.'
  echo 'Installing PostgreSQL 16 from the signed PostgreSQL repository...'
  (
    umask 022
    keydir=/usr/share/postgresql-common/pgdg
    install -d -m 755 "$keydir"
    keytmp=$(mktemp)
    trap 'rm -f "$keytmp"' EXIT
    curl --retry 3 --fail --silent --show-error --location https://www.postgresql.org/media/keys/ACCC4CF8.asc -o "$keytmp"
    keyid=$(gpg --batch --with-colons --show-keys "$keytmp" | awk -F: '$1=="fpr" && !seen {print $10; seen=1}')
    [[ "$keyid" == B97B0AFCAA1A47F044F244A07FCC7D46ACCC4CF8 ]] || die 'Unexpected PGDG signing key.'
    install -m 644 "$keytmp" "$keydir/hc-platform.asc"
    printf '%s\n' 'deb [signed-by=/usr/share/postgresql-common/pgdg/hc-platform.asc] https://apt.postgresql.org/pub/repos/apt jammy-pgdg main' >/etc/apt/sources.list.d/hc-platform-pgdg.list
    export DEBIAN_FRONTEND=noninteractive
    apt-get -o Acquire::Retries=3 update
    apt-get -o Acquire::Retries=3 install -y --no-install-recommends postgresql-16 postgresql-client-16 locales
  )
fi
if ! locale -a | awk 'tolower($0)=="en_us.utf8" {found=1} END {exit !found}'; then
  localedef -i en_US -f UTF-8 en_US.utf8
fi

if [[ ! -e "$cluster" && ! -L "$cluster" ]]; then
  mkdir -m 700 "$cluster"
  printf '%s\n' "$id" > "$cluster/source.id"
fi
[[ ! -L "$cluster" && -f "$cluster/source.id" && "$(cat "$cluster/source.id")" == "$id" ]] || die 'Existing cluster belongs to another snapshot. No databases were replaced.'
chgrp postgres "$cluster"
chmod 710 "$cluster"
install -d -o postgres -g postgres -m 700 "$data" "$socket"
aspg() { runuser -u postgres -- "$@" 9>&-; }
admin() { aspg "$pg/psql" -Xqw -h "$socket" -p "$port" -U postgres -d postgres -v ON_ERROR_STOP=1 "$@"; }
if [[ ! -f "$data/PG_VERSION" ]]; then
  aspg "$pg/initdb" -D "$data" --encoding=UTF8 --locale=en_US.utf8 --auth-local=peer --auth-host=scram-sha-256 >/dev/null
fi
[[ "$(cat "$data/PG_VERSION")" == 16 ]] || die 'Expected a PostgreSQL 16 data directory.'
if ! aspg "$pg/pg_ctl" status -D "$data" >/dev/null; then
  aspg "$pg/pg_ctl" start -D "$data" -o "-h 127.0.0.1 -k $socket -p $port" -l "$data/server.log" -w -t 90
fi
[[ "$(admin -Atc 'SHOW data_directory')" == "$data" ]] || die 'Connected to an unexpected PostgreSQL cluster.'

passfile=$cfg/postgres.pgpass
role=$(admin -Atc "SELECT rolname FROM pg_roles WHERE rolname='hc'")
if [[ ! -f "$passfile" ]]; then
  [[ -z "$role" ]] || die 'The hc role exists but its connection file is missing.'
  password=$(openssl rand -hex 32)
  printf '127.0.0.1:%s:*:hc:%s\n' "$port" "$password" >"$passfile.tmp"
  mv "$passfile.tmp" "$passfile"
fi
chmod 600 "$passfile"
IFS=: read -r dbhost dbport dbname dbuser password <"$passfile"
[[ "$dbhost:$dbport:$dbname:$dbuser" == "127.0.0.1:$port:*:hc" && "$password" =~ ^[a-f0-9]{64}$ ]] || die 'Invalid PostgreSQL connection file.'
if [[ -z "$role" ]]; then
  printf "CREATE ROLE hc LOGIN PASSWORD '%s';\n" "$password" | admin -f - >/dev/null
fi
unset password
export PGPASSFILE="$passfile"
appsql() { "$pg/psql" -Xqw -h 127.0.0.1 -p "$port" -U hc -v ON_ERROR_STOP=1 "$@"; }
appsql -d postgres -Atc 'SELECT 1' >/dev/null
restore_stream() {
  local name=$1
  printf 'BEGIN;\nSET LOCAL ROLE hc;\n' || return 1
  "$pg/pg_restore" --no-owner --no-acl -f - "$backup/$name.dump" || return 1
  printf "\nRESET ROLE;\nALTER DATABASE %s SET hc_platform.snapshot_id = '%s';\nCOMMIT;\n" "$name" "$id"
}
for db in hc_data temporal temporal_visibility; do
  owner=$(admin -Atc "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname='$db'")
  if [[ -z "$owner" ]]; then
    admin -c "CREATE DATABASE $db OWNER hc TEMPLATE template0"
  elif [[ "$owner" != hc ]]; then
    die "Unrelated database $db already exists."
  fi
  restored=$(appsql -d "$db" -Atc "SELECT current_setting('hc_platform.snapshot_id',true)")
  if [[ "$restored" == "$id" ]]; then
    echo "$db already restored."
    continue
  fi
  empty=$(appsql -d "$db" -Atc "SELECT NOT EXISTS (SELECT FROM pg_namespace WHERE nspname !~ '^pg_' AND nspname NOT IN ('public','information_schema')) AND NOT EXISTS (SELECT FROM pg_class WHERE relnamespace='public'::regnamespace) AND NOT EXISTS (SELECT FROM pg_proc WHERE pronamespace='public'::regnamespace) AND NOT EXISTS (SELECT FROM pg_type WHERE typnamespace='public'::regnamespace) AND NOT EXISTS (SELECT FROM pg_extension WHERE extname<>'plpgsql')")
  [[ -z "$restored" && "$empty" == t ]] || die "$db contains other data. No restore was attempted against it."
  echo "Restoring $db..."
  restore_stream "$db" | admin -d "$db" -f - >/dev/null
  [[ "$(appsql -d "$db" -Atc "SELECT current_setting('hc_platform.snapshot_id',true)")" == "$id" ]] || die "$db restore did not commit."
done

actual=$backup/dataset-heads.restored.txt
appsql -d hc_data -At >"$actual.tmp" <<'SQL'
SET app.organization_id = '01';
SET app.project_id = '01';
SET app.region_code = 'global';
SELECT t.task_code,h.collection_version_id,jsonb_array_length(c.document->'episodes')
FROM episode_store.collection_heads h
JOIN episode_store.collections c USING(organization_id,project_id,region_code,dataset_id,collection_version_id)
JOIN collection_tasks.collection_tasks t USING(organization_id,project_id,dataset_id)
WHERE h.organization_id='01' AND h.project_id='01' AND h.region_code='global'
ORDER BY 1;
SQL
diff -u "$backup/dataset-heads.txt" "$actual.tmp"
mv "$actual.tmp" "$actual"
echo 'Database restore verified.'
cut -d'|' -f1,3 "$actual"
