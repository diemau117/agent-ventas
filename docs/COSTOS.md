# Costos del despliegue

Estado actual: **$0/mes**. Todo corre en planes gratuitos y hardware local.

| Pieza | Plan actual | Coste |
|---|---|---|
| Landing (Worker `salesmind`, Static Assets) | Cloudflare Workers free | $0 |
| Puente público (quick tunnel `*.trycloudflare.com`) | cloudflared quick tunnel | $0 |
| API (`uvicorn :8000`) | esta máquina | $0 |
| Base de datos | Postgres local (host o Docker) | $0 |
| LLM (Groq, `openai/gpt-oss-20b`) | plan gratuito de Groq (límites de rate) | $0 |
| Backups diarios (`deploy/backup.sh`) | disco local `~/backups` | $0 |

## Riesgos asociados al tier gratuito

- **La máquina apagada = todo caído**: API, DB y túnel viven en el mismo
  host, sin SLA ni alertas. Si se reinicia, las unidades de `deploy/` lo
  levantan solas (linger + timers), pero una caída a mitad de la noche no
  se entera nadie.
- **Quick tunnel sin SLA**: Cloudflare advierte que estos tunnels no tienen
  SLA y la URL cambia en cada reinicio; el watcher
  (`agent-ventas-tunnel-watch.timer`) reduce la ventana a ≤1 min, pero hay
  un hueco con la landing apuntando a la URL vieja.
- **Backups solo locales**: si el disco/máquina muere, se pierden dumps y
  datos. Copiar `~/backups/` a otro destino (otro host, un bucket gratuito)
  es lo mínimo recomendable.
- **Groq free tier**: límites de rate y posibles cambios de disponibilidad;
  sin presupuesto asociado hoy (`DAILY_TOKEN_BUDGET=200000`).

## Alternativa mínima de pago (opción futura, NO activada)

Si hace falta que el servicio no dependa de esta máquina:

| Opción | Coste aprox. | Qué resuelve |
|---|---|---|
| VPS pequeño (p.ej. Hetzner CX22: 2 vCPU / 4 GB) | ~€4-5/mes | Servicio 24/7 con SLA; el mismo stack de `deploy/` corre ahí sin cambios de precio. |
| Backup extra del VPS (Hetzner Snapshot/Backups) | ~€1-1.5/mes | Backups fuera del disco principal. |
| Dominio propio + tunnel **nombrado** de cloudflared | ~€10-12/año | Hostname fijo (sin churn de URL) y TLS propio; requiere `cloudflared tunnel login`. |

Escenario combinado realista: **~€5-7/mes** (VPS + snapshot + dominio
amortizado). Ninguna de estas opciones está contratada ni configurada; este
documento solo deja constancia del coste de la decisión por si el tier
gratuito deja de servir.

## Sizing — 5 clientes × 1000 chats/día (2026-09-26)

Supuestos (marcar cuál aplica; los tokens son **medidos** en el smoke de
2026-09-26, no estimados: `tokens_in` 2.727-5.820/turno, `tokens_out`
86-1.652/turno, media ≈ 4.000 in / 800 out):

| Escenario | Turnos LLM/día (total) | Tokens in/día | Tokens out/día |
|---|---|---|---|
| A: "1000 chat" = 1000 **conversaciones**/cliente × ~6 turnos → 30.000 | 30.000 | ~120 M | ~24 M |
| B: "1000 chat" = 1000 **mensajes**/cliente → 5.000 | 5.000 | ~20 M | ~4 M |

Promedio de carga: 0,35 turnos/s (A) o 0,06 turnos/s (B); pico ×10 sigue
por debajo de 4 req/s. **El cuello de botella no es la CPU.**

### Hardware (API + Postgres + Chatwoot en una caja)

| Pieza | RSS/uso esperado | Nota |
|---|---|---|
| API FastAPI/uvicorn | 150-300 MB | sync endpoints en threadpool; 4-8 workers bastan |
| Postgres 18 | 300-800 MB | 60-120k filas `messages`/día (≈60-120 MB/día); pooling default |
| Chatwoot (rails + sidekiq + valkey) | 1,5-2,5 GB | la pieza pesada; solo si corres el Chatwoot en la misma caja |
| **Total** | **~3 GB (sin Chatwoot) / ~5 GB (con Chatwoot)** | red 10-20 GB/día según adjuntos |

- **Mínimo viable**: 2 vCPU / 4 GB (Hetzner CX22 ~€4-5/mes) con Chatwoot
  en la misma caja y retención de mensajes (borrar `messages` > 90 días).
- **Cómodo**: 2 vCPU / 8 GB (~€7-8/mes) si los 5 clientes llegan al
  escenario A y añades monitorización + backups en disco.
- La máquina actual (8 hilos / 7 GB) ya aguanta el escenario A de API+DB;
  con Chatwoot nativo corriendo queda justa de RAM (3 GB libres hoy).

### Nube gratis (Cloudflare) — límites exactos (fuentes primarias, 2026-09-26)

| Dato | Valor Free | Fuente |
|---|---|---|
| Requests Workers/día | **100.000** (reset 00:00 UTC; superar → Error 1027) | developers.cloudflare.com/workers/platform/limits/ |
| CPU por request | **10 ms** (la espera de red no cuenta; Error 1102 si se excede) | idem |
| Memoria / subrequests | 128 MB · 50 subrequests/invocación | idem |
| Python Workers (FastAPI/Langchain documentados) | sin gating explícito de plan; aplica límite Free | developers.cloudflare.com/workers/languages/python/ |
| Hyperdrive (Postgres externo) | **100.000 consultas/día** | developers.cloudflare.com/workers/platform/pricing/ |
| Containers | **Free: N/A** — solo Workers Paid ($5 USD/mes) | idem |

Veredicto:
- **Sí en free**: el borde — landing (Static Assets, ya desplegada),
  DNS/WAF, cloudflared tunnel. La API actual (FastAPI + SQLAlchemy +
  psycopg) **no corre en Workers Python tal cual**; los límites que la
  matarían serían los 10 ms de CPU/request (Error 1102) y 100k req/día,
  además del rediseño de acceso a Postgres vía Hyperdrive. Reescribir
  para Workers es otro proyecto, no "subirlo".
- **5 clientes × 1000 chats/día**: ~5-30k requests/día de API (según
  escenario) — cupo de 100k/día de Workers lo admitiría **solo si** la
  API corriera en Workers, que no es el caso. Medida propia pendiente si
  algún día se plantea.
- **Conclusión**: brain (API+DB) en VPS (~€5/mes) o en la caja local
  ($0, sin SLA). Cloudflare gratis cubre el borde.

### Alternativas gratis always-on verificadas (2026-09-26)

| Opción | Hecho duro |
|---|---|
| **Oracle Cloud Always Free** | Vigente: 2 OCPU + 12 GB ARM (`VM.Standard.A1.Flex`), 10 TB egress/mes; **reclama la instancia si p95 < 20% durante 7 días**; sin Postgres gestionado free (la BD iría en la VM) |
| **Google Cloud Free Tier** | Solo 1× `e2-micro` (us-*) + 30 GB; Cloud SQL sin free tier; el trial de $300/90 días borra recursos al cerrar |
| **Fly.io** | **Sin tier always-on**: solo trial 2 h / 7 días; free allowances descontinuados 2024-10-07; tarjeta obligatoria |

### LLM (Groq) — el coste real (ficha del modelo, leída 2026-09-26)

Precios `openai/gpt-oss-20b`: **input $0.075/M**, cached input
$0.037/M, **output $0.30/M**. La tabla pública de rate limits
(30 RPM · 1.000 RPD · 8.000 TPM · 200.000 TPD) es del **plan Developer**;
los límites exactos del free tier no se publican (solo en la consola
autenticada).

Coste por escenario (solo tokens, con los medidos arriba):

| Escenario | Tokens/día | Coste/día | Coste/mes |
|---|---|---|---|
| A (120 M in + 24 M out) | 144 M | ~$16 | **~$480** |
| B (20 M in + 4 M out) | 24 M | ~$2,7 | **~$81** |

Escenario A a precio completo **no es sostenible gratis**: el free tier
de Groq está lejos de 120 M in/día (Developer = 200k TPD; el free no se
publica). Escenario B es un order of magnitude más cercano pero aún por
encima de 200k TPD → tocará Developer plan o reducir tokens/turno (la
mayor partida es el system prompt + tools inyectados cada turno:
optimización de cache/prefix es el primer palanca, no el modelo).

Presupuesto por negocio (`Business.daily_token_budget`, default 200k)
ya separa el gasto por tenant: revisar el default por cliente al alta.
