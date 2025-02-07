#!/usr/bin/env python3
import os
import asyncio
import aiohttp
from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError, MessageIdInvalidError, FloodWaitError
from telethon.tl.types import PeerChannel
from aiohttp import web
import sqlite3
import logging
from pathlib import Path
from guessit import guessit
from dotenv import load_dotenv
from collections import deque
from datetime import datetime, timedelta
import time
import re
from messages import Messages as msg
from aiohttp import web
from urllib.parse import quote
import shlex

# Cargar variables de entorno
load_dotenv()

# Configuración básica
API_ID = os.getenv('API_ID')
API_HASH = os.getenv('API_HASH')
PHONE_NUMBER = os.getenv('PHONE_NUMBER')
PROXY_PORT = int(os.getenv('PROXY_PORT', 8080))
BASE_URL = os.getenv('BASE_URL', f'http://localhost:{PROXY_PORT}')
CONTROL_CHANNEL_ID = int(os.getenv('CONTROL_CHANNEL_ID'))
MOVIES_FOLDER = Path(os.getenv('MOVIES_FOLDER'))
SERIES_FOLDER = Path(os.getenv('SERIES_FOLDER'))
DB_PATH = os.getenv('DB_PATH', 'robingood.db')
RATE_LIMIT = int(os.getenv('RATE_LIMIT', 20))
RECONNECTION_INTERVAL = int(os.getenv('RECONNECTION_INTERVAL', 300))
CHUNK_SIZE = 512 * 1024  # 1MB
# Añadir después de las variables de entorno existentes
BATCH_SIZE = int(os.getenv('BATCH_SIZE', 50))  # Tamaño del lote para iter_messages
TMDB_API_KEY = os.getenv('TMDB_API_KEY')
TMDB_LANGUAGE = os.getenv('TMDB_LANGUAGE', 'es-ES')

# Configuración de logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Variables globales
client = None
rate_limiter = None
media_library = None
conn = None
cursor = None

class RateLimiter:
    def __init__(self, operations_per_minute=20):
        self.operations_per_minute = operations_per_minute
        self.operations = deque()
        self.lock = asyncio.Lock()
        self.last_operation_time = 0
        self.min_interval = 1.0 / (operations_per_minute / 60)

    async def acquire(self):
        async with self.lock:
            now = time.time()
            while self.operations and now - self.operations[0] >= 60:
                self.operations.popleft()
            if len(self.operations) >= self.operations_per_minute:
                wait_time = 60 - (now - self.operations[0])
                if wait_time > 0:
                    logger.debug(f"Rate limit: esperando {wait_time:.2f} segundos")
                    await asyncio.sleep(wait_time)
                    now = time.time()
            time_since_last = now - self.last_operation_time
            if time_since_last < self.min_interval:
                await asyncio.sleep(self.min_interval - time_since_last)
                now = time.time()
            self.operations.append(now)
            self.last_operation_time = now

class MediaLibrary:
    def __init__(self, movies_folder, series_folder):
        self.movies_folder = Path(movies_folder)
        self.series_folder = Path(series_folder)
        self._is_connected = True
        self._visible_paths = {
            'movies': self.movies_folder,
            'series': self.series_folder
        }
        self._hidden_paths = {
            'movies': self.movies_folder.parent / f".{self.movies_folder.name}",
            'series': self.series_folder.parent / f".{self.series_folder.name}"
        }

    @property
    def is_connected(self):
        return self._is_connected

    @is_connected.setter
    def is_connected(self, value):
        if value != self._is_connected:
            self._is_connected = value
            self._update_folder_visibility()

    def _update_folder_visibility(self):
        try:
            for media_type in ['movies', 'series']:
                visible_path = self._visible_paths[media_type]
                hidden_path = self._hidden_paths[media_type]
                if self.is_connected:
                    if hidden_path.exists():
                        hidden_path.rename(visible_path)
                        logger.info(f"Carpeta {visible_path} visible")
                else:
                    if visible_path.exists():
                        visible_path.rename(hidden_path)
                        logger.info(f"Carpeta {visible_path} oculta")
        except Exception as e:
            logger.error(f"Error actualizando visibilidad de carpetas: {str(e)}")

class ConnectionManager:
    def __init__(self, client, media_library):
        self.client = client
        self.media_library = media_library
        self.is_connected = False
        self.retry_count = 0
        self.max_retries = float('inf')

    async def handle_connection_status(self, is_connected: bool):
        if is_connected != self.is_connected:
            self.is_connected = is_connected
            self.media_library.is_connected = is_connected
            if is_connected:
                logger.info(msg.CONNECTION_ESTABLISHED)
                self.retry_count = 0
            else:
                logger.warning(msg.CONNECTION_LOST)

    async def ensure_connected(self):
        while True:
            try:
                if not self.client.is_connected():
                    await self.handle_connection_status(False)
                    try:
                        await rate_limiter.acquire()
                        await self.client.connect()
                        if not await self.client.is_user_authorized():
                            await authenticate()
                        await self.handle_connection_status(True)
                    except Exception as e:
                        self.retry_count += 1
                        wait_time = min(300, self.retry_count * 30)
                        logger.error(msg.CONNECTION_RETRYING.format(count=self.retry_count, error=str(e)))
                        logger.info(msg.CONNECTION_WAIT.format(seconds=wait_time))
                        await asyncio.sleep(wait_time)
                else:
                    await self.handle_connection_status(True)
            except Exception as e:
                logger.error(msg.LOG_ERROR.format(error=str(e)))
            await asyncio.sleep(60)

    async def start(self):
        while True:
            try:
                await self.ensure_connected()
            except Exception as e:
                logger.error(msg.LOG_FATAL_ERROR.format(error=str(e)))
                await asyncio.sleep(RECONNECTION_INTERVAL)

def init_globals():
    """Inicializa las variables globales."""
    global client, rate_limiter, media_library, conn, cursor

    rate_limiter = RateLimiter(RATE_LIMIT)
    media_library = MediaLibrary(MOVIES_FOLDER, SERIES_FOLDER)
    client = TelegramClient('streaming', API_ID, API_HASH)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Crear tablas
    # Crear tablas
    cursor.execute('''CREATE TABLE IF NOT EXISTS files
                (file_id TEXT,
                 channel_id INTEGER,
                 file_path TEXT,
                 name TEXT,
                 type TEXT,
                 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                 PRIMARY KEY (file_id, channel_id))''')

    cursor.execute('''CREATE TABLE IF NOT EXISTS channels
                (channel_id INTEGER PRIMARY KEY,
                 name TEXT,
                 last_processed_id INTEGER DEFAULT 0,
                 added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')

    cursor.execute('''CREATE TABLE IF NOT EXISTS indexing_status
                (channel_id INTEGER PRIMARY KEY,
                 total_messages INTEGER,
                 processed_messages INTEGER,
                 status TEXT,
                 last_update TIMESTAMP)''')

    cursor.execute('''CREATE TABLE IF NOT EXISTS unrecognized_files
                (message_id TEXT,
                 channel_id INTEGER,
                 filename TEXT,
                 attempts INTEGER DEFAULT 1,
                 PRIMARY KEY (message_id, channel_id))''')

    # Añadir en la sección de creación de tablas
    cursor.execute('''CREATE TABLE IF NOT EXISTS skipped_files
                (message_id TEXT,
                 channel_id INTEGER,
                 filename TEXT,
                 skip_reason TEXT,
                 skipped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                 PRIMARY KEY (message_id, channel_id))''')

    # Registrar handlers después de inicializar el cliente
    register_handlers()

async def authenticate():
    """Maneja la autenticación con Telegram."""
    if not client.is_connected():
        await client.connect()

    if not await client.is_user_authorized():
        await client.send_code_request(PHONE_NUMBER)
        try:
            code = input(msg.AUTH_ENTER_CODE)
            await client.sign_in(PHONE_NUMBER, code)
        except SessionPasswordNeededError:
            password = input(msg.AUTH_ENTER_2FA)
            await client.sign_in(password=password)

# Funciones handler
async def add_channel_handler(event):
    """Handler para el comando /add"""
    if event.chat_id != CONTROL_CHANNEL_ID:
        return

    try:
        args = event.text.split()
        if len(args) != 2:
            await event.respond(msg.CMD["ADD"]["USAGE"])
            return

        channel_id = int(args[1])
        await rate_limiter.acquire()

        try:
            channel = await client.get_entity(PeerChannel(channel_id))
        except ValueError:
            # Intentar obtener el canal directamente si PeerChannel falla
            channel = await client.get_entity(channel_id)

        if not getattr(channel, 'broadcast', False):
            await event.respond(msg.CMD["ADD"]["NOT_CHANNEL"])
            return

        cursor.execute("""
            INSERT OR IGNORE INTO channels (channel_id, name)
            VALUES (?, ?)
        """, (channel_id, channel.title))

        if cursor.rowcount > 0:
            # Asegurarse de que se hace commit antes de iniciar la indexación
            conn.commit()
            await event.respond(msg.CMD["ADD"]["SUCCESS"].format(name=channel.title))
            # Crear una nueva tarea para indexar el canal
            asyncio.create_task(index_channel(channel_id))
        else:
            # Verificar si el canal ya existe
            cursor.execute("SELECT name FROM channels WHERE channel_id = ?", (channel_id,))
            existing = cursor.fetchone()
            if existing:
                await event.respond(msg.CMD["ADD"]["EXISTS"].format(name=existing[0]))
            else:
                raise Exception("Error al insertar el canal en la base de datos")

    except ValueError as e:
        await event.respond(msg.CMD["ADD"]["INVALID_ID"])
        logger.error(f"Error en add_channel_handler (ValueError): {str(e)}")
    except Exception as e:
        await event.respond(msg.CMD["ADD"]["ERROR"].format(error=str(e)))
        logger.error(f"Error en add_channel_handler: {str(e)}")

async def edit_list_handler(event):
    """Handler para el comando /edit list"""
    if event.chat_id != CONTROL_CHANNEL_ID:
        return

    try:
        args = event.text.split()
        media_type = args[2] if len(args) > 2 else None

        if media_type and media_type not in ['movie', 'serie']:
            await event.respond(msg.CMD["EDIT_LIST"]["INVALID_TYPE"])
            return

        # Query base para obtener archivos no excluidos
        query = """
            SELECT message_id, channel_id, filename
            FROM unrecognized_files
            WHERE NOT EXISTS (
                SELECT 1 FROM skipped_files
                WHERE skipped_files.message_id = unrecognized_files.message_id
                AND skipped_files.channel_id = unrecognized_files.channel_id
            )
        """

        cursor.execute(query)
        files = cursor.fetchall()

        if not files:
            await event.respond(msg.CMD["EDIT_LIST"]["EMPTY"])
            return

        # Organizar archivos por tipo
        movies = []
        series = []

        for message_id, channel_id, filename in files:
            # Configuración mejorada de guessit
            options = {
                'name_only': True,
                'advanced': True,
                'expected_title': ['movie', 'episode']
            }
            guess = guessit(filename, options)

            if guess.get('type') == 'movie':
                movies.append((message_id, channel_id, filename))
            elif guess.get('type') == 'episode':
                series.append((message_id, channel_id, filename))

        # Preparar respuesta según el tipo solicitado
        response = [msg.CMD["EDIT_LIST"]["HEADER"]]

        if not media_type or media_type == 'movie':
            response.append("\n🎬 PELÍCULAS:")
            for message_id, channel_id, filename in movies:
                response.append(f"`{message_id}` - {filename}")

        if not media_type or media_type == 'serie':
            response.append("\n📺 SERIES:")
            for message_id, channel_id, filename in series:
                response.append(f"`{message_id}` - {filename}")

        # Enviar respuesta en bloques si es necesario
        current_block = []
        for line in response:
            current_block.append(line)
            if len('\n'.join(current_block)) > 4000:  # Límite de Telegram
                await event.respond('\n'.join(current_block))
                current_block = []

        if current_block:
            current_block.append(msg.CMD["EDIT_LIST"]["FOOTER"])
            await event.respond('\n'.join(current_block))

    except Exception as e:
        logger.error(msg.LOG_ERROR.format(error=str(e)))
        await event.respond(f"{msg.ERROR_PREFIX}{str(e)}")

async def skip_handler(event):
    """Handler para el comando /skip"""
    if event.chat_id != CONTROL_CHANNEL_ID:
        return

    try:
        args = event.text.split(maxsplit=2)
        if len(args) < 2:
            await event.respond(msg.CMD["SKIP"]["USAGE"])
            return

        # Procesar rango de IDs y razón
        id_range = args[1]
        reason = args[2] if len(args) > 2 else "skipped by user"

        # Procesar rango
        message_ids = []
        for part in id_range.split(','):
            if '-' in part:
                start, end = map(int, part.strip('[]').split('-'))
                message_ids.extend(range(start, end + 1))
            else:
                message_ids.append(int(part.strip('[]')))

        message_ids = sorted(list(set(message_ids)))  # Eliminar duplicados y ordenar

        skipped = 0
        errors = 0

        for message_id in message_ids:
            try:
                cursor.execute("""
                    INSERT INTO skipped_files (message_id, channel_id, filename, skip_reason)
                    SELECT message_id, channel_id, filename, ?
                    FROM unrecognized_files
                    WHERE message_id = ?
                    AND NOT EXISTS (
                        SELECT 1 FROM skipped_files
                        WHERE message_id = unrecognized_files.message_id
                    )
                """, (reason, str(message_id)))

                if cursor.rowcount > 0:
                    skipped += 1
                else:
                    errors += 1

            except Exception as e:
                logger.error(f"Error skipping message {message_id}: {str(e)}")
                errors += 1

        conn.commit()
        await event.respond(msg.CMD["SKIP"]["RESULT"].format(
            skipped=skipped,
            errors=errors,
            total=len(message_ids)
        ))

    except Exception as e:
        logger.error(msg.LOG_ERROR.format(error=str(e)))
        await event.respond(f"{msg.ERROR_PREFIX}{str(e)}")

async def edit_handler(event):
    """Handler para el comando /edit"""
    if event.chat_id != CONTROL_CHANNEL_ID:
        return

    try:
        # Dividir manteniendo las comillas
        args = shlex.split(event.text)
        if len(args) != 4:  # /edit <tipo> <nombre_actual> <nombre_nuevo>
            await event.respond(msg.CMD["EDIT"]["USAGE"])
            return

        media_type = args[1].lower()
        current_name = args[2]  # nombre actual
        new_name = args[3]      # nombre nuevo

        if media_type not in ['movie', 'serie']:
            await event.respond(msg.CMD["EDIT"]["INVALID_TYPE"])
            return

        cursor.execute("""
            SELECT message_id, channel_id, filename
            FROM unrecognized_files
            WHERE filename LIKE ?
            AND NOT EXISTS (
                SELECT 1 FROM skipped_files
                WHERE skipped_files.message_id = unrecognized_files.message_id
            )
        """, (f"%{current_name}%",))

        files = cursor.fetchall()
        if not files:
            await event.respond(msg.CMD["EDIT"]["NOT_FOUND"])
            return

        success = 0
        errors = 0

        # Procesar en lotes usando BATCH_SIZE
        for i in range(0, len(files), BATCH_SIZE):
            batch = files[i:i + BATCH_SIZE]
            message_ids = [int(msg_id) for msg_id, channel_id, _ in batch]
            channel_id = batch[0][1]  # Todos los mensajes del mismo canal

            await rate_limiter.acquire()
            messages = await client.get_messages(channel_id, ids=message_ids)

            for message, (msg_id, _, filename) in zip(messages, batch):
                if not message or not message.file:
                    errors += 1
                    continue

                if await process_media_message_with_name(
                    message,
                    channel_id,
                    f"{new_name}{Path(filename).suffix}"
                ):
                    cursor.execute("""
                        DELETE FROM unrecognized_files
                        WHERE message_id = ? AND channel_id = ?
                    """, (str(msg_id), channel_id))
                    success += 1
                else:
                    errors += 1

        conn.commit()
        await event.respond(msg.CMD["EDIT"]["BATCH_RESULT"].format(
            success=success,
            errors=errors,
            total=len(files)
        ))

    except Exception as e:
        logger.error(msg.LOG_ERROR.format(error=str(e)))
        await event.respond(f"{msg.ERROR_PREFIX}{str(e)}")
async def process_media_message_with_name(message, channel_id, forced_name):
    """Procesa un mensaje de media con un nombre forzado."""
    if not message.file or not message.file.mime_type.startswith('video/'):
        return False

    try:
        media_info = guessit(forced_name)

        if not media_info or 'type' not in media_info:
            return False

        if media_info['type'] == 'movie':
            base_folder = MOVIES_FOLDER
            folder = base_folder / f"{media_info['title']} ({media_info.get('year', 'XXXX')})"
            file_name = f"{media_info['title']} ({media_info.get('year', 'XXXX')}){Path(forced_name).suffix}"
        else:
            base_folder = SERIES_FOLDER
            folder = base_folder / media_info['title'] / f"Season {media_info.get('season', 1)}"
            file_name = f"{media_info['title']} - S{media_info.get('season', 1):02d}E{media_info.get('episode', 1):02d}{Path(forced_name).suffix}"

        folder.mkdir(parents=True, exist_ok=True)
        strm_path = folder / f"{Path(file_name).stem}.strm"

        with strm_path.open('w') as f:
            f.write(f"{BASE_URL}/{channel_id}/{message.id}")

        cursor.execute("""
            INSERT OR REPLACE INTO files (file_id, channel_id, file_path, type)
            VALUES (?, ?, ?, ?)
        """, (str(message.id), channel_id, str(strm_path), media_info['type']))
        conn.commit()

        logger.info(msg.LOG_FILE_PROCESSED.format(path=strm_path))
        return True

    except Exception as e:
        logger.error(msg.LOG_PROCESSING_ERROR.format(filename=forced_name, error=str(e)))
        return False

async def status_handler(event):
    """Handler para el comando /status"""
    if event.chat_id != CONTROL_CHANNEL_ID:
        return

    try:
        cursor.execute("""
            SELECT
                COUNT(DISTINCT CASE WHEN type = 'movie' THEN file_id END) as movies,
                COUNT(DISTINCT CASE WHEN type = 'episode' THEN
                    substr(file_path, 0, instr(file_path, '/Season')) END) as series,
                COUNT(CASE WHEN type = 'episode' THEN 1 END) as episodes,
                COUNT(*) as total_files
            FROM files
        """)
        total_movies, total_series, total_episodes, total_files = cursor.fetchone()

        cursor.execute("""
            SELECT
                c.channel_id,
                c.name,
                i.total_messages,
                i.processed_messages,
                i.status,
                i.last_update
            FROM channels c
            LEFT JOIN indexing_status i ON c.channel_id = i.channel_id
            ORDER BY c.name
        """)
        channels = cursor.fetchall()

        cursor.execute("SELECT COUNT(*) FROM unrecognized_files")
        unrecognized_count = cursor.fetchone()[0]

        response = [msg.CMD["STATUS"]["HEADER"]]

        if channels:
            response.append(msg.CMD["STATUS"]["CHANNELS_HEADER"])
            for channel in channels:
                channel_id, name, total, processed, status, last_update = channel
                progress_bar = '█' * int((processed or 0)/(total or 1)*10) + '▒' * (10-int((processed or 0)/(total or 1)*10))
                percentage = ((processed or 0) / (total or 1)) * 100 if total else 0

                response.append(msg.CMD["STATUS"]["CHANNEL_INFO"].format(
                    name=name,
                    id=channel_id,
                    progress_bar=progress_bar,
                    percentage=percentage,
                    processed=processed or 0,
                    total=total or 0,
                    status=status or 'sin indexar',
                    last_update=last_update or 'nunca'
                ))
        else:
            response.append(msg.CMD["STATUS"]["NO_CHANNELS"])

        response.append(msg.CMD["STATUS"]["STATS_HEADER"])
        response.append(msg.CMD["STATUS"]["STATS"].format(
            total=total_files or 0,
            movies=total_movies or 0,
            series=total_series or 0,
            episodes=total_episodes or 0
        ))

        if unrecognized_count > 0:
            response.append(msg.CMD["STATUS"]["UNRECOGNIZED"].format(count=unrecognized_count))

        await event.respond("\n".join(response))

    except Exception as e:
        logger.error(msg.LOG_ERROR.format(error=str(e)))
        await event.respond(msg.CMD["STATUS"]["ERROR"].format(error=str(e)))

async def del_channel_handler(event):
    """Handler para el comando /del"""
    if event.chat_id != CONTROL_CHANNEL_ID:
        return

    try:
        args = event.text.split()
        if len(args) != 2:
            await event.respond(msg.CMD["DEL"]["USAGE"])
            return

        channel_id = int(args[1])

        # Primero verificar si el canal existe
        cursor.execute("SELECT name FROM channels WHERE channel_id = ?", (channel_id,))
        result = cursor.fetchone()

        if not result:
            await event.respond(msg.CMD["DEL"]["NOT_FOUND"])
            return

        channel_name = result[0]
        deleted_files = 0
        deleted_dirs = set()

        try:
            # Obtener todos los archivos del canal
            cursor.execute("SELECT file_path FROM files WHERE channel_id = ?", (channel_id,))
            files = cursor.fetchall()

            # Eliminar archivos STRM
            for (file_path,) in files:
                try:
                    path = Path(file_path)
                    if path.exists():
                        path.unlink()
                        deleted_files += 1

                        # Añadir el directorio padre para revisión posterior
                        parent = path.parent
                        while parent not in [MOVIES_FOLDER, SERIES_FOLDER]:
                            deleted_dirs.add(parent)
                            if parent.parent in [MOVIES_FOLDER, SERIES_FOLDER]:
                                break
                            parent = parent.parent

                except Exception as e:
                    logger.error(f"Error eliminando archivo {file_path}: {str(e)}")

            # Eliminar directorios vacíos
            for directory in sorted(deleted_dirs, key=lambda x: len(str(x).split('/')), reverse=True):
                try:
                    if directory.exists() and not any(directory.iterdir()):
                        directory.rmdir()
                except Exception as e:
                    logger.error(f"Error eliminando directorio {directory}: {str(e)}")

            # Eliminar registros de la base de datos
            cursor.execute("DELETE FROM files WHERE channel_id = ?", (channel_id,))
            cursor.execute("DELETE FROM indexing_status WHERE channel_id = ?", (channel_id,))
            cursor.execute("DELETE FROM unrecognized_files WHERE channel_id = ?", (channel_id,))
            cursor.execute("DELETE FROM channels WHERE channel_id = ?", (channel_id,))

            # Commit de los cambios en la base de datos
            conn.commit()

            # Preparar mensaje de respuesta
            response = msg.CMD["DEL"]["SUCCESS"].format(
                name=channel_name,
                id=channel_id,
                deleted_files=deleted_files
            )

            if deleted_dirs:
                response += msg.CMD["DEL"]["DIRS_REMOVED"].format(
                    dirs=", ".join(d.name for d in deleted_dirs if not d.exists())
                )

            await event.respond(response)

        except Exception as e:
            logger.error(f"Error procesando eliminación: {str(e)}")
            raise

    except ValueError:
        await event.respond(msg.CMD["DEL"]["INVALID_ID"])
    except Exception as e:
        logger.error(f"Error en del_channel_handler: {str(e)}")
        await event.respond(msg.CMD["DEL"]["ERROR"].format(error=str(e)))

async def check_new_messages():
    """Revisa periódicamente nuevos mensajes en los canales."""
    while True:
        try:
            cursor.execute("SELECT channel_id, last_processed_id FROM channels")
            channels = cursor.fetchall()

            for channel_id, last_processed_id in channels:
                try:
                    await rate_limiter.acquire()
                    messages = await client.get_messages(channel_id, limit=1)
                    if not messages:
                        continue

                    latest_message_id = messages[0].id
                    if latest_message_id > last_processed_id:
                        # Hay mensajes nuevos
                        new_messages = []
                        async for message in client.iter_messages(
                            channel_id,
                            min_id=last_processed_id,
                            max_id=latest_message_id
                        ):
                            new_messages.append(message)
                            if len(new_messages) >= 100:  # Procesar en lotes
                                for msg in new_messages:
                                    await process_media_message(msg, channel_id)
                                new_messages = []

                        # Procesar mensajes restantes
                        for msg in new_messages:
                            await process_media_message(msg, channel_id)

                        # Actualizar último mensaje procesado
                        cursor.execute("""
                            UPDATE channels
                            SET last_processed_id = ?
                            WHERE channel_id = ?
                        """, (latest_message_id, channel_id))
                        conn.commit()

                except Exception as e:
                    logger.error(f"Error checking channel {channel_id}: {str(e)}")

        except Exception as e:
            logger.error(f"Error in check_new_messages: {str(e)}")

        await asyncio.sleep(300)  # Revisar cada 5 minutos

async def import_subscribed_channels(event):
    """Handler para el comando /import"""
    if event.chat_id != CONTROL_CHANNEL_ID:
        return

    try:
        await event.respond(msg.CMD["IMPORT"]["START"])

        added = 0
        skipped = 0
        total = 0

        async for dialog in client.iter_dialogs():
            await rate_limiter.acquire()

            if dialog.is_channel:
                total += 1
                try:
                    cursor.execute("""
                        INSERT OR IGNORE INTO channels (channel_id, name)
                        VALUES (?, ?)
                    """, (dialog.id, dialog.title))

                    if cursor.rowcount > 0:
                        added += 1
                        asyncio.create_task(index_channel(dialog.id))
                    else:
                        skipped += 1

                except Exception as e:
                    logger.error(msg.LOG_ERROR.format(error=str(e)))

        conn.commit()

        response = msg.CMD["IMPORT"]["COMPLETE"].format(
            added=added,
            skipped=skipped,
            total=total
        )

        if added > 0:
            response += msg.CMD["IMPORT"]["INDEXING"]

        await event.respond(response)

    except Exception as e:
        logger.error(msg.LOG_ERROR.format(error=str(e)))
        await event.respond(msg.CMD["IMPORT"]["ERROR"].format(error=str(e)))

def register_handlers():
    """Registra los handlers de comandos."""
    client.add_event_handler(add_channel_handler, events.NewMessage(pattern='/add'))
    client.add_event_handler(edit_list_handler, events.NewMessage(pattern=r'/edit list( \w+)?'))
    client.add_event_handler(edit_handler, events.NewMessage(pattern='/edit'))
    client.add_event_handler(skip_handler, events.NewMessage(pattern='/skip'))
    client.add_event_handler(status_handler, events.NewMessage(pattern='/status'))
    client.add_event_handler(import_subscribed_channels, events.NewMessage(pattern='/import'))
    client.add_event_handler(del_channel_handler, events.NewMessage(pattern='/del'))


async def process_media_message(message, channel_id):
    """Procesa un mensaje de media y crea los archivos STRM correspondientes."""
    if not message.file or not message.file.mime_type.startswith('video/'):
        return False

    try:
        filename = message.file.name or f"video_{message.id}.mp4"
        media_info = guessit(filename)
        if not media_info and message.message:
            media_info = guessit(message.message)

        if not media_info or 'type' not in media_info:
            cursor.execute("""
                INSERT OR REPLACE INTO unrecognized_files
                (message_id, channel_id, filename, attempts)
                VALUES (?, ?, ?, COALESCE(
                    (SELECT attempts + 1 FROM unrecognized_files
                     WHERE message_id = ? AND channel_id = ?),
                    1
                ))
            """, (str(message.id), channel_id, filename, str(message.id), channel_id))
            conn.commit()
            logger.info(msg.LOG_FILE_NOT_RECOGNIZED.format(filename=filename, id=message.id))
            return False

        if media_info['type'] == 'movie':
            base_folder = MOVIES_FOLDER
            folder = base_folder / f"{media_info['title']} ({media_info.get('year', 'XXXX')})"
            file_name = f"{media_info['title']} ({media_info.get('year', 'XXXX')}){Path(filename).suffix}"
        else:
            base_folder = SERIES_FOLDER
            folder = base_folder / media_info['title'] / f"Season {media_info.get('season', 1)}"
            file_name = f"{media_info['title']} - S{media_info.get('season', 1):02d}E{media_info.get('episode', 1):02d}{Path(filename).suffix}"

        folder.mkdir(parents=True, exist_ok=True)
        strm_path = folder / f"{Path(file_name).stem}.strm"

        with strm_path.open('w') as f:
            f.write(f"{BASE_URL}/{channel_id}/{message.id}")

        cursor.execute("""
            INSERT OR REPLACE INTO files (file_id, channel_id, file_path, type)
            VALUES (?, ?, ?, ?)
        """, (str(message.id), channel_id, str(strm_path), media_info['type']))
        conn.commit()

        logger.info(msg.LOG_FILE_PROCESSED.format(path=strm_path))
        return True

    except Exception as e:
        logger.error(msg.LOG_PROCESSING_ERROR.format(filename=filename, error=str(e)))
        return False

async def handle_proxy_request(request):
    """Maneja las solicitudes de streaming."""
    channel_id = int(request.match_info['channel_id'])
    file_id = int(request.match_info['file_id'])

    try:
        await rate_limiter.acquire()
        message = await client.get_messages(channel_id, ids=file_id)
        if not message or not message.file:
            return web.Response(status=404, text=msg.HTTP_FILE_NOT_FOUND)

        file_size = message.file.size
        range_header = request.headers.get('Range')

        # Procesar header de rango si existe
        start = 0
        end = file_size - 1

        if range_header:
            try:
                range_match = re.match(r'bytes=(\d+)-(\d*)', range_header)
                if range_match:
                    start = int(range_match.group(1))
                    end = int(range_match.group(2)) if range_match.group(2) else file_size - 1
            except:
                pass

        # Configurar headers básicos
        headers = {
            'Content-Type': message.file.mime_type or 'video/mp4',
            'Accept-Ranges': 'bytes',
            'Content-Range': f'bytes {start}-{end}/{file_size}',
            'Content-Length': str(end - start + 1)
        }

        # Preparar respuesta
        response = web.StreamResponse(
            status=206 if range_header else 200,
            headers=headers
        )
        await response.prepare(request)

        try:
            async for chunk in client.iter_download(
                message.media,
                offset=start,
                limit=end - start + 1
            ):
                await response.write(chunk)

            await response.write_eof()
            return response

        except Exception as e:
            logger.error(f"Error durante streaming: {str(e)}")
            if not response.prepared:
                return web.Response(status=500, text=str(e))
            return response

    except Exception as e:
        logger.error(msg.LOG_STREAMING_ERROR.format(id=file_id, error=str(e)))
        return web.Response(status=500, text=msg.HTTP_SERVER_ERROR.format(error=str(e)))

async def index_channel(channel_id):
    """Indexa un canal mensaje por mensaje."""
    try:
        # Primera llamada a la API - obtener entidad del canal
        await rate_limiter.acquire()
        channel = await client.get_entity(PeerChannel(channel_id))

        cursor.execute("SELECT last_processed_id FROM channels WHERE channel_id = ?", (channel_id,))
        result = cursor.fetchone()
        last_processed_id = result[0] if result else 0

        # Segunda llamada a la API - obtener el último mensaje
        await rate_limiter.acquire()
        messages = await client.get_messages(channel, limit=1)
        if not messages:
            raise Exception(msg.LOG_NO_MESSAGES)

        total_messages = messages[0].id
        logger.info(f"Total de mensajes a procesar: {total_messages}")

        cursor.execute("""
            INSERT OR REPLACE INTO indexing_status
            (channel_id, total_messages, processed_messages, status, last_update)
            VALUES (?, ?, ?, 'indexando', datetime('now'))
        """, (channel_id, total_messages, last_processed_id))
        conn.commit()

        processed = last_processed_id
        messages_in_batch = 0

        try:
            # Usar min_id para asegurar que obtenemos todos los mensajes desde el último procesado
            async for message in client.iter_messages(
                channel,
                reverse=True,     # Para procesar desde los más antiguos
                min_id=last_processed_id,  # Comenzar desde el último mensaje procesado
                wait_time=1       # Pequeña espera entre lotes internos
            ):
                try:
                    messages_in_batch += 1

                    if await process_media_message(message, channel_id):
                        logger.info(f"Procesado mensaje {message.id} del canal {channel_id}")

                    processed = message.id

                    # Actualizar progreso cada 10 mensajes
                    if messages_in_batch % 10 == 0:
                        cursor.execute("""
                            UPDATE indexing_status
                            SET processed_messages = ?, last_update = datetime('now')
                            WHERE channel_id = ?
                        """, (processed, channel_id))

                        cursor.execute("""
                            UPDATE channels
                            SET last_processed_id = ?
                            WHERE channel_id = ?
                        """, (processed, channel_id))
                        conn.commit()

                    # Aplicar rate limit cada BATCH_SIZE mensajes procesados
                    if messages_in_batch % BATCH_SIZE == 0:
                        await rate_limiter.acquire()
                        logger.info(f"Procesados {messages_in_batch} mensajes. Rate limit aplicado en mensaje {message.id}")

                except FloodWaitError as e:
                    logger.warning(f"FloodWaitError: esperando {e.seconds} segundos")
                    await asyncio.sleep(e.seconds)
                except Exception as e:
                    logger.error(f"Error procesando mensaje {message.id}: {str(e)}")
                    continue

        except Exception as e:
            logger.error(f"Error en iter_messages: {str(e)}")

        # Actualización final
        cursor.execute("""
            UPDATE indexing_status
            SET status = 'completo', last_update = datetime('now')
            WHERE channel_id = ?
        """, (channel_id,))

        cursor.execute("""
            UPDATE channels
            SET last_processed_id = ?
            WHERE channel_id = ?
        """, (processed, channel_id))
        conn.commit()

        logger.info(f"Indexación completada para el canal {channel_id}. Total mensajes procesados: {messages_in_batch}")

    except Exception as e:
        logger.error(msg.LOG_INDEXING_ERROR.format(channel=channel_id, error=str(e)))
        cursor.execute("""
            UPDATE indexing_status
            SET status = ?, last_update = datetime('now')
            WHERE channel_id = ?
        """, (f"error: {str(e)}", channel_id))
        # conn.commit()

async def main():
    """Función principal."""
    try:
        connection_manager = ConnectionManager(client, media_library)
        await authenticate()

        app = web.Application()
        app.router.add_get('/{channel_id}/{file_id}', handle_proxy_request)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, 'localhost', PROXY_PORT)
        await site.start()
        logger.info(msg.LOG_SERVER_START.format(port=PROXY_PORT))

        asyncio.create_task(connection_manager.start())
        asyncio.create_task(check_new_messages())


        cursor.execute("SELECT channel_id FROM channels")
        channels = cursor.fetchall()
        for (channel_id,) in channels:
            asyncio.create_task(index_channel(channel_id))

        await client.run_until_disconnected()

    except Exception as e:
        logger.error(msg.LOG_FATAL_ERROR.format(error=str(e)))
    finally:
        conn.close()

if __name__ == '__main__':
    try:
        init_globals()
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info(msg.LOG_PROGRAM_END)
    except Exception as e:
        logger.error(msg.LOG_FATAL_ERROR.format(error=str(e)))
    finally:
        if conn:
            conn.close()
