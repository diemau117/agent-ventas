# 🚀 Despliegue de Agent Ventas

## Opción 1: Render (Recomendado)

### Requisitos
- Cuenta en [Render](https://render.com) (gratis)
- Cuenta en [GitHub](https://github.com)
- [Render CLI](https://render.com/docs/cli) (opcional)

### Pasos

1. **Subí el código a GitHub**
   ```bash
   git init
   git add .
   git commit -m "Initial commit"
   git remote add origin https://github.com/tu-usuario/agent-ventas.git
   git push -u origin main
   ```

2. **Creá el servicio en Render**
   - Andá a https://render.com
   - Click en "New" → "Web Service"
   - Conectá tu repo de GitHub
   - Configuración:
     - **Name**: `agent-ventas`
     - **Runtime**: Python
     - **Build Command**: `pip install -r requirements.txt`
     - **Start Command**: `alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port $PORT`

3. **Configurá las variables de entorno**
   - `APP_ENV=prod`
   - `SECRET_KEY` (generá una aleatoria)
   - `DATABASE_URL` (Render la crea automáticamente si usás el blueprint)
   - `GROQ_API_KEY` (de https://console.groq.com)
   - `STRIPE_SECRET_KEY` (de https://dashboard.stripe.com)
   - `STRIPE_WEBHOOK_SECRET`
   - `STRIPE_PRICE_STARTER`, `STRIPE_PRICE_PRO`, `STRIPE_PRICE_ENTERPRISE`
   - `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_WHATSAPP_NUMBER`

4. **Creá la base de datos**
   - En Render, creá un nuevo PostgreSQL
   - Plan: Free
   - Conectá el servicio web a la base de datos

5. **Desplegá**
   - Render despliega automáticamente cuando hacés push a `main`
   - O ejecutá: `render blueprint apply`

### URL de acceso
- Web: `https://agent-ventas.onrender.com`
- API: `https://agent-ventas.onrender.com/api`
- Docs: `https://agent-ventas.onrender.com/docs`

---

## Opción 2: Docker Local

```bash
# Cloná el repo
git clone https://github.com/tu-usuario/agent-ventas.git
cd agent-ventas

# Configurá las variables
cp .env.example .env
# Editá .env con tus claves

# Levantá los servicios
docker-compose up -d

# La app está en http://localhost:8000
```

---

## Opción 3: VPS Manual

```bash
# En el servidor
sudo apt update
sudo apt install python3.11 python3-pip postgresql

# Cloná el repo
git clone https://github.com/tu-usuario/agent-ventas.git
cd agent-ventas

# Instalá dependencias
pip install -r requirements.txt

# Configurá las variables
cp .env.example .env
nano .env

# Creá la base de datos
sudo -u postgres createdb agent_ventas

# Migrá la base de datos
alembic upgrade head

# Levantá la app
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

---

## 🔧 Comandos útiles

### Backup de la base de datos
```bash
./scripts/backup.sh
```

### Ver logs en Render
```bash
render logs agent-ventas
```

### Desplegar manualmente
```bash
./scripts/deploy.sh
```

---

## 📝 Notas

- El plan free de Render se duerme después de 15 minutos de inactividad
- La base de datos free de Render tiene 1GB de almacenamiento
- Para producción, considerá el plan Starter ($7/mes)
