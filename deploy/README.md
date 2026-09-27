# Despliegue local: autoarranque y backups ($0)

Unidades **de usuario** de systemd y scripts para que `agent-ventas` sobreviva
a reboots sin intervención manual. Todo vive en este directorio (`deploy/`);
nada está instalado todavía: la instalación es manual y se hace con
`systemctl --user` (nunca con sudo).

> **NO tocar `~/.config/systemd/user/cloudflared.service`** — esa unidad
> pertenece a n8n y tiene su propio `TUNNEL_TOKEN` en `~/.env.cloudflared`.
> Las unidades de este repo son independientes.

## Qué hay aquí

| Archivo | Qué hace |
|---|---|
| `agent-ventas.service` | uvicorn `app.main:app` en `127.0.0.1:8000`, `Restart=always`, `RestartSec=5`. Lee `EnvironmentFile=.env` del repo. |
| `agent-ventas-tunnel.service` | `cloudflared tunnel --no-autoupdate --url http://127.0.0.1:8000`, `Restart=always`, `RestartSec=10`. Log en `~/agent-ventas-tunnel.log` (append). |
| `agent-ventas-tunnel-watch.service` + `.timer` | Cada 1 min corre `update-tunnel-url.sh`: si la URL `*.trycloudflare.com` del log cambió, redeploya la landing con `npx wrangler deploy --var ANA_API_URL:<url>`; si no cambió, exit 0 sin hacer nada. `flock` evita solapes. |
| `agent-ventas-backup.service` + `.timer` | Diario (`Persistent=true`): `deploy/backup.sh` — `pg_dump` si la DB es alcanzable, si no `sqlite3 .backup`, si no mensaje y exit 0. Conserva los últimos 7 archivos en `~/backups/`. |
| `update-tunnel-url.sh` | Lógica del watcher (state en `deploy/.last_tunnel_url`, log en `~/agent-ventas-tunnel-watch.log`). |
| `backup.sh` | Lógica del backup (nunca falla ruidosamente). |
| `chatwoot-db.service` | Postgres user-space de Chatwoot (`~/.local/pgdata`, `:5434`). Fuera de `/tmp` → sobrevive reboots. |
| `chatwoot-valkey.service` | Valkey de Chatwoot (`:6390`), foreground con `Restart=always`. |
| `chatwoot-rails.service` | Dashboard Chatwoot (Puma `127.0.0.1:3001`), tras db+valkey. Requiere `public/vite-dev` pre-compilado. |
| `chatwoot-sidekiq.service` | Sidekiq con `-C config/sidekiq.yml` — **sin el `-C` los webhooks no salen** (cola `critical` sin consumir). |

## Instalación manual

```bash
cd /home/diego/Factory/agent-ventas
mkdir -p ~/.config/systemd/user
cp deploy/*.service deploy/*.timer ~/.config/systemd/user/
systemctl --user daemon-reload

systemctl --user enable --now agent-ventas.service
systemctl --user enable --now agent-ventas-tunnel.service
systemctl --user enable --now agent-ventas-tunnel-watch.timer
systemctl --user enable --now agent-ventas-backup.timer

# Centro de control (Chatwoot) — el orden importa: db y valkey primero.
systemctl --user enable --now chatwoot-db.service chatwoot-valkey.service
systemctl --user enable --now chatwoot-rails.service chatwoot-sidekiq.service

# Arrancan también tras reboot/login sin sesión abierta.
# Puede requerir permiso de polkit (pedirá la contraseña del usuario):
loginctl enable-linger $USER
```

Verificación:

```bash
systemctl --user status agent-ventas.service agent-ventas-tunnel.service
systemctl --user list-timers | grep agent-ventas     # los dos timers presentes
curl -s http://127.0.0.1:8000/health                 # {"status":...}
tail -f ~/agent-ventas-tunnel.log                    # URL nueva al arrancar
bash deploy/backup.sh                                # prueba manual del backup
```

Para desactivar (sin borrar nada):

```bash
systemctl --user disable --now agent-ventas-tunnel-watch.timer \
  agent-ventas-backup.timer agent-ventas-tunnel.service agent-ventas.service
```

### Advertencia operativa: la DB hoy es un crash-loop

Verificado en este host: con el `.env` actual (`127.0.0.1:5433`, cluster de
Docker caído) `init_db()` del lifespan lanza
`OperationalError: connection to server ... port 5433 ... Connection refused`
y uvicorn sale. Como la unidad tiene `Restart=always`, hasta que no haya una
DB alcanzable el servicio **reintentará cada 5 s** (log:
`journalctl --user -u agent-ventas.service`). No es un bug del unit:
arreglarlo es resolver `DATABASE_URL` (ver más abajo), no tocar el
reinicio. El proceso manual que hoy sirve en `:8000` arranca con
`DATABASE_URL=sqlite:////tmp/opencode/agent_test.db` por encima del `.env`.

### Orden esperado tras un reboot

1. `agent-ventas.service` levanta uvicorn en `:8000`.
2. `agent-ventas-tunnel.service` abre el quick tunnel e imprime la URL nueva
   en `~/agent-ventas-tunnel.log`.
3. En ≤1 min el watcher detecta la URL, hace `wrangler deploy` en
   `/home/diego/Factory/web/saleamind1` y la guarda en
   `deploy/.last_tunnel_url` (estado local, no versionado). Si `wrangler`
   falla, no guarda estado y reintenta en el siguiente tick.

### Backups

- Salida: `~/backups/agent-ventas-YYYYMMDD.sql.gz` (Postgres) o
  `~/backups/agent-ventas-YYYYMMDD.db` (SQLite); rotación a los últimos 7.
- Errores de `pg_dump` quedan en `~/backups/backup.log`.
- Restaurar Postgres:
  `gunzip < ~/backups/agent-ventas-20260926.sql.gz | psql "<DATABASE_URL>"`.
- Restaurar SQLite: copiar el `.db` sobre `data/agent_ventas.db`.
- Copiá los dumps fuera de esta máquina: el disco solo vive aquí.

## Estado de la base de datos (requisito sin sudo)

El Postgres **nativo** del host (`postgresql.service`, `127.0.0.1:5432`,
datos en `/var/lib/postgres/data`) está levantado, pero desde este usuario
**no hay ninguna forma de autenticar sin sudo**:

- socket (`psql -h /var/run/postgresql`) → `fe_sendauth: no password supplied`
  para `diego`; con `-U postgres` → `Peer authentication failed` (el peer es
  solo para el OS-user `postgres`);
- TCP (`psql -h 127.0.0.1 -U agent/postgres/diego`) →
  `password authentication failed` con las credenciales del stack
  (`agent`/`agent`) y variantes típicas;
- no existe `~/.pgpass` ni `PGPASSWORD` en dotfiles/historial;
- el daemon de Docker está caído y `sudo` requiere contraseña (prohibido),
  así que el cluster de compose (`:5433`) no puede levantarse.

Por eso **`.env` NO se tocó**: sigue apuntando a
`postgresql+psycopg://agent@127.0.0.1:5433/agent_ventas` (cluster de compose,
caído hasta que alguien levante Docker con `make up`).

Para conectar al Postgres nativo hace falta **una** de estas dos cosas
(ambas requieren una contraseña que este agente no tiene):

1. la password del rol `agent` (o crear el rol) — p.ej.
   `psql -h 127.0.0.1 -U postgres` con la password del superuser y después
   `CREATE ROLE agent LOGIN PASSWORD '<nueva>'; CREATE DATABASE agent_ventas OWNER agent;`, o
2. la password del superuser `postgres` para editar `pg_hba.conf` y habilitar
   `peer`/`trust` en loopback (trabajo de admin, con sudo).

Con una de ellas el cableado es: `alembic upgrade head` +
`DATABASE_URL=postgresql+psycopg://agent:<pass>@127.0.0.1:5432/agent_ventas`
en `.env`.

### Plan B (documentado, NO activado): SQLite durable

Si nunca se recupera la password, la alternativa sin servicios externos es
SQLite en el repo con WAL (sobrevive reboots; el directorio `data/` hay que
añadirlo a `.gitignore` al activarlo):

```bash
mkdir -p data
# URL nueva en .env:
DATABASE_URL=sqlite:///data/agent_ventas.db
# WAL es persistente en el fichero: una sola vez basta.
sqlite3 data/agent_ventas.db 'PRAGMA journal_mode=WAL;'
alembic upgrade head
```

Ojo: `app/db/database.py` crea el engine sin `connect_args` ni PRAGMAs, así
que el `journal_mode` hay que fijarlo una vez como arriba. Las migraciones de
Alembic ya declaran `with_variant(sa.JSON(), 'sqlite')` en los JSONB, pero
**no está comprobado** `alembic upgrade head` sobre SQLite ni el arranque con
ese engine — probarlo en una copia antes de migrar datos reales. El backup de
`deploy/backup.sh` ya cubre este caso (`data/agent_ventas.db`).
