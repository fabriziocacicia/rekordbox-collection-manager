#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# Script di Ripristino (Restore) Libreria e Database Rekordbox per macOS
# ==============================================================================
# Ripristina i file di backup creati con 'backup_rekordbox_mac.sh' o tramite
# l'applicazione 'Rekordbox Collection Manager'.
# ==============================================================================

BACKUP_ROOT="${HOME}/Music/rekordbox/Backups"
DIR_PIONEER="${HOME}/Library/Pioneer/rekordbox"
DIR_APPSUPPORT="${HOME}/Library/Application Support/Pioneer/rekordbox"

show_help() {
    cat << 'EOF'
Uso:
  ./scripts/restore_rekordbox_mac.sh [OPZIONI] [PERCORSO_CARTELLA_BACKUP]

Descrizione:
  Ripristina la libreria Rekordbox (master.db, impostazioni, playlist, ecc.)
  a partire da un backup precedentemente creato.
  
  Se non viene specificato alcun percorso, lo script cerca automaticamente
  i backup presenti nella cartella predefinita:
    ~/Music/rekordbox/Backups
  e permette di scegliere quale ripristinare (proponendo come predefinito il più recente).

Opzioni:
  -y, --yes      Salta la richiesta di conferma interattiva (modalità automatica)
  -h, --help     Mostra questo messaggio di aiuto ed esce

Esempi:
  # Cerca i backup in ~/Music/rekordbox/Backups e chiedi conferma interattiva:
  ./scripts/restore_rekordbox_mac.sh

  # Ripristina una specifica cartella di backup:
  ./scripts/restore_rekordbox_mac.sh ~/Music/rekordbox/Backups/rekordbox_mac_backup_20260920_002628

  # Ripristina senza prompt di conferma (scripting/CI):
  ./scripts/restore_rekordbox_mac.sh -y ~/Music/rekordbox/Backups/rekordbox_mac_backup_20260920_002628
EOF
}

# 1. Parsing argomenti
AUTO_YES=false
CUSTOM_BACKUP_PATH=""

while [ $# -gt 0 ]; do
    case "$1" in
        -h|--help)
            show_help
            exit 0
            ;;
        -y|--yes)
            AUTO_YES=true
            shift
            ;;
        *)
            if [ -z "${CUSTOM_BACKUP_PATH}" ]; then
                CUSTOM_BACKUP_PATH="$1"
            else
                echo "❌ Parametro non riconosciuto: $1"
                echo "Usa -h o --help per istruzioni."
                exit 1
            fi
            shift
            ;;
    esac
done

# 2. Controllo processi attivi (Rekordbox e Rekordbox Collection Manager)
if [ "${SKIP_PROCESS_CHECK:-0}" != "1" ] && pgrep -il "rekordbox" > /dev/null 2>&1; then
    ACTIVE_PROCS=$(pgrep -il "rekordbox" | awk -v pid="$$" '$1 != pid {print $0}')
    if [ -n "${ACTIVE_PROCS}" ]; then
        echo "⚠️  Rekordbox o Rekordbox Collection Manager è ancora in esecuzione!"
        echo "   Chiudilo prima di procedere con il ripristino per evitare conflitti o corruzioni."
        echo "   Processi rilevati:"
        echo "${ACTIVE_PROCS}"
        exit 1
    fi
fi

# 3. Individuazione della cartella di backup
SELECTED_BACKUP=""

if [ -n "${CUSTOM_BACKUP_PATH}" ]; then
    # Espansione tilde o percorsi relativi
    if [[ "${CUSTOM_BACKUP_PATH}" == ~* ]]; then
        CUSTOM_BACKUP_PATH="${HOME}${CUSTOM_BACKUP_PATH:1}"
    fi
    CUSTOM_BACKUP_PATH=$(cd "$(dirname "${CUSTOM_BACKUP_PATH}")" 2>/dev/null && pwd)/$(basename "${CUSTOM_BACKUP_PATH}")

    if [ ! -d "${CUSTOM_BACKUP_PATH}" ]; then
        echo "❌ La cartella di backup specificata non esiste:"
        echo "   ${CUSTOM_BACKUP_PATH}"
        exit 1
    fi
    SELECTED_BACKUP="${CUSTOM_BACKUP_PATH}"
else
    # Ricerca automatica dei backup nella cartella predefinita
    mkdir -p "${BACKUP_ROOT}"
    BACKUP_CANDIDATES=()
    while IFS= read -r -d $'\0' dir; do
        BACKUP_CANDIDATES+=("$dir")
    done < <(find "${BACKUP_ROOT}" -maxdepth 1 -type d \( -name "rekordbox_mac_backup_*" -o -name "rekordbox_backup_*" \) -print0 2>/dev/null | sort -zr)

    if [ ${#BACKUP_CANDIDATES[@]} -eq 0 ]; then
        echo "❌ Nessun backup Rekordbox trovato in: ${BACKUP_ROOT}"
        echo "   Puoi specificare manualmente il percorso della cartella:"
        echo "   $0 /percorso/alla/cartella_backup"
        exit 1
    elif [ ${#BACKUP_CANDIDATES[@]} -eq 1 ]; then
        SELECTED_BACKUP="${BACKUP_CANDIDATES[0]}"
        echo "📦 Trovato 1 backup in ${BACKUP_ROOT}:"
        echo "   ${SELECTED_BACKUP}"
    else
        echo "📦 Trovati ${#BACKUP_CANDIDATES[@]} backup in ${BACKUP_ROOT}:"
        for i in "${!BACKUP_CANDIDATES[@]}"; do
            idx=$((i + 1))
            bname=$(basename "${BACKUP_CANDIDATES[$i]}")
            if [ "$i" -eq 0 ]; then
                echo "   ${idx}) ${bname} (Più recente ⭐)"
            else
                echo "   ${idx}) ${bname}"
            fi
        done

        if [ "${AUTO_YES}" = true ]; then
            SELECTED_BACKUP="${BACKUP_CANDIDATES[0]}"
            echo "⚡ Modalità automatica: selezionato il backup più recente."
        else
            echo ""
            read -r -p "Seleziona il backup da ripristinare [1-${#BACKUP_CANDIDATES[@]}] (default: 1): " choice
            choice="${choice:-1}"
            if ! [[ "$choice" =~ ^[0-9]+$ ]] || [ "$choice" -lt 1 ] || [ "$choice" -gt ${#BACKUP_CANDIDATES[@]} ]; then
                echo "❌ Scelta non valida: '$choice'."
                exit 1
            fi
            SELECTED_BACKUP="${BACKUP_CANDIDATES[$((choice - 1))]}"
        fi
    fi
fi

# 4. Verifica del contenuto del backup
PIONEER_SRC=""
if [ -d "${SELECTED_BACKUP}/Pioneer_Library" ] && [ -f "${SELECTED_BACKUP}/Pioneer_Library/master.db" ]; then
    PIONEER_SRC="${SELECTED_BACKUP}/Pioneer_Library"
elif [ -f "${SELECTED_BACKUP}/master.db" ]; then
    PIONEER_SRC="${SELECTED_BACKUP}"
elif [ "$(basename "${SELECTED_BACKUP}")" = "Pioneer_Library" ] && [ -f "${SELECTED_BACKUP}/master.db" ]; then
    PIONEER_SRC="${SELECTED_BACKUP}"
else
    echo "❌ Errore: La cartella non contiene un database Rekordbox valido ('master.db' non trovato):"
    echo "   ${SELECTED_BACKUP}"
    exit 1
fi

APP_SUPPORT_SRC=""
if [ -d "${SELECTED_BACKUP}/Application_Support" ]; then
    APP_SUPPORT_SRC="${SELECTED_BACKUP}/Application_Support"
fi

# 5. Riepilogo e Richiesta di Conferma
DB_SIZE=$(ls -lh "${PIONEER_SRC}/master.db" | awk '{print $5}')
DB_MOD=$(stat -f "%Sm" -t "%Y-%m-%d %H:%M:%S" "${PIONEER_SRC}/master.db" 2>/dev/null || date -r "${PIONEER_SRC}/master.db" "+%Y-%m-%d %H:%M:%S" 2>/dev/null || echo "N/D")

echo ""
echo "======================================================================"
echo "⚠️  ATTENZIONE: RIPRISTINO LIBRERIA REKORDBOX"
echo "======================================================================"
echo " Origine Backup : ${SELECTED_BACKUP}"
echo " Database       : master.db (${DB_SIZE}, modificato il ${DB_MOD})"
echo " Destinazione   : ${DIR_PIONEER}"
if [ -n "${APP_SUPPORT_SRC}" ]; then
    echo " Configurazioni : ${DIR_APPSUPPORT}"
fi
echo "======================================================================"
echo "🛡️  Verrà creato un backup di sicurezza dell'attuale libreria prima del ripristino."
echo ""

if [ "${AUTO_YES}" != true ]; then
    read -r -p "Sei sicuro di voler procedere con il ripristino? (s/N): " confirm
    case "${confirm}" in
        [sS]|[sS][iI]|[yY]|[yY][eE][sS])
            echo ""
            ;;
        *)
            echo "❌ Operazione annullata dall'utente. Nessun file è stato modificato."
            exit 0
            ;;
    esac
fi

# 6. Creazione Backup di Emergenza (Safety Pre-Restore Snapshot)
SAFETY_TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
SAFETY_BACKUP_DIR="${BACKUP_ROOT}/rekordbox_pre_restore_safety_${SAFETY_TIMESTAMP}"

echo "🛡️  1/3 Creazione backup di emergenza dello stato attuale..."
mkdir -p "${SAFETY_BACKUP_DIR}/Pioneer_Library"

if [ -d "${DIR_PIONEER}" ]; then
    cp -R "${DIR_PIONEER}/" "${SAFETY_BACKUP_DIR}/Pioneer_Library/"
    echo "   ✅ Libreria attuale archiviata in: ${SAFETY_BACKUP_DIR}/Pioneer_Library"
fi

if [ -d "${DIR_APPSUPPORT}" ]; then
    mkdir -p "${SAFETY_BACKUP_DIR}/Application_Support"
    cp -R "${DIR_APPSUPPORT}/" "${SAFETY_BACKUP_DIR}/Application_Support/"
    echo "   ✅ Configurazioni attuali archiviate in: ${SAFETY_BACKUP_DIR}/Application_Support"
fi

# 7. Ripristino del Database e della Libreria Principale
echo ""
echo "🔄 2/3 Ripristino database principale in ${DIR_PIONEER}..."
mkdir -p "${DIR_PIONEER}"

# Sincronizzazione atomica con eliminazione file orfani o transitori
if command -v rsync > /dev/null 2>&1; then
    rsync -aI --delete "${PIONEER_SRC}/" "${DIR_PIONEER}/"
else
    # Fallback sicuro se rsync non fosse disponibile
    rm -rf "${DIR_PIONEER:?}"/*
    cp -R "${PIONEER_SRC}/." "${DIR_PIONEER}/"
fi

# Rimuovi file WAL/SHM temporanei se non erano presenti nel backup originale
if [ ! -f "${PIONEER_SRC}/master.db-wal" ]; then
    rm -f "${DIR_PIONEER}/master.db-wal"
fi
if [ ! -f "${PIONEER_SRC}/master.db-shm" ]; then
    rm -f "${DIR_PIONEER}/master.db-shm"
fi

echo "   ✅ Database principale e file di libreria ripristinati."

# 8. Ripristino Configurazioni Application Support (se presenti nel backup)
if [ -n "${APP_SUPPORT_SRC}" ]; then
    echo ""
    echo "🔄 3/3 Ripristino configurazioni in ${DIR_APPSUPPORT}..."
    mkdir -p "${DIR_APPSUPPORT}"
    if command -v rsync > /dev/null 2>&1; then
        rsync -aI --delete "${APP_SUPPORT_SRC}/" "${DIR_APPSUPPORT}/"
    else
        rm -rf "${DIR_APPSUPPORT:?}"/*
        cp -R "${APP_SUPPORT_SRC}/." "${DIR_APPSUPPORT}/"
    fi
    echo "   ✅ Configurazioni ripristinate."
else
    echo "ℹ️  3/3 Nessuna configurazione Application_Support presente nel backup (passaggio saltato)."
fi

# 9. Verifica finale
echo ""
if [ -f "${DIR_PIONEER}/master.db" ]; then
    FINAL_SIZE=$(ls -lh "${DIR_PIONEER}/master.db" | awk '{print $5}')
    echo "🎉 Ripristino completato con successo!"
    echo "📊 File ripristinato: ${DIR_PIONEER}/master.db (${FINAL_SIZE})"
    echo ""
    echo "💡 Consiglio: Ora puoi avviare Rekordbox in tutta sicurezza."
    echo "   In caso di necessità, il tuo stato precedente è conservato in:"
    echo "   ${SAFETY_BACKUP_DIR}"
else
    echo "❌ Errore critico: master.db non trovato in ${DIR_PIONEER} dopo il ripristino!"
    echo "   Puoi recuperare i tuoi file dal backup di emergenza:"
    echo "   ${SAFETY_BACKUP_DIR}"
    exit 1
fi
