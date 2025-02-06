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
CHUNK_SIZE = 1024 * 1024  # 1MB

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

    args = event.text.split()
    if len(args) != 2:
        await event.respond(msg.CMD["ADD"]["USAGE"])
        return

    try:
        channel_id = int(args[1])
        await rate_limiter.acquire()
        channel = await client.get_entity(channel_id)

        if not channel.broadcast:
            await event.respond(msg.CMD["ADD"]["NOT_CHANNEL"])
            return

        cursor.execute("""
            INSERT OR IGNORE INTO channels (channel_id, name)
            VALUES (?, ?)
        """, (channel_id, channel.title))

        if cursor.rowcount > 0:
            conn.commit()
            await event.respond(msg.CMD["ADD"]["SUCCESS"].format(name=channel.title))
            asyncio.create_task(index_channel(channel_id))
        else:
            await event.respond(msg.CMD["ADD"]["EXISTS"].format(name=channel.title))

    except ValueError:
        await event.respond(msg.CMD["ADD"]["INVALID_ID"])
    except Exception as e:
        await event.respond(msg.CMD["ADD"]["ERROR"].format(error=str(e)))

# [Continúa con el resto de handlers...]

# Continuación de los handlers y funciones principales...

async def edit_list_handler(event):
    """Handler para el comando /edit list"""
    if event.chat_id != CONTROL_CHANNEL_ID:
        return

    try:
        cursor.execute("""
            SELECT message_id, channel_id, filename
            FROM unrecognized_files
            ORDER BY channel_id, filename
        """)
        files = cursor.fetchall()

        if not files:
            await event.respond(msg.CMD["EDIT_LIST"]["EMPTY"])
            return

        response = [msg.CMD["EDIT_LIST"]["HEADER"]]

        for message_id, channel_id, filename in files:
            response.append(f"`{message_id}` - {filename}")

            if len(response) > 50:  # Limitamos a 50 archivos por mensaje
                await event.respond("\n".join(response))
                response = [msg.CMD["EDIT_LIST"]["CONTINUE"]]

        if response:
            response.append(msg.CMD["EDIT_LIST"]["FOOTER"])
            await event.respond("\n".join(response))

    except Exception as e:
        logger.error(msg.LOG_ERROR.format(error=str(e)))
        await event.respond(f"{msg.ERROR_PREFIX}{str(e)}")

async def edit_handler(event):
    """Handler para el comando /edit"""
    if event.chat_id != CONTROL_CHANNEL_ID:
        return

    try:
        args = event.text.split(maxsplit=3)
        if len(args) < 4:
            await event.respond(msg.CMD["EDIT"]["USAGE"])
            return

        message_id = args[1]
        media_type = args[2].lower()
        new_name = args[3].strip('" ')  # Eliminamos comillas si existen

        if media_type not in ['movie', 'serie']:
            await event.respond(msg.CMD["EDIT"]["INVALID_TYPE"])
            return

        cursor.execute("""
            SELECT channel_id, filename
            FROM unrecognized_files
            WHERE message_id = ?
        """, (message_id,))

        result = cursor.fetchone()
        if not result:
            await event.respond(msg.CMD["EDIT"]["NOT_FOUND"])
            return

        channel_id, old_filename = result

        try:
            await rate_limiter.acquire()
            message = await client.get_messages(channel_id, ids=int(message_id))
            if not message or not message.file:
                await event.respond(msg.CMD["EDIT"]["NO_ACCESS"])
                return
        except Exception as e:
            await event.respond(msg.CMD["ADD"]["ERROR"].format(error=str(e)))
            return

        extension = Path(old_filename).suffix
        if media_type == 'movie':
            if not re.match(r'.+\(\d{4}\)$', new_name):
                await event.respond(msg.CMD["EDIT"]["INVALID_MOVIE"])
                return
            formatted_name = f"{new_name}{extension}"
        else:
            match = re.match(r'(.+?)\s*S(\d{1,2})E(\d{1,2})$', new_name)
            if not match:
                await event.respond(msg.CMD["EDIT"]["INVALID_SERIES"])
                return
            show_name, season, episode = match.groups()
            formatted_name = f"{show_name} - S{int(season):02d}E{int(episode):02d}{extension}"

        # En lugar de modificar message.file.name, creamos un nuevo objeto con la información necesaria
        media_info = guessit(formatted_name)
        if not media_info or 'type' not in media_info:
            await event.respond(msg.CMD["EDIT"]["INVALID_FORMAT"])
            return

        # Procesar el mensaje directamente con el nombre formateado
        if await process_media_message_with_name(message, channel_id, formatted_name):
            cursor.execute("""
                DELETE FROM unrecognized_files
                WHERE message_id = ? AND channel_id = ?
            """, (message_id, channel_id))
            conn.commit()
            await event.respond(msg.CMD["EDIT"]["SUCCESS"].format(
                old=old_filename,
                new=formatted_name
            ))
        else:
            await event.respond(msg.CMD["EDIT"]["ERROR"])

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

        # Obtener y eliminar archivos STRM
        cursor.execute("SELECT file_path FROM files WHERE channel_id = ?", (channel_id,))
        files = cursor.fetchall()

        deleted_count = 0
        directories_to_check = set()

        for (file_path,) in files:
            try:
                path = Path(file_path)
                if path.exists():
                    path.unlink()
                    deleted_count += 1

                    # Almacenar directorios para verificar después
                    current_dir = path.parent
                    while current_dir in [MOVIES_FOLDER, SERIES_FOLDER] or \
                          current_dir.parent in [MOVIES_FOLDER, SERIES_FOLDER]:
                        directories_to_check.add(current_dir)
                        current_dir = current_dir.parent

            except Exception as e:
                logger.error(f"Error eliminando archivo {file_path}: {str(e)}")

        # Eliminar directorios vacíos, comenzando desde los más profundos
        deleted_dirs = []
        for directory in sorted(directories_to_check, key=lambda x: len(str(x).split('/')), reverse=True):
            try:
                if directory.exists() and not any(directory.iterdir()):
                    directory.rmdir()
                    deleted_dirs.append(directory.name)
            except Exception as e:
                logger.error(f"Error eliminando directorio {directory}: {str(e)}")

        # Eliminar registros de la base de datos
        cursor.execute("DELETE FROM files WHERE channel_id = ?", (channel_id,))
        cursor.execute("DELETE FROM indexing_status WHERE channel_id = ?", (channel_id,))
        cursor.execute("DELETE FROM unrecognized_files WHERE channel_id = ?", (channel_id,))
        cursor.execute("DELETE FROM channels WHERE channel_id = ?", (channel_id,))

        conn.commit()

        response = msg.CMD["DEL"]["SUCCESS"].format(
            name=channel_name,
            id=channel_id,
            deleted_files=deleted_count
        )

        if deleted_dirs:
            response += msg.CMD["DEL"]["DIRS_REMOVED"].format(
                dirs=", ".join(deleted_dirs)
            )

        await event.respond(response)

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
    client.add_event_handler(edit_list_handler, events.NewMessage(pattern='/edit list'))
    client.add_event_handler(edit_handler, events.NewMessage(pattern='/edit'))
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

        # Obtener el nombre real del archivo desde la base de datos
        cursor.execute("""
            SELECT file_path FROM files
            WHERE file_id = ? AND channel_id = ?
        """, (str(file_id), channel_id))
        result = cursor.fetchone()

        if result:
            # Usar el nombre del archivo sin la extensión .strm
            display_name = Path(result[0]).stem
        else:
            # Fallback al nombre original del archivo
            display_name = message.file.name or f"video_{file_id}"

        # Preparar el nombre del archivo para los headers
        ascii_name = display_name.encode('ascii', 'ignore').decode()
        utf8_name = quote(display_name.encode('utf-8'))

        file_size = message.file.size
        range_header = request.headers.get('Range')

        # Procesar header de rango si existe
        start = 0
        end = file_size - 1

        if range_header:
            try:
                # Parsear el header de rango
                range_match = re.match(r'bytes=(\d*)-(\d*)', range_header)
                if range_match:
                    start = int(range_match.group(1)) if range_match.group(1) else 0
                    end = int(range_match.group(2)) if range_match.group(2) else file_size - 1

                if start >= file_size:
                    return web.Response(
                        status=416,
                        text='Requested range not satisfiable',
                        headers={
                            'Content-Range': f'bytes */{file_size}',
                            'Accept-Ranges': 'bytes',
                            'Content-Length': '0'
                        }
                    )

                # Ajustar el final si es necesario
                end = min(end, file_size - 1)
            except (ValueError, AttributeError) as e:
                logger.error(f"Error parsing range header: {str(e)}")
                start = 0
                end = file_size - 1

        # Configurar respuesta
        response = web.StreamResponse(status=206 if range_header else 200)
        response.headers.update({
            'Accept-Ranges': 'bytes',
            'Content-Type': message.file.mime_type or 'video/mp4',
            'Content-Disposition': f'inline; filename="{ascii_name}"; filename*=UTF-8\'\'{utf8_name}'
        })

        if range_header:
            response.headers['Content-Range'] = f'bytes {start}-{end}/{file_size}'
        response.headers['Content-Length'] = str(end - start + 1)

        await response.prepare(request)

        try:
            CHUNK_SIZE = 1024 * 1024  # 1MB chunks
            bytes_sent = 0
            total_to_send = end - start + 1

            async for chunk in client.iter_download(
                message.media,
                offset=start,
                limit=total_to_send,
                chunk_size=CHUNK_SIZE
            ):
                try:
                    # Verificar si el cliente sigue conectado
                    if not response._payload_writer or response._payload_writer.transport is None:
                        logger.info("Conexión perdida")
                        break

                    await response.write(chunk)
                    bytes_sent += len(chunk)

                    if bytes_sent >= total_to_send:
                        break

                except ConnectionResetError:
                    logger.info("Conexión reiniciada por el cliente")
                    break
                except RuntimeError as e:
                    if "Cannot write to closing transport" in str(e):
                        logger.info("Transporte cerrado durante la escritura")
                        break
                    raise

            # Verificar si podemos cerrar la respuesta correctamente
            try:
                if response._payload_writer and response._payload_writer.transport:
                    await response.write_eof()
            except (ConnectionResetError, RuntimeError):
                logger.info("Error al cerrar el stream - Cliente probablemente desconectado")

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
        await rate_limiter.acquire()
        channel = await client.get_entity(PeerChannel(channel_id))

        cursor.execute("SELECT last_processed_id FROM channels WHERE channel_id = ?", (channel_id,))
        result = cursor.fetchone()
        last_processed_id = result[0] if result else 0

        # Obtener el ID del último mensaje del canal
        await rate_limiter.acquire()
        messages = await client.get_messages(channel, limit=1)
        if not messages:
            raise Exception(msg.LOG_NO_MESSAGES)

        total_messages = messages[0].id

        cursor.execute("""
            INSERT OR REPLACE INTO indexing_status
            (channel_id, total_messages, processed_messages, status, last_update)
            VALUES (?, ?, ?, 'indexando', datetime('now'))
        """, (channel_id, total_messages, last_processed_id))
        conn.commit()

        current_id = last_processed_id + 1

        while current_id <= total_messages:
            try:
                # Obtener un solo mensaje
                await rate_limiter.acquire()
                message = await client.get_messages(channel, ids=current_id)

                if message:
                    try:
                        if await process_media_message(message, channel_id):
                            logger.info(f"Procesado mensaje {current_id} del canal {channel_id}")
                    except Exception as e:
                        logger.error(f"Error procesando mensaje {current_id}: {str(e)}")

                # Actualizar progreso
                cursor.execute("""
                    UPDATE indexing_status
                    SET processed_messages = ?, last_update = datetime('now')
                    WHERE channel_id = ?
                """, (current_id, channel_id))

                cursor.execute("""
                    UPDATE channels
                    SET last_processed_id = ?
                    WHERE channel_id = ?
                """, (current_id, channel_id))
                conn.commit()

            except FloodWaitError as e:
                logger.warning(f"FloodWaitError: esperando {e.seconds} segundos")
                await asyncio.sleep(e.seconds)
                continue
            except Exception as e:
                logger.error(f"Error con mensaje {current_id}: {str(e)}")

            current_id += 1

        cursor.execute("""
            UPDATE indexing_status
            SET status = 'completo', last_update = datetime('now')
            WHERE channel_id = ?
        """, (channel_id,))
        conn.commit()

        logger.info(f"Indexación completada para el canal {channel_id}")

    except Exception as e:
        logger.error(msg.LOG_INDEXING_ERROR.format(channel=channel_id, error=str(e)))
        cursor.execute("""
            UPDATE indexing_status
            SET status = ?, last_update = datetime('now')
            WHERE channel_id = ?
        """, (f"error: {str(e)}", channel_id))
        conn.commit()

        for offset in range(last_processed_id, total_messages, batch_size):
            await rate_limiter.acquire()
            messages = await client.get_messages(channel, limit=batch_size, add_offset=offset)

            for message in messages:
                if await process_media_message(message, channel_id):
                    processed += 1

                if processed % 10 == 0:
                    cursor.execute("""
                        UPDATE indexing_status
                        SET processed_messages = ?, last_update = datetime('now')
                        WHERE channel_id = ?
                    """, (processed, channel_id))
                    conn.commit()

        cursor.execute("""
            UPDATE indexing_status
            SET processed_messages = ?, status = 'completo', last_update = datetime('now')
            WHERE channel_id = ?
        """, (processed, channel_id))

        cursor.execute("""
            UPDATE channels
            SET last_processed_id = ?
            WHERE channel_id = ?
        """, (total_messages, channel_id))
        conn.commit()

    except Exception as e:
        logger.error(msg.LOG_INDEXING_ERROR.format(channel=channel_id, error=str(e)))
        cursor.execute("""
            UPDATE indexing_status
            SET status = ?, last_update = datetime('now')
            WHERE channel_id = ?
        """, (f"error: {str(e)}", channel_id))
        conn.commit()

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
