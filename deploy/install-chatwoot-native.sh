#!/usr/bin/env bash
# Chatwoot NATIVO en esta máquina — sin Docker, sin root.
#   binarios:      ~/.local (ruby 3.4.4, valkey)
#   gems/bundle:   ~/.local/bundle
#   postgres:      cluster user-space ~/.local/pgdata en :5434 (trust, localhost)
#   chatwoot:      ~/chatwoot en :3001 (el :3000 queda libre por si algún día
#                  vuelve el stack de compose)
# Idempotente: re-ejecutar continúa donde falló (stages con marcas).
set -euo pipefail
L="$HOME/.local"; RUBY="$L/ruby-3.4.4"; SRC="$HOME/src"; APP="$HOME/chatwoot"
export PATH="$L/bin:$RUBY/bin:$PATH"
stage(){ echo; echo "===== [$(date +%T)] $* ====="; }

stage "1/8 libyaml (psych lo necesita y no está en el sistema)"
mkdir -p "$SRC"; cd "$SRC"
if [ ! -d yaml-0.2.5 ]; then
  curl -fsSLO https://github.com/yaml/libyaml/releases/download/0.2.5/yaml-0.2.5.tar.gz
  tar xzf yaml-0.2.5.tar.gz
fi
cd yaml-0.2.5
if [ ! -f "$L/lib/libyaml.so" ]; then
  ./configure --prefix="$L" >/dev/null
  make -j6 >/dev/null && make install >/dev/null
fi

stage "2/8 ruby 3.4.4 exacto (Gemfile de Chatwoot lo pinea; Arch solo trae 3.4.10)"
cd "$SRC"
if [ ! -d ruby-3.4.4 ]; then
  curl -fsSLO https://cache.ruby-lang.org/pub/ruby/3.4/ruby-3.4.4.tar.gz
  tar xzf ruby-3.4.4.tar.gz
fi
cd ruby-3.4.4
if [ ! -x "$RUBY/bin/ruby" ]; then
  ./configure --prefix="$RUBY" --enable-shared --with-libyaml-dir="$L" \
    --disable-install-doc >/dev/null
  make -j6 >/dev/null && make install >/dev/null
fi
ruby -v

stage "3/8 valkey 9.1 (fork de Redis; Chatwoot solo habla protocolo redis)"
mkdir -p "$L/bin"; cd "$SRC"
if [ ! -x "$L/bin/valkey-server" ]; then
  [ -d valkey ] || git clone --depth 1 --branch 9.1.2 https://github.com/valkey-io/valkey.git
  (cd valkey && make -j6 >/dev/null && cp src/valkey-server src/valkey-cli "$L/bin/")
fi
if ! "$L/bin/valkey-cli" -p 6390 ping 2>/dev/null | grep -q PONG; then
  "$L/bin/valkey-server" --daemonize yes --port 6390 --dir "$L" \
    --logfile "$L/valkey.log" --pidfile "$L/valkey.pid" --save ''
fi
"$L/bin/valkey-cli" -p 6390 ping

stage "4/8 postgres user-space :5434"
if [ ! -d "$L/pgdata" ]; then
  initdb -D "$L/pgdata" -U chatwoot -A trust
fi
pg_ctl -D "$L/pgdata" -l "$L/pgdata.log" \
  -o "-p 5434 -k /tmp -h 127.0.0.1" start >/dev/null 2>&1 || true
createdb -h 127.0.0.1 -p 5434 -U chatwoot chatwoot 2>/dev/null || true
psql -h 127.0.0.1 -p 5434 -U chatwoot -d chatwoot -tc "select 'pg-ok'"

stage "5/8 pnpm 10 (frontend lo pide package.json)"
if ! command -v pnpm >/dev/null 2>&1; then
  npm install -g pnpm@10.2.0 --prefix "$L" >/dev/null
fi
pnpm --version

stage "6/8 bundle install + pnpm install (~5-10 min)"
cd "$APP"
if [ ! -f .env ]; then
  cat > .env <<ENV
RAILS_ENV=development
PORT=3001
SECRET_KEY_BASE=$(openssl rand -hex 64)
FRONTEND_URL=http://localhost:3001
DATABASE_URL=postgresql://chatwoot@127.0.0.1:5434/chatwoot
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5434
POSTGRES_DATABASE=chatwoot
POSTGRES_USERNAME=chatwoot
POSTGRES_PASSWORD=
REDIS_URL=redis://127.0.0.1:6390/0
ACTIVE_STORAGE_SERVICE=local
ENABLE_ACCOUNT_SIGNUP=false
SAFE_FETCH_ALLOW_PRIVATE_NETWORK=true
ENV
fi
gem install bundler >/dev/null 2>&1 || true
bundle config set --local path "$L/bundle"
bundle install -j6
[ -d node_modules ] || pnpm install

stage "7/8 db + servidores (:3001)"
export RAILS_ENV=development
# Fresh install al estilo del CI de Chatwoot: schema:load, no db:migrate
# (las migraciones viejas referencian constants eliminados de gems actuales).
bin/rails db:drop db:create db:schema:load
if ! curl -s -m 2 http://127.0.0.1:3001/ >/dev/null 2>&1; then
  nohup bin/rails server -p 3001 -b 127.0.0.1 >"$L/chatwoot-rails.log" 2>&1 &
  nohup bundle exec sidekiq >"$L/chatwoot-sidekiq.log" 2>&1 &
fi
for i in $(seq 1 45); do
  curl -s -m 2 http://127.0.0.1:3001/ >/dev/null 2>&1 && break
  sleep 2
done
curl -s -m 5 -o /dev/null -w "rails http %{http_code}\n" http://127.0.0.1:3001/

stage "8/8 estado"
echo "logs: $L/chatwoot-rails.log $L/chatwoot-sidekiq.log"
tail -3 "$L/chatwoot-rails.log" 2>/dev/null || true
echo "CHATWOOT-NATIVE-OK"
