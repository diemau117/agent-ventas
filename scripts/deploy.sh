#!/usr/bin/env bash
# deploy.sh — Despliegue automatizado de Agent Ventas a Render
# Uso: ./scripts/deploy.sh

set -euo pipefail

echo "🚀 Agent Ventas — Despliegue a Render"
echo "======================================"

# Verificar que render CLI está instalado
if ! command -v render &> /dev/null; then
    echo "❌ Render CLI no está instalado."
    echo "   Instalá con: npm install -g @render-cli/cli"
    echo "   O usá el dashboard: https://render.com"
    exit 1
fi

# Verificar variables de entorno requeridas
echo ""
echo "📋 Verificando variables de entorno..."

required_vars=("GROQ_API_KEY")
for var in "${required_vars[@]}"; do
    if [[ -z "${!var:-}" ]]; then
        echo "❌ Falta la variable de entorno: $var"
        exit 1
    fi
done
echo "✅ Variables de entorno OK"

# Verificar que estamos en un repo git
if ! git rev-parse --git-dir &> /dev/null; then
    echo "❌ No estás en un repo git."
    echo "   Agent Ventas se despliega desde GitHub a Render."
    exit 1
fi

# Verificar que hay commits
if ! git log --oneline -1 &> /dev/null; then
    echo "❌ No hay commits en el repo."
    exit 1
fi

echo ""
echo "📦 Estado del repo:"
git log --oneline -3
echo ""

# Obtener la URL del servicio
echo "🔍 Buscando servicio en Render..."
SERVICE_NAME="agent-ventas"

# Verificar si el servicio existe
if render services list 2>/dev/null | grep -q "$SERVICE_NAME"; then
    echo "✅ Servicio encontrado: $SERVICE_NAME"
    echo ""
    echo "📝 Para desplegar, hacé push a la rama main:"
    echo "   git push origin main"
    echo ""
    echo "   O usá el dashboard: https://render.com"
else
    echo "⚠️  Servicio no encontrado en Render."
    echo ""
    echo "📝 Para crear el servicio por primera vez:"
    echo "   1. Andá a https://render.com"
    echo "   2. Creá un nuevo Web Service"
    echo "   3. Conectá tu repo de GitHub"
    echo "   4. Usá la configuración de render.yaml"
    echo ""
    echo "   O ejecutá: render blueprint apply"
fi

echo ""
echo "✅ Listo!"
