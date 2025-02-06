class Messages:
    # Mensajes de Autenticación
    AUTH_ENTER_CODE = "Por favor, introduce el código de verificación: "
    AUTH_ENTER_2FA = "Por favor, introduce tu contraseña de dos factores: "

    # Mensajes de Estado y Conexión
    CONNECTION_ESTABLISHED = "✅ Conexión establecida"
    CONNECTION_LOST = "❌ Conexión perdida"
    CONNECTION_RETRYING = "🔄 Reintento {count}: {error}"
    CONNECTION_WAIT = "⏳ Esperando {seconds} segundos..."

    # Mensajes de Comandos
    CMD = {
        "ADD": {
            "USAGE": "❌ Uso: /add <channel_id>",
            "SUCCESS": "✅ Canal '{name}' añadido correctamente",
            "EXISTS": "⚠️ El canal '{name}' ya existe",
            "NOT_CHANNEL": "❌ El ID proporcionado no corresponde a un canal",
            "INVALID_ID": "❌ ID de canal inválido",
            "ERROR": "❌ Error añadiendo canal: {error}"
        },
        "DEL": {
            "USAGE": "❌ Uso: /del <channel_id>",
            "SUCCESS": "✅ Canal '{name}' (ID: {id}) eliminado. {deleted_files} archivos eliminados.",
            "DIRS_REMOVED": "\nDirectorios eliminados: {dirs}",
            "NOT_FOUND": "❌ Canal no encontrado",
            "INVALID_ID": "❌ ID de canal inválido",
            "ERROR": "❌ Error eliminando canal: {error}"
        },
        "EDIT": {
            "USAGE": "❌ Uso: /edit <message_id> <movie|serie> \"<nombre>\"",
            "SUCCESS": "✅ Archivo renombrado:\n- Anterior: {old}\n- Nuevo: {new}",
            "NOT_FOUND": "❌ Archivo no encontrado",
            "NO_ACCESS": "❌ No se puede acceder al archivo",
            "INVALID_TYPE": "❌ Tipo inválido. Usar 'movie' o 'serie'",
            "INVALID_MOVIE": "❌ Formato película inválido. Usar: \"Nombre (YYYY)\"",
            "INVALID_SERIES": "❌ Formato serie inválido. Usar: \"Nombre SXXEXX\"",
            "INVALID_FORMAT": "❌ Formato de nombre inválido",
            "ERROR": "❌ Error procesando archivo"
        },
        "EDIT_LIST": {
            "HEADER": "📝 Archivos pendientes de procesar:",
            "EMPTY": "✅ No hay archivos pendientes de procesar",
            "CONTINUE": "📝 Continuación de la lista:",
            "FOOTER": "\nUsa /edit <message_id> <movie|serie> \"<nombre>\" para procesar"
        },
        "STATUS": {
            "HEADER": "📊 Estado del Sistema",
            "CHANNELS_HEADER": "\n📺 Canales:",
            "NO_CHANNELS": "❌ No hay canales configurados",
            "CHANNEL_INFO": "\n{name} (ID: {id})\n[{progress_bar}] {percentage:.1f}%\n{processed}/{total} mensajes\nEstado: {status}\nÚltima actualización: {last_update}",
            "STATS_HEADER": "\n📈 Estadísticas:",
            "STATS": "Total archivos: {total}\nPelículas: {movies}\nSeries: {series}\nEpisodios: {episodes}",
            "UNRECOGNIZED": "\n⚠️ {count} archivos sin procesar",
            "ERROR": "❌ Error obteniendo estado: {error}"
        },
        "IMPORT": {
            "START": "🔄 Importando canales suscritos...",
            "COMPLETE": "✅ Importación completada:\n- Añadidos: {added}\n- Omitidos: {skipped}\n- Total canales: {total}",
            "INDEXING": "\n🔄 Comenzando indexación de nuevos canales...",
            "ERROR": "❌ Error importando canales: {error}"
        }
    }

    # Mensajes de Error HTTP
    HTTP_FILE_NOT_FOUND = "❌ Archivo no encontrado"
    HTTP_SERVER_ERROR = "❌ Error del servidor: {error}"

    # Mensajes de Log
    LOG_ERROR = "Error: {error}"
    LOG_FATAL_ERROR = "Error fatal: {error}"
    LOG_FILE_NOT_RECOGNIZED = "Archivo no reconocido: {filename} (ID: {id})"
    LOG_FILE_PROCESSED = "Archivo procesado: {path}"
    LOG_PROCESSING_ERROR = "Error procesando archivo {filename}: {error}"
    LOG_STREAMING_ERROR = "Error de streaming para ID {id}: {error}"
    LOG_INDEXING_ERROR = "Error indexando canal {channel}: {error}"
    LOG_SERVER_START = "Servidor iniciado en puerto {port}"
    LOG_PROGRAM_END = "Programa finalizado"
    LOG_NO_MESSAGES = "No hay mensajes en el canal"

    # Mensajes TMDB
    TMDB_SEARCH = "🔍 TMDB: Buscando '{}'"
    TMDB_FOUND_MOVIE = "✅ TMDB: Encontrada película '{}' ({})"
    TMDB_TRY_SERIE = "🔄 TMDB: No es película, buscando como serie..."
    TMDB_FOUND_SERIE = "✅ TMDB: Encontrada serie '{}' ({})"
    TMDB_NOT_FOUND = "❌ TMDB: No se encontraron resultados para '{}'"
    TMDB_TYPE_CHECK = "🎯 TMDB: Tipo detectado '{}', verificando con guessit..."
    TMDB_TYPE_CONFLICT = "⚠️ TMDB/Guessit: Conflicto de tipos - TMDB:{} vs Guessit:{}"
    TMDB_ERROR = "❌ Error en búsqueda TMDB: {}"
