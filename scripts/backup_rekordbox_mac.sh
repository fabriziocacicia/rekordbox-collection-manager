#!/usr/bin/env bash
set -euo pipefail

# Verifica che Rekordbox non sia in esecuzione
if pgrep -il rekordbox > /dev/null 2>&1; then
    echo "⚠️  Rekordbox è ancora in esecuzione! Chiudilo prima di procedere (Cmd + Q)."
    exit 1
fi

TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_PARENT_DIR="${HOME}/Music/rekordbox/Backups"
BACKUP_DIR="${BACKUP_PARENT_DIR}/rekordbox_mac_backup_${TIMESTAMP}"

DIR_PIONEER="${HOME}/Library/Pioneer/rekordbox"
DIR_APPSUPPORT="${HOME}/Library/Application Support/Pioneer/rekordbox"

echo "📦 Creazione cartella backup in ${BACKUP_PARENT_DIR}..."
mkdir -p "${BACKUP_DIR}/Pioneer_Library"

# 1. Backup del Database principale (master.db, backup, XML, sync)
if [ -d "${DIR_PIONEER}" ]; then
    echo "💾 Copia database da ${DIR_PIONEER} ..."
    cp -R "${DIR_PIONEER}/" "${BACKUP_DIR}/Pioneer_Library/"
    echo "✅ Database e libreria copiati."
else
    echo "❌ Errore: Cartella non trovata in ${DIR_PIONEER}"
fi

# 2. Backup impostazioni e preferenze aggiuntive (se presenti)
if [ -d "${DIR_APPSUPPORT}" ]; then
    mkdir -p "${BACKUP_DIR}/Application_Support"
    echo "💾 Copia configurazioni da ${DIR_APPSUPPORT} ..."
    cp -R "${DIR_APPSUPPORT}/" "${BACKUP_DIR}/Application_Support/"
    echo "✅ Configurazioni copiate."
fi

echo ""
echo "🎉 Backup Mac completato in: ${BACKUP_DIR}"
ls -lh "${BACKUP_DIR}/Pioneer_Library"