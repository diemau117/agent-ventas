# Runbook — Alta de cliente en Chatwoot (multi-tenant)

Objetivo: un cliente nuevo queda operativo con su propio inbox de Chatwoot
(WhatsApp, Telegram, web…) apuntando a su `business_id`, sin tocar código.
Instancia única self-hosted: `~/docker-compose.yml` (servicios `chatwoot*`).

## Pasos

1. **Crear la Account del cliente** (no una instalación nueva).
   En la instancia existente: consola de plataforma
   (`http://localhost:3000/platform`) o API de plataforma:
   `POST /platform/api/v1/accounts` con el token de plataforma
   (`PLATFORM_API_KEY`). `ENABLE_ACCOUNT_SIGNUP=false` en el compose:
   las cuentas se crean solo desde la plataforma, no por signup público.

2. **Crear su inbox** dentro de esa Account (Settings → Inboxes → Add):
   - WhatsApp: requiere Meta Cloud API (phone number id + token).
   - Telegram: bot token de @BotFather.
   - Web/site widget: genera el script que el cliente incrusta.
   Anotar el `inbox_id` (aparece en la URL del inbox).

3. **Invitar por email al usuario del cliente** (Settings → Team Members →
   Add team member): la persona que va a atender desde el lado del cliente.
   Siempre desde la Account del cliente, jamás desde otra (aislamiento).

4. **Conectar el inbox al `business_id` correspondiente** en la BD de Agent
   Ventas (una fila de `businesses`):

   ```sql
   UPDATE businesses SET
     chatwoot_url      = 'http://localhost:3000',   -- instancia
     chatwoot_token    = '<api_access_token>',      -- Settings → Profile → Access Token
     chatwoot_inbox_id = <inbox_id>                 -- paso 2
   WHERE id = <business_id>;
   ```

   `push_handoff_for_business` usa esas 3 columnas (con fallback a las env
   `CHATWOOT_URL`/`CHATWOOT_TOKEN` solo si la URL/token están vacíos; el
   inbox **siempre** es por negocio). Vacío = integración inactiva para ese
   negocio, el handoff queda solo en la conversación interna.

5. **Webhook de vuelta** (respuestas del humano → widget): en
   Settings → Webhooks de la Account del cliente, suscribir
   `https://<tu-dominio>/api/webhook/chatwoot?token=<CHATWOOT_TOKEN>` con
   el evento `message_created`. Sin el `token` compartido el backend
   responde 401 (best-effort: si `CHATWOOT_TOKEN` está vacío acepta, no
   usar en producción).

6. **Verificar** (una vez por alta): desde la landing con la `public_key`
   del negocio, pedir "quiero hablar con un humano" → debe llegar una
   nota interna `Handoff de conversación #N` **al inbox de ese cliente**
   (no a otro), con nombre/necesidad/temperatura/próxima cita, y la
   respuesta del humano debe volver al widget
   (`GET /api/leads?token=<crm_token_del_negocio>` muestra el lead
   avanzando).

## Estado de la verificación (2026-09-26)

- **E2E REAL VERIFICADO (5/5 fases, `RESULTADO: OK`)** contra la
  instancia nativa en `:3001`:
  1. Handoff del tenant A → `handoff=True` en `POST /api/chat`.
  2. Conversación creada **en el inbox 1 real** con correlación
     `agent_ventas_business_id=1 / agent_ventas_conversation_id=N`.
  3. Nota interna `Handoff de conversación #N` con
     `content_attributes.source=agent_ventas` (anti-bucle).
  4. **Webhook real**: el humano responde desde el dashboard →
     Chatwoot `message_created` → `WebhookJob` (Sidekiq) →
     `POST /api/webhook/chatwoot` → mensaje persistido +
     `Conversation.state=human` en la BD interna.
  5. Tenant B sin credenciales → push `skipped`, sin errores.
- **Suscripción**: `Webhook` account 1 →
  `http://127.0.0.1:8000/api/webhook/chatwoot`
  (subs: `message_created`, `conversation_created`).
- **Instancia nativa (sin Docker)**:
  `deploy/install-chatwoot-native.sh` → ruby 3.4.4 en `~/.local`
  (Gemfile pinea 3.4.4), valkey :6390, Postgres user-space :5434,
  Chatwoot **:3001**, DB vía `db:schema:load` (estilo CI — `db:migrate`
  falla por constants eliminados de gems actuales). Parches GCC 16:
  `HAVE_STDBOOL_H` en `ruby/config.h` + `-std=gnu17` en `rbconfig`.
  Servidores (**vía systemd, preferida — unidades en `deploy/chatwoot-*.service`**,
  instaladas 2026-09-26): `chatwoot-db` (`:5434`, datos en `~/.local/pgdata`,
  fuera de `/tmp`), `chatwoot-valkey` (`:6390`), `chatwoot-rails` (Puma `:3001`)
  y `chatwoot-sidekiq`. Arranque manual equivalente: `bin/rails server -p 3001`
  + `bundle exec sidekiq -C config/sidekiq.yml` (sidekiq **obligatorio**: sin
  él el webhook no se entrega; y **sin `-C`** solo procesa la cola `default` —
  `EventDispatcherJob` y `WebhookJob` quedan atascados en `critical`/`high` y
  el webhook jamás sale, verificado 2026-09-26). El frontend necesita build de
  Vite una vez (`NODE_OPTIONS=--max-old-space-size=3584 bin/vite build` — con
  el heap default de Node se OOMea en esta caja); sin `public/vite-dev` la
  primera petición HTML dispara el build inline y cuelga minutos.

## No confundir

- **`public_key`** (widget): identifica el tenant en `/api/chat`. Publicable.
- **`Business.crm_token`** (H20): token de lectura del CRM, uno por
  cliente, solo ve sus leads/citas. Generar con
  `secrets.token_urlsafe(32)` y guardarlo en la fila del negocio.
- **`CRM_TOKEN`** (env): token de **operador** (ve todos los tenants).
  No se distribuye a clientes.
- **`chatwoot_token`**: credencial de API de Chatwoot para publicar
  handoffs; vive en BD, por negocio.
