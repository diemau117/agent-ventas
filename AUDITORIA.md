# Auditoría del Sistema — Agent Ventas

**Fecha:** 2026-09-28
**Auditor:** AI Assistant
**Estado del sistema:** ⚠️ Parcialmente funcional (Render no conecta a PostgreSQL)

---

## 1. Resumen Ejecutivo

| Área | Estado | Detalle |
|------|--------|---------|
| **Backend (FastAPI)** | ✅ Funcional | 163 tests pasan, código limpio |
| **Frontend (Landing/Panel)** | ✅ Funcional | HTML/CSS/JS sin frameworks |
| **Base de Datos** | ❌ No conecta | Error SSL con Render PostgreSQL |
| **Agente AI (LangGraph)** | ✅ Funcional | Grafo classify → act → verify |
| **WhatsApp (Twilio)** | ❌ No implementado | Solo simulación |
| **Stripe (Pagos)** | ❌ No implementado | Solo simulación |
| **Seguridad** | ⚠️ Mejorable | Tokens en query params, sin auth en onboarding |
| **Tests** | ✅ 163 tests | Cobertura buena |
| **Documentación** | ⚠️ Incompleta | README básico |

---

## 2. Arquitectura del Sistema

```
┌─────────────────────────────────────────────────────────────┐
│                        CLIENTE                               │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐          │
│  │   Landing   │  │    Panel    │  │   Widget    │          │
│  │  index.html │  │  panel.html │  │  widget.js  │          │
│  └─────────────┘  └─────────────┘  └─────────────┘          │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                      SERVIDOR (FastAPI)                      │
│  ┌─────────────────────────────────────────────────────┐    │
│  │                    MIDDLEWARE                        │    │
│  │  • Rate Limit (por IP, 30/min)                      │    │
│  │  • CORS (allowed_origins=*)                          │    │
│  └─────────────────────────────────────────────────────┘    │
│                              │                               │
│  ┌─────────────────────────────────────────────────────┐    │
│  │                     ROUTERS                           │    │
│  │  /api/chat          → Agente AI (LangGraph)          │    │
│  │  /api/leads         → Captación de leads             │    │
│  │  /api/onboarding    → Crear negocio                  │    │
│  │  /api/panel/*       → Dashboard del cliente          │    │
│  │  /api/control-center/* → Panel del asesor            │    │
│  │  /api/leads (GET)   → CRM de lectura                 │    │
│  │  /api/appointments  → CRM de lectura                 │    │
│  │  /api/whatsapp/*    → Integración Twilio (simulado)  │    │
│  │  /api/stripe/*      → Integración Stripe (simulado)  │    │
│  └─────────────────────────────────────────────────────┘    │
│                              │                               │
│  ┌─────────────────────────────────────────────────────┐    │
│  │                   AGENTE AI                           │    │
│  │  classify → act → verify                             │    │
│  │  • Jeff (decisión determinista)                      │    │
│  │  • RAG (Knowledge base)                              │    │
│  │  • Tools (search_knowledge, create_appointment, etc) │    │
│  │  • Verifier (verificador determinista)               │    │
│  └─────────────────────────────────────────────────────┘    │
│                              │                               │
│  ┌─────────────────────────────────────────────────────┐    │
│  │                  BASE DE DATOS                        │    │
│  │  PostgreSQL (Render)                                 │    │
│  │  • Business, Lead, Appointment, Conversation, Message │    │
│  │  • Knowledge, RateLimitBucket, ExternalSearch        │    │
│  └─────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. Seguridad

### 3.1. Autenticación y Autorización

| Endpoint | Auth | Estado | Detalle |
|----------|------|--------|---------|
| `POST /api/chat` | `public_key` | ✅ OK | La clave pública resuelve el tenant |
| `POST /api/leads` | `public_key` | ✅ OK | La clave pública resuelve el tenant |
| `POST /api/onboarding` | ❌ Ninguna | 🔴 **P1** | Cualquiera puede crear negocios |
| `POST /api/init-db` | ❌ Ninguna | 🔴 **P1** | Cualquiera puede inicializar la BD |
| `GET /api/leads` | `crm_token` | ✅ OK | Token de operador o tenant |
| `GET /api/appointments` | `crm_token` | ✅ OK | Token de operador o tenant |
| `GET /api/panel/*` | `crm_token` (query) | 🟡 **P2** | Token en query param (logs) |
| `GET /api/control-center/*` | `crm_token` (query) | 🟡 **P2** | Token en query param (logs) |
| `POST /api/whatsapp/send` | ❌ Ninguna | 🔴 **P1** | Cualquiera puede enviar WhatsApp |
| `POST /api/whatsapp/webhook` | ❌ Ninguna | 🟡 **P2** | Webhook sin verificar firma |
| `POST /api/stripe/checkout` | ❌ Ninguna | 🔴 **P1** | Cualquiera puede crear checkout |
| `POST /api/stripe/webhook` | ❌ Ninguna | 🔴 **P1** | Webhook sin verificar firma |

### 3.2. Problemas de Seguridad Críticos

#### 🔴 P1: Onboarding sin autenticación
**Archivo:** `app/api/routes/onboarding.py`
**Problema:** Cualquiera puede crear negocios sin autenticación.
**Impacto:** Un atacante puede crear miles de negocios, llenar la BD, y causar denegación de servicio.
**Solución:** Agregar autenticación (API key del operador) al endpoint de onboarding.

#### 🔴 P1: Init-db sin autenticación
**Archivo:** `app/api/routes/onboarding.py`
**Problema:** Cualquiera puede inicializar la base de datos.
**Impacto:** Un atacante puede inicializar la BD y causar problemas.
**Solución:** Agregar autenticación o eliminar el endpoint (usar migraciones de Alembic).

#### 🔴 P1: WhatsApp send sin autenticación
**Archivo:** `app/api/routes/whatsapp.py`
**Problema:** Cualquiera puede enviar mensajes de WhatsApp.
**Impacto:** Un atacante puede enviar spam a través de la cuenta de Twilio.
**Solución:** Agregar autenticación (API key del operador o token de tenant).

#### 🔴 P1: Stripe checkout sin autenticación
**Archivo:** `app/api/routes/stripe.py`
**Problema:** Cualquiera puede crear sesiones de checkout.
**Impacto:** Un atacante puede crear sesiones de checkout falsas.
**Solución:** Agregar autenticación (token de tenant).

#### 🔴 P1: Stripe webhook sin verificar firma
**Archivo:** `app/api/routes/stripe.py`
**Problema:** El webhook no verifica la firma de Stripe.
**Impacto:** Un atacante puede enviar webhooks falsos y activar suscripciones sin pagar.
**Solución:** Verificar la firma del webhook con `stripe.Webhook.construct_event`.

### 3.3. Problemas de Seguridad Menores

#### 🟡 P2: Token en query param
**Archivo:** `app/api/routes/panel.py`, `app/api/routes/control_center.py`
**Problema:** El token CRM se pasa como query param (`?token=...`).
**Impacto:** Los tokens en URLs se guardan en logs, historial del navegador, y se pueden filtrar.
**Solución:** Pasar el token en un header (`Authorization: Bearer <token>`).

#### 🟡 P2: CORS demasiado permisivo
**Archivo:** `app/config.py`
**Problema:** `allowed_origins: str = "*"` permite cualquier origen.
**Impacto:** Cualquier sitio web puede hacer requests a la API.
**Solución:** Configurar `allowed_origins` con los orígenes permitidos.

#### 🟡 P2: Sin headers de seguridad
**Archivo:** `app/main.py`
**Problema:** No hay headers de seguridad (HSTS, X-Frame-Options, etc.).
**Impacto:** El sistema es vulnerable a ataques de clickjacking, MIME sniffing, etc.
**Solución:** Agregar headers de seguridad con middleware.

#### 🟡 P2: Sin HTTPS forzado
**Archivo:** `app/main.py`
**Problema:** No hay configuración para forzar HTTPS.
**Impacto:** Los requests pueden hacerse por HTTP (sin cifrar).
**Solución:** Forzar HTTPS con middleware o configuración de uvicorn.

---

## 4. Funcionalidad

### 4.1. Agente AI (LangGraph)

| Componente | Estado | Detalle |
|------------|--------|---------|
| **classify** | ✅ OK | Clasifica intención del mensaje |
| **act** | ✅ OK | Ejecuta tools y genera respuesta |
| **verify** | ✅ OK | Verifica claims comerciales |
| **Jeff** | ✅ OK | Capa de decisión determinista |
| **RAG** | ✅ OK | Búsqueda en base de conocimiento |
| **Tools** | ✅ OK | search_knowledge, create_appointment, etc. |
| **Verifier** | ✅ OK | Verificador determinista |

### 4.2. Integraciones

| Integración | Estado | Detalle |
|-------------|--------|---------|
| **WhatsApp (Twilio)** | ❌ No implementado | Solo simulación (print) |
| **Stripe (Pagos)** | ❌ No implementado | Solo simulación (URL falsa) |
| **Chatwoot** | ⚠️ Parcial | Código existe pero no se usa |
| **HubSpot** | ❌ No implementado | No hay código |

### 4.3. Base de Datos

| Tabla | Estado | Detalle |
|-------|--------|---------|
| **Business** | ✅ OK | Multi-tenant, public_key, crm_token |
| **Lead** | ✅ OK | Con temperatura, estado, etc. |
| **Appointment** | ✅ OK | Con lead_id, conversation_id |
| **Conversation** | ✅ OK | Con business_id, state |
| **Message** | ✅ OK | Con role, content, tokens |
| **Knowledge** | ✅ OK | Base de conocimiento para RAG |
| **RateLimitBucket** | ✅ OK | Rate limit por IP |
| **ExternalSearch** | ✅ OK | Auditoría de búsquedas |

---

## 5. Tests

| Test | Estado | Detalle |
|------|--------|---------|
| **test_api.py** | ✅ OK | Tests de API |
| **test_auth_obs.py** | ✅ OK | Tests de auth y observabilidad |
| **test_booking_crm.py** | ✅ OK | Tests de booking y CRM |
| **test_catalog_leads.py** | ✅ OK | Tests de catálogo y leads |
| **test_chatwoot.py** | ✅ OK | Tests de Chatwoot |
| **test_close_directive.py** | ✅ OK | Tests de directivas de cierre |
| **test_crm.py** | ✅ OK | Tests de CRM |
| **test_crm_tenant.py** | ✅ OK | Tests de multi-tenant |
| **test_graph_no_tools_fallback.py** | ✅ OK | Tests de fallback sin tools |
| **test_jeff.py** | ✅ OK | Tests de Jeff |
| **test_llm_groq.py** | ✅ OK | Tests de Groq LLM |
| **test_personas.py** | ✅ OK | Tests de personas |
| **test_policies_verifier.py** | ✅ OK | Tests de políticas y verificador |
| **test_rag.py** | ✅ OK | Tests de RAG |
| **test_salesmind.py** | ✅ OK | Tests de SalesMind |
| **test_seed_demo.py** | ✅ OK | Tests de seed demo |
| **test_websearch.py** | ✅ OK | Tests de búsqueda web |

**Total:** 163 tests ✅

---

## 6. Configuración

### 6.1. Variables de Entorno

| Variable | Requerida | Default | Detalle |
|----------|-----------|---------|---------|
| `APP_ENV` / `ENVIRONMENT` | ❌ | `dev` | Entorno (dev/prod) |
| `GROQ_API_KEY` | ✅ (prod) | `""` | API key de Groq |
| `DATABASE_URL` | ✅ (prod) | `postgresql://agent@127.0.0.1:5433/agent_ventas` | URL de PostgreSQL |
| `ALLOW_FAKE_LLM` | ❌ | `true` | Permite FakeProvider |
| `RATE_LIMIT_PER_MIN` | ❌ | `30` | Rate limit por IP |
| `DAILY_TOKEN_BUDGET` | ❌ | `200000` | Presupuesto diario de tokens |
| `TRUST_PROXY_HEADERS` | ❌ | `false` | Confiar en headers de proxy |
| `EXTERNAL_SEARCH_ENABLED` | ❌ | `true` | Búsqueda externa |
| `CHATWOOT_URL` | ❌ | `""` | URL de Chatwoot |
| `CHATWOOT_TOKEN` | ❌ | `""` | Token de Chatwoot |
| `ALLOWED_ORIGINS` | ❌ | `*` | Orígenes permitidos (CORS) |
| `LANDING_PUBLIC_KEY` | ❌ | `""` | Clave pública del tenant |
| `CRM_TOKEN` | ❌ | `""` | Token de operador CRM |
| `STRIPE_SECRET_KEY` | ❌ | `""` | API key de Stripe |
| `STRIPE_WEBHOOK_SECRET` | ❌ | `""` | Secret de webhook de Stripe |
| `TWILIO_ACCOUNT_SID` | ❌ | `""` | Account SID de Twilio |
| `TWILIO_AUTH_TOKEN` | ❌ | `""` | Auth token de Twilio |
| `TWILIO_WHATSAPP_NUMBER` | ❌ | `""` | Número de WhatsApp |

### 6.2. Docker

| Archivo | Estado | Detalle |
|---------|--------|---------|
| **Dockerfile** | ✅ OK | Python 3.12-slim, ca-certificados |
| **docker-compose.yml** | ✅ OK | App + PostgreSQL |
| **render.yaml** | ✅ OK | Blueprint de Render |

---

## 7. Despliegue

### 7.1. Render

| Componente | Estado | Detalle |
|------------|--------|---------|
| **Web Service** | ✅ Live | https://agent-ventas.onrender.com |
| **PostgreSQL** | ❌ No conecta | Error SSL |
| **Landing** | ✅ OK | https://agent-ventas.onrender.com |
| **Panel** | ✅ OK | https://agent-ventas.onrender.com/panel |
| **Onboarding** | ✅ OK | https://agent-ventas.onrender.com/onboarding-page |
| **API** | ⚠️ Parcial | /health OK, /api/init-db falla |

### 7.2. Endpoints

| Endpoint | Método | Estado | Detalle |
|----------|--------|--------|---------|
| `/health` | GET | ✅ 200 | Health check |
| `/` | GET | ✅ 200 | Landing |
| `/sales` | GET | ✅ 200 | Sales page |
| `/onboarding-page` | GET | ✅ 200 | Onboarding page |
| `/panel` | GET | ✅ 200 | Panel page |
| `/api/chat` | POST | ⚠️ 500 | Error SSL (BD) |
| `/api/leads` | POST | ⚠️ 500 | Error SSL (BD) |
| `/api/onboarding` | POST | ⚠️ 500 | Error SSL (BD) |
| `/api/init-db` | POST | ⚠️ 500 | Error SSL (BD) |
| `/api/panel/*` | GET | ⚠️ 500 | Error SSL (BD) |
| `/api/control-center/*` | GET | ⚠️ 500 | Error SSL (BD) |
| `/api/whatsapp/*` | POST | ⚠️ 500 | No implementado |
| `/api/stripe/*` | POST | ⚠️ 500 | No implementado |

---

## 8. Problemas Conocidos

### 8.1. Error SSL con Render PostgreSQL

**Error:** `SSL connection has been closed unexpectedly`

**Causa:** El contenedor de Docker (`python:3.12-slim`) no tiene los certificados CA del sistema. psycopg2 no puede verificar el certificado de Render PostgreSQL.

**Solución:** Instalar `ca-certificates` en el Dockerfile (ya hecho, pero el error persiste).

**Estado:** 🔴 No resuelto

### 8.2. WhatsApp no implementado

**Estado:** El endpoint `/api/whatsapp/send` solo simula el envío (print).

**Solución:** Implementar la integración real con Twilio.

### 8.3. Stripe no implementado

**Estado:** El endpoint `/api/stripe/checkout` solo simula la creación de la sesión de checkout.

**Solución:** Implementar la integración real con Stripe.

### 8.4. Webhook de Stripe no verifica firma

**Estado:** El endpoint `/api/stripe/webhook` no verifica la firma de Stripe.

**Solución:** Verificar la firma del webhook con `stripe.Webhook.construct_event`.

---

## 9. Recomendaciones

### 9.1. Prioridad Alta (P1)

1. **Resolver error SSL con Render PostgreSQL** — Sin esto, el sistema no funciona.
2. **Implementar autenticación en onboarding** — Cualquiera puede crear negocios.
3. **Implementar autenticación en init-db** — Cualquiera puede inicializar la BD.
4. **Implementar autenticación en WhatsApp send** — Cualquiera puede enviar WhatsApp.
5. **Implementar autenticación en Stripe checkout** — Cualquiera puede crear checkout.
6. **Verificar firma de webhook de Stripe** — Webhook sin verificar firma.

### 9.2. Prioridad Media (P2)

1. **Mover token de query param a header** — Tokens en URLs se guardan en logs.
2. **Configurar CORS correctamente** — `allowed_origins=*` es demasiado permisivo.
3. **Agregar headers de seguridad** — HSTS, X-Frame-Options, etc.
4. **Forzar HTTPS** — Los requests pueden hacerse por HTTP.

### 9.3. Prioridad Baja (P3)

1. **Implementar integración real con Twilio** — WhatsApp no está implementado.
2. **Implementar integración real con Stripe** — Pagos no están implementados.
3. **Agregar tests de seguridad** — No hay tests para verificar la seguridad.
4. **Mejorar documentación** — README básico, falta documentación de API.

---

## 10. Conclusión

El sistema tiene una base sólida: el backend está bien diseñado, el agente AI funciona correctamente, y los tests pasan. Sin embargo, hay problemas críticos que deben resolverse antes de que el sistema pueda ser usado en producción:

1. **Error SSL con Render PostgreSQL** — Sin esto, el sistema no funciona.
2. **Falta de autenticación** — Varios endpoints críticos no tienen autenticación.
3. **Integraciones no implementadas** — WhatsApp y Stripe no están implementados.

Una vez que se resuelvan estos problemas, el sistema estará listo para ser usado en producción.

---

**Fin de la auditoría.**
