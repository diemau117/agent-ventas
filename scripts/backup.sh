#!/usr/bin/env bash
# backup.sh — Backup de la base de datos de Agent Ventas
# Uso: ./scripts/backup.sh

set -euo pipefail

echo "💾 Agent Ventas — Backup de base de datos"
echo "=========================================="

# Configuración
BACKUP_DIR="${BACKUP_DIR:-./backups}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="${BACKUP_DIR}/agent_ventas_${TIMESTAMP}.sql"

# Crear directorio de backups
mkdir -p "$BACKUP_DIR"

# Obtener DATABASE_URL
if [[ -z "${DATABASE_URL:-}" ]]; then
    echo "❌ DATABASE_URL no está definida."
    echo "   Ejemplo: export DATABASE_URL=postgresql+psycopg://user:pass@host:5432/db"
    exit 1
fi

# Extraer credenciales de la URL
# Formato: postgresql+psycopg://user:pass@host:5432/db
DB_URL="${DATABASE_URL#postgresql+psycopg://}"
DB_USER="${DB_URL%%:*}"
DB_PASS="${DB_URL#*:}"
DB_PASS="${DB_PASS%%@*}"
DB_HOST="${DB_URL#*@}"
DB_HOST="${DB_HOST%%:*}"
DB_PORT="${DB_URL##*:}"
DB_PORT="${DB_PORT%%/*}"
DB_NAME="${DB_URL##*/}"

echo "📦 Host: $DB_HOST"
echo "📦 Base: $DB_NAME"
echo "📦 Usuario: $DB_USER"
echo ""

# Verificar que pg_dump está instalado
if ! command -v pg_dump &> /dev/null; then
    echo "❌ pg_dump no está instalado."
    echo "   Instalá con: sudo apt install postgresql-client"
    exit 1
fi

# Hacer el backup
echo "📝 Creando backup..."
PGPASSWORD="$DB_PASS" pg_dump \
    -h "$DB_HOST" \
    -p "$DB_PORT" \
    -U "$DB_USER" \
    -d "$DB_NAME" \
    -F c \
    -f "$BACKUP_FILE"

echo ""
echo "✅ Backup creado: $BACKUP_FILE"
echo "   Tamaño: $(du -h "$BACKUP_FILE" | cut -f1)"
echo ""
echo "📝 Para restaurar:"
echo "   pg_restore -h $DB_HOST -p $DB_PORT -U $DB_USER -d $DB_NAME $BACKUP_FILE"
