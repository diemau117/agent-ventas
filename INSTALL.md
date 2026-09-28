# Guía de Instalación — Agent Ventas

Esta guía te permite instalar y configurar Agent Ventas en minutos, ya sea en local, Docker o Render.

## Requisitos Previos

- Python 3.12+ (recomendado) o 3.14
- PostgreSQL 16+ (o SQLite para desarrollo)
- Cuenta en Groq (para el LLM)
- Cuenta en Stripe (opcional, para pagos)
- Cuenta en Twilio (opcional, para WhatsApp)

## Instalación Rápida (Desarrollo Local)

### 1. Clonar el repositorio

```bash
git clone https://github.com/diemau117/agent-ventas.git
cd agent-ventas
```

### 2. Crear entorno virtual

```bash
python3 -m venv .venv
source .venv/bin/activate  # Linux/Mac
# .venv\Scripts\activate   # Windows
```

### 3. Instalar dependencias

```bash
pip install -r requirements.txt
```

### 4. Configurar variables de entorno

```bash
cp .env.example .env
```

Edita `.env` con tus credenciales:

```env
# App
APP_ENV=dev
SECRET_KEY=un-secreto-largo-y-aleatorio

# Database (PostgreSQL recomendado para producción)
DATABASE_URL=postgresql+psycopg2://usuario:password@localhost:5432/agent_ventas

# Groq (LLM) — OBLIGATORIO para producción
GROQ_API_KEY=gsk_tu_clave_aqui

# Stripe (opcional)
STRIPE_SECRET_KEY=sk_test_tu_clave_aqui
STRIPE_WEBHOOK_SECRET=whsec_tu_clave_aqui
STRIPE_PRICE_STARTER=price_xxx
STRIPE_PRICE_PRO=price_xxx
STRIPE_PRICE_ENTERPRISE=price_xxx

# Twilio (opcional)
TWILIO_ACCOUNT_SID=AC_tu_clave_aqui
TWILIO_AUTH_TOKEN=tu_token_aqui
TWILIO_WHATSAPP_NUMBER=whatsapp:+14155238886
```

### 5. Inicializar la base de datos

```bash
# Crear tablas
python -c "from app.db.database import init_db; init_db()"

# O usar Alembic (recomendado)
alembic upgrade head
```

### 6. Ejecutar la aplicación

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

La aplicación estará disponible en `http://localhost:8000`

- Landing: `http://localhost:8000/`
- API Docs: `http://localhost:8000/docs`
- Panel: `http://localhost:8000/panel`

## Instalación con Docker

### 1. Construir la imagen

```bash
docker build -t agent-ventas .
```

### 2. Ejecutar con docker-compose

```bash
docker-compose up -d
```

### 3. Verificar que esté funcionando

```bash
curl http://localhost:8000/health
```

## Instalación en Render

### 1. Crear una cuenta en Render

Ve a [render.com](https://render.com) y crea una cuenta gratuita.

### 2. Crear un nuevo Web Service

1. Click en "New" → "Web Service"
2. Conecta tu repositorio de GitHub
3. Configura:
   - **Name**: `agent-ventas`
   - **Runtime**: Python
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`

### 3. Crear una base de datos PostgreSQL

1. Click en "New" → "PostgreSQL"
2. Configura:
   - **Name**: `agent-ventas-db`
   - **Plan**: Free
3. Copia la **Internal Database URL**

### 4. Configurar variables de entorno

En el Web Service, ve a "Environment" y agrega:

| Key | Value |
|-----|-------|
| `APP_ENV` | `prod` |
| `DATABASE_URL` | Internal Database URL |
| `GROQ_API_KEY` | Tu clave de Groq |
| `SECRET_KEY` | Un secreto largo y aleatorio |

### 5. Desplegar

Render automáticamente desplegará cuando hagas push a la rama principal.

## Configuración del Agente

### 1. Crear un negocio (onboarding)

```bash
curl -X POST http://localhost:8000/api/onboarding \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Mi Negocio",
    "email": "mi@email.com",
    "phone": "+52 123 456 7890",
    "description": "Descripción de mi negocio",
    "agent_name": "Sofi"
  }'
```

Esto devolverá:
- `id`: ID del negocio
- `crm_token`: Token para acceder al CRM
- `public_key`: Clave pública para el widget

### 2. Configurar el widget

En tu sitio web, agrega:

```html
<script>
  window.AGENT_VENTAS_KEY = "tu_public_key_aqui";
</script>
<script src="http://localhost:8000/static/widget.js"></script>
```

### 3. Configurar el CRM

Para acceder al CRM de lectura:

```bash
curl "http://localhost:8000/api/leads?token=tu_crm_token"
```

## Configuración de WhatsApp (Twilio)

### 1. Crear una cuenta en Twilio

Ve a [twilio.com](https://twilio.com) y crea una cuenta.

### 2. Configurar el webhook

En la consola de Twilio, configura el webhook para que apunte a:

```
https://tu-dominio.com/api/whatsapp/webhook
```

### 3. Configurar variables de entorno

```env
TWILIO_ACCOUNT_SID=AC_tu_clave_aqui
TWILIO_AUTH_TOKEN=tu_token_aqui
TWILIO_WHATSAPP_NUMBER=whatsapp:+14155238886
```

## Configuración de Pagos (Stripe)

### 1. Crear una cuenta en Stripe

Ve a [stripe.com](https://stripe.com) y crea una cuenta.

### 2. Crear productos y precios

En el dashboard de Stripe, crea productos y copia sus IDs.

### 3. Configurar variables de entorno

```env
STRIPE_SECRET_KEY=sk_test_tu_clave_aqui
STRIPE_WEBHOOK_SECRET=whsec_tu_clave_aqui
STRIPE_PRICE_STARTER=price_xxx
STRIPE_PRICE_PRO=price_xxx
STRIPE_PRICE_ENTERPRISE=price_xxx
```

### 4. Configurar el webhook

En el dashboard de Stripe, configura el webhook para que apunte a:

```
https://tu-dominio.com/api/stripe/webhook
```

## Pruebas

### Ejecutar tests

```bash
pytest -v
```

### Verificar endpoints

```bash
# Health check
curl http://localhost:8000/health

# Catálogo
curl "http://localhost:8000/api/catalog?public_key=tu_public_key"

# CRM
curl "http://localhost:8000/api/leads?token=tu_crm_token"
```

## Solución de Problemas

### Error: "connection refused" a la base de datos

Verifica que:
1. PostgreSQL esté corriendo
2. La URL de conexión sea correcta
3. Las credenciales sean correctas

### Error: "invalid_public_key"

Verifica que:
1. La public_key exista en la base de datos
2. Estés usando la clave correcta

### Error: "daily_token_budget_exceeded"

El negocio ha alcanzado su límite diario de tokens. Puedes:
1. Aumentar el límite en la configuración del negocio
2. Esperar a que se reinicie el contador (día siguiente UTC)

### Error: "rate_limited"

Has hecho demasiadas peticiones en poco tiempo. Espera un minuto y vuelve a intentar.

## Soporte

Para soporte, abre un issue en GitHub o contacta al equipo de desarrollo.
