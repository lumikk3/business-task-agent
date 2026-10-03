#!/usr/bin/env bash
# Step 18：免 root 在本机起真实 PostgreSQL + Redis（不为生产用，只为本地跑通与验证）。
#
# 为什么不用 apt install：本机 WSL 的 sudo 需要密码。这里用 Ubuntu 官方 .deb 包
# 解包到用户目录，直接以当前用户跑真实二进制 —— 不需要 sudo、不需要 Docker。
#
#   scripts/dev_services.sh up        # 下载/解包/initdb/启动 PG+Redis
#   scripts/dev_services.sh env       # 打印 export DATABASE_URL/REDIS_URL/LD_LIBRARY_PATH
#   eval "$(scripts/dev_services.sh env)"
#   scripts/dev_services.sh status
#   scripts/dev_services.sh down
#
# 想用 sudo/apt 或 Docker 的替代路径见 DEPLOY.md。
set -euo pipefail

PREFIX="${BTA_SERVICES_DIR:-$HOME/.local/share/bta-services}"
DEBS="$PREFIX/debs"
OPT="$PREFIX/opt"
PGDATA="$PREFIX/pgdata"
REDISDATA="$PREFIX/redisdata"
PG_PORT="${BTA_PG_PORT:-55432}"
REDIS_PORT="${BTA_REDIS_PORT:-6399}"
PG_DB="${BTA_PG_DB:-business}"
PG_USER="${BTA_PG_USER:-hermes}"
PGSOCK="$PREFIX/run"

log() { printf '\033[36m[services]\033[0m %s\n' "$*"; }
die() { printf '\033[31m[services] %s\033[0m\n' "$*" >&2; exit 1; }

# 解包出来的 PG/Redis 依赖同名库（libicu/libnuma/liburing/liblzf），
# 本脚本自己执行这些二进制时也要能加载到它们。
export LD_LIBRARY_PATH="$OPT/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

# Ubuntu 各版本包名/版本号不同（postgresql-16 vs 18、libicu74 vs 78），动态解析。
resolve_pkg() {
  local pattern="$1"
  apt-cache search --names-only "$pattern" 2>/dev/null \
    | awk '{print $1}' | sort -V | tail -1
}

need_deb() { [[ -f "$DEBS/$1"* ]]; }

download_debs() {
  mkdir -p "$DEBS"
  local pg_pkg icu_pkg
  pg_pkg="$(resolve_pkg '^postgresql-[0-9]+$')"
  [[ -n "$pg_pkg" ]] || die "找不到 postgresql 包（先跑一次 sudo apt update？）"
  icu_pkg="$(resolve_pkg '^libicu[0-9]+$')"
  local packages=("$pg_pkg" "postgresql-client-${pg_pkg#postgresql-}"
                  libpq5 liblzf1 libnuma1)
  [[ -n "$icu_pkg" ]] && packages+=("$icu_pkg")
  resolve_pkg '^liburing[0-9]+$' >/dev/null && packages+=("$(resolve_pkg '^liburing[0-9]+$')")
  packages+=("$(resolve_pkg '^redis-server$')" "$(resolve_pkg '^redis-tools$')")

  log "目标包: ${packages[*]}"
  ( cd "$DEBS" && apt-get download "${packages[@]}" >/dev/null 2>&1 ) \
    || die "apt-get download 失败（需要能访问 apt 源）"
  log "已下载到 $DEBS"
}

unpack_debs() {
  mkdir -p "$OPT"
  local f
  for f in "$DEBS"/*.deb; do dpkg -x "$f" "$OPT"; done
  log "已解包到 $OPT"
}

pg_bindir() {
  local d
  d="$(find "$OPT/usr/lib/postgresql" -maxdepth 1 -mindepth 1 -type d 2>/dev/null | sort -V | tail -1)"
  [[ -n "$d" ]] || die "找不到 postgresql 二进制目录"
  printf '%s/bin' "$d"
}
pg_sharedir() {
  local d
  d="$(find "$OPT/usr/share/postgresql" -maxdepth 1 -mindepth 1 -type d 2>/dev/null | sort -V | tail -1)"
  printf '%s' "$d"
}

start_redis() {
  if "$OPT/usr/bin/redis-cli" -p "$REDIS_PORT" ping >/dev/null 2>&1; then
    log "Redis 已在 $REDIS_PORT 运行"; return
  fi
  mkdir -p "$REDISDATA"
  "$OPT/usr/bin/redis-server" --port "$REDIS_PORT" --bind 127.0.0.1 \
    --daemonize yes --dir "$REDISDATA" --save '' --appendonly no
  sleep 1
  "$OPT/usr/bin/redis-cli" -p "$REDIS_PORT" ping >/dev/null || die "Redis 启动失败"
  log "Redis 已启动: 127.0.0.1:$REDIS_PORT"
}

start_pg() {
  local bin share
  bin="$(pg_bindir)"; share="$(pg_sharedir)"
  mkdir -p "$PGSOCK"
  if [[ ! -f "$PGDATA/PG_VERSION" ]]; then
    log "initdb -> $PGDATA"
    mkdir -p "$PGDATA"
    "$bin/initdb" -D "$PGDATA" -L "$share" -U "$PG_USER" \
      --auth=trust --encoding=UTF8 --locale=C >/dev/null
  fi
  if "$bin/pg_ctl" -D "$PGDATA" status >/dev/null 2>&1; then
    log "PostgreSQL 已在运行"; return
  fi
  "$bin/pg_ctl" -D "$PGDATA" -l "$PGDATA/server.log" \
    -o "-p $PG_PORT -k $PGSOCK -c listen_addresses=127.0.0.1 -c timezone=UTC" start >/dev/null
  sleep 1
  "$bin/pg_ctl" -D "$PGDATA" status >/dev/null || { tail -20 "$PGDATA/server.log"; die "PostgreSQL 启动失败"; }
  log "PostgreSQL 已启动: 127.0.0.1:$PG_PORT (user=$PG_USER)"
  "$bin/createdb" -h 127.0.0.1 -p "$PG_PORT" -U "$PG_USER" "$PG_DB" 2>/dev/null \
    && log "已创建数据库 $PG_DB" || log "数据库 $PG_DB 已存在"
}

stop_all() {
  local bin
  bin="$(pg_bindir 2>/dev/null || true)"
  if [[ -n "$bin" && -f "$PGDATA/PG_VERSION" ]] && "$bin/pg_ctl" -D "$PGDATA" status >/dev/null 2>&1; then
    "$bin/pg_ctl" -D "$PGDATA" -m fast stop >/dev/null && log "PostgreSQL 已停止"
  fi
  if [[ -x "$OPT/usr/bin/redis-cli" ]] && "$OPT/usr/bin/redis-cli" -p "$REDIS_PORT" ping >/dev/null 2>&1; then
    "$OPT/usr/bin/redis-cli" -p "$REDIS_PORT" shutdown nosave 2>/dev/null || true
    log "Redis 已停止"
  fi
}

status_all() {
  local bin
  bin="$(pg_bindir 2>/dev/null || true)"
  if [[ -n "$bin" ]] && "$bin/pg_ctl" -D "$PGDATA" status >/dev/null 2>&1; then
    log "PostgreSQL: UP   (127.0.0.1:$PG_PORT, db=$PG_DB, user=$PG_USER)"
  else
    log "PostgreSQL: DOWN"
  fi
  if [[ -x "$OPT/usr/bin/redis-cli" ]] && "$OPT/usr/bin/redis-cli" -p "$REDIS_PORT" ping >/dev/null 2>&1; then
    log "Redis:      UP   (127.0.0.1:$REDIS_PORT)"
  else
    log "Redis:      DOWN"
  fi
}

print_env() {
  printf 'export LD_LIBRARY_PATH="%s/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"\n' "$OPT"
  printf 'export DATABASE_URL="postgresql://%s@127.0.0.1:%s/%s"\n' "$PG_USER" "$PG_PORT" "$PG_DB"
  printf 'export REDIS_URL="redis://127.0.0.1:%s/0"\n' "$REDIS_PORT"
}

case "${1:-up}" in
  up)
    download_debs; unpack_debs; start_redis; start_pg
    log "完成。接下来："
    echo "  eval \"\$(scripts/dev_services.sh env)\""
    echo "  python scripts/migrate_to_pg.py                 # 把 SQLite 数据灌进 PG"
    echo "  python scripts/run_pg_redis_demo.py             # 验证 Agent 跑在 PG+Redis 上"
    ;;
  env)    print_env ;;
  status) status_all ;;
  down)   stop_all ;;
  *)      die "用法: $0 {up|env|status|down}" ;;
esac
