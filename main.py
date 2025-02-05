#!/usr/bin/env python3
import os
import time
import asyncio
from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError, MessageIdInvalidError
from aiohttp import web
import sqlite3
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from guessit import guessit
from dotenv import load_dotenv
import re
from datetime import datetime
import datetime


def setup_logging():
    log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    date_format = '%Y-%m-%d %H:%M:%S'

    log_dir = 'logs'
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    file_handler = RotatingFileHandler(
        f'{log_dir}/robingood.log',
        maxBytes=10*1024*1024,
        backupCount=5
    )
    file_handler.setFormatter(logging.Formatter(log_format, date_format))
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter(log_format, date_format))
    logger.addHandler(console_handler)

    return logger

logger = setup_logging()
load_dotenv()

# Configuración
API_ID = os.getenv('API_ID')
API_HASH = os.getenv('API_HASH')
PHONE_NUMBER = os.getenv('PHONE_NUMBER')
SERVER_HOST = os.getenv('SERVER_HOST', 'localhost')
SERVER_PORT = int(os.getenv('SERVER_PORT', 8080))
TELEGRAM_REQUESTS_PER_MINUTE = int(os.getenv('TELEGRAM_REQUESTS_PER_MINUTE', 20))
CONTROL_CHANNEL = int(os.getenv('CONTROL_CHANNEL'))

class RateLimiter:
    def __init__(self, requests_per_minute):
        self.requests_per_minute = requests_per_minute
        self.interval = 60 / requests_per_minute
        self.last_requests = {}

    async def can_request(self, key):
        current_time = time.time()
        if key in self.last_requests:
            time_passed = current_time - self.last_requests[key]
            if time_passed < self.interval:
                await asyncio.sleep(self.interval - time_passed)

        self.last_requests[key] = current_time
        return True

routes = web.RouteTableDef()
client = TelegramClient('robingood', API_ID, API_HASH)
DB_PATH = os.getenv('DB_PATH', 'media_files.db')
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()
# Crear tablas
cursor.execute('''
CREATE TABLE IF NOT EXISTS channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id INTEGER NOT NULL UNIQUE,
    channel_name TEXT,
    total_messages INTEGER,
    indexed_messages INTEGER DEFAULT 0,
    last_scan DATETIME,
    status TEXT DEFAULT 'pending'
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    title TEXT NOT NULL,
    year INTEGER,
    season INTEGER,
    episode INTEGER,
    type TEXT NOT NULL,
    mime_type TEXT,
    file_size INTEGER,
    UNIQUE(file_id, channel)
)
''')
conn.commit()

async def update_stream_urls(cursor, base_url="http://127.0.0.1:8080"):
    try:
        cursor.execute("""
            UPDATE files
            SET stream_url = CASE
                WHEN type = 'movie' THEN
                    CASE
                        WHEN year IS NOT NULL THEN
                            printf('%s/Movies/%s (%s).%s',
                                ?,
                                title,
                                year,
                                CASE WHEN mime_type LIKE '%matroska%' THEN 'mkv' ELSE 'mp4' END
                            )
                        ELSE
                            printf('%s/Movies/%s.%s',
                                ?,
                                title,
                                CASE WHEN mime_type LIKE '%matroska%' THEN 'mkv' ELSE 'mp4' END
                            )
                    END
                WHEN type = 'episode' THEN
                    printf('%s/Series/%s/Season %02d/S%02dE%02d.%s',
                        ?,
                        title,
                        season,
                        season,
                        episode,
                        CASE WHEN mime_type LIKE '%matroska%' THEN 'mkv' ELSE 'mp4' END
                    )
            END
            WHERE stream_url IS NULL
        """, (base_url, base_url, base_url))
        cursor.connection.commit()
        logger.info("URLs de streaming actualizadas correctamente")
    except Exception as e:
        logger.error(f"Error actualizando URLs de streaming: {str(e)}")
        cursor.connection.rollback()

# Crear tablas
cursor.execute('''
CREATE TABLE IF NOT EXISTS channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id INTEGER NOT NULL UNIQUE,
    channel_name TEXT,
    total_messages INTEGER,
    indexed_messages INTEGER DEFAULT 0,
    last_scan DATETIME,
    status TEXT DEFAULT 'pending'
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    title TEXT NOT NULL,
    year INTEGER,
    season INTEGER,
    episode INTEGER,
    type TEXT NOT NULL,
    mime_type TEXT,
    file_size INTEGER,
    UNIQUE(file_id, channel)
)
''')
conn.commit()

# Añadir columna stream_url si no existe
try:
    cursor.execute("ALTER TABLE files ADD COLUMN stream_url TEXT")
    conn.commit()
except sqlite3.OperationalError:
    # La columna ya existe
    pass

# Actualizar URLs
loop = asyncio.get_event_loop()
loop.run_until_complete(update_stream_urls(cursor))

def parse_media_info(filename, message_text=''):
    try:
        guess = guessit(filename)
        if message_text:
            message_guess = guessit(message_text)
            for key, value in message_guess.items():
                if key not in guess:
                    guess[key] = value
        return guess
    except Exception as e:
        logger.error(f"Error parseando información del medio: {str(e)}")
        return {}

class ChannelIndexer:
    def __init__(self, channel_id, rate_limiter):
        self.channel_id = channel_id
        self.rate_limiter = rate_limiter
        self.initial_scan_complete = False
        self.last_message_id = None
        logger.info(f"Inicializado indexador para canal: {channel_id}")

    async def initial_scan(self, client, cursor):
        try:
            logger.info(f"Iniciando escaneo inicial del canal {self.channel_id}")
            channel = await client.get_entity(self.channel_id)
            cursor.execute("""
                INSERT OR REPLACE INTO channels
                (channel_id, channel_name, total_messages, status)
                VALUES (?, ?, ?, ?)
            """, (self.channel_id, channel.title, 0, 'scanning'))
            cursor.connection.commit()

            async for message in client.iter_messages(self.channel_id):
                await self.rate_limiter
                if message.media:
                    await self.process_message(client, cursor, message)
                self.last_message_id = message.id

            self.initial_scan_complete = True
            cursor.execute("""
                UPDATE channels
                SET status = 'completed', last_scan = CURRENT_TIMESTAMP
                WHERE channel_id = ?
            """, (self.channel_id,))
            cursor.connection.commit()
            logger.info(f"Escaneo inicial completado para canal {self.channel_id}")
        except Exception as e:
            logger.error(f"Error en escaneo inicial del canal {self.channel_id}: {str(e)}")
            cursor.execute("UPDATE channels SET status = 'error' WHERE channel_id = ?",
                         (self.channel_id,))
            cursor.connection.commit()
            raise

    async def process_message(self, client, cursor, message):
        if not message.media:
            return
        try:
            file_info = {
                'file_id': message.id,
                'channel': str(self.channel_id),
                'title': message.message or 'Unknown',
                'type': 'unknown',
                'mime_type': getattr(message.media, 'mime_type', None),
                'file_size': getattr(message.media, 'size', None)
            }

            if file_info['mime_type'] and 'video' in file_info['mime_type'].lower():
                title, year, season, episode = self.parse_filename(file_info['title'])
                file_info['title'] = title
                file_info['year'] = year
                file_info['season'] = season
                file_info['episode'] = episode
                file_info['type'] = 'episode' if season and episode else 'movie'

            cursor.execute("""
                INSERT OR REPLACE INTO files
                (file_id, channel, title, year, season, episode, type, mime_type, file_size)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                file_info['file_id'],
                file_info['channel'],
                file_info['title'],
                file_info.get('year'),
                file_info.get('season'),
                file_info.get('episode'),
                file_info['type'],
                file_info['mime_type'],
                file_info['file_size']
            ))
            cursor.connection.commit()
        except Exception as e:
            logger.error(f"Error procesando mensaje {message.id}: {str(e)}")
            raise

    def parse_filename(self, filename):
        title = filename
        year = None
        season = None
        episode = None

        year_match = re.search(r'\((\d{4})\)', filename)
        if year_match:
            year = int(year_match.group(1))
            title = filename.replace(year_match.group(0), '').strip()

        episode_match = re.search(r'S(\d{1,2})E(\d{1,2})', filename, re.IGNORECASE)
        if episode_match:
            season = int(episode_match.group(1))
            episode = int(episode_match.group(2))

        return title, year, season, episode

    async def initial_scan(self):
        if self.initial_scan_complete:
            return

        logger.info(f"Iniciando escaneo inicial del canal {self.channel_id}")
        try:
            if not await self.rate_limiter.can_request(f"initial_scan_{self.channel_id}"):
                logger.warning(f"Rate limit alcanzado para canal {self.channel_id}")
                return

            messages = await client.get_messages(self.channel_id, limit=1)
            if not messages:
                logger.warning(f"No se encontraron mensajes en el canal {self.channel_id}")
                return

            self.last_message_id = messages[0].id
            logger.info(f"Último mensaje en canal {self.channel_id}: {self.last_message_id}")

            cursor.execute("""
                UPDATE channels
                SET status = 'scanning',
                    total_messages = ?,
                    indexed_messages = 0,
                    last_scan = CURRENT_TIMESTAMP
                WHERE channel_id = ?
            """, (self.last_message_id, self.channel_id))
            conn.commit()

            batch_size = 50
            current_id = 0

            while current_id < self.last_message_id:
                if not await self.rate_limiter.can_request(f"batch_scan_{self.channel_id}"):
                    await asyncio.sleep(1)
                    continue

                messages = await client.get_messages(
                    self.channel_id,
                    limit=batch_size,
                    offset_id=current_id
                )

                if not messages:
                    break

                for message in messages:
                    if message and message.id:
                        await self.process_message(message)
                        current_id = message.id

                logger.info(f"Progreso escaneo canal {self.channel_id}: {current_id}/{self.last_message_id} ({(current_id/self.last_message_id*100):.1f}%)")

            cursor.execute("""
                UPDATE channels
                SET status = 'completed',
                    last_scan = CURRENT_TIMESTAMP
                WHERE channel_id = ?
            """, (self.channel_id,))
            conn.commit()

            self.initial_scan_complete = True
            logger.info(f"Escaneo inicial completado para canal {self.channel_id}")

        except Exception as e:
            logger.error(f"Error en escaneo inicial del canal {self.channel_id}: {str(e)}")
            cursor.execute("""
                UPDATE channels
                SET status = 'error',
                    last_scan = CURRENT_TIMESTAMP
                WHERE channel_id = ?
            """, (self.channel_id,))
            conn.commit()

    async def setup_new_message_handler(self):
        @client.on(events.NewMessage(chats=[self.channel_id]))
        async def handler(event):
            logger.info(f"Nuevo mensaje detectado en canal {self.channel_id}")
            await self.process_message(event.message)
        return handler

    async def setup_new_message_handler(self):  # Cambiado a async def
        @client.on(events.NewMessage(chats=[self.channel_id]))
        async def handler(event):
            logger.info(f"Nuevo mensaje detectado en canal {self.channel_id}")
            await self.process_message(event.message)
        return handler  # Devolvemos el handler para poder desregistrarlo si es necesario


@routes.get('/{path:.*}')
async def handle_media(request):
    path = request.match_info['path']
    current_time = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    if not path:
        # Página principal con estructura de directorios
        content = [
            '<html>',
            '<head>',
            '<title>Index of /</title>',
            '<style>',
            'body { font-family: monospace; }',
            'pre { margin: 0; }',
            '.directory { color: #0000ee; }',
            '</style>',
            '</head>',
            '<body>',
            '<h1>Index of /</h1>',
            '<hr>',
            '<pre>',
            '<a href="../" class="directory">../</a>',
            '<a href="Movies/" class="directory">Movies/</a>                                             -                 -',
            '<a href="Series/" class="directory">Series/</a>                                             -                 -',
            '</pre>',
            '<hr>',
            '</body>',
            '</html>'
        ]
        return web.Response(text='\n'.join(content), content_type='text/html')

    path_parts = path.rstrip('/').split('/')

    if path_parts[0] == 'Movies':
        if len(path_parts) == 1:
            cursor.execute("""
                SELECT id, title, year, file_size, mime_type
                FROM files
                WHERE type = 'movie'
                ORDER BY title
            """)
            movies = cursor.fetchall()
            content = [
                '<html>',
                '<head>',
                '<title>Index of /Movies</title>',
                '<style>',
                'body { font-family: monospace; }',
                'pre { margin: 0; }',
                '</style>',
                '</head>',
                '<body>',
                '<h1>Index of /Movies</h1>',
                '<hr>',
                '<pre>'
            ]
            content.append('<a href="../">../</a>')
            for movie in movies:
                movie_title = f"{movie[1]} ({movie[2]})" if movie[2] else movie[1]
                safe_title = re.sub(r'[<>:"/\\|?*]', '', movie_title)
                size = f"{movie[3]/1024/1024:.1f}M" if movie[3] else "-"
                ext = '.mkv' if movie[4] and 'matroska' in movie[4].lower() else '.mp4'
                file_name = f"{safe_title}{ext}"
                content.append(f'<a href="/Movies/{file_name}">{file_name}</a>{" "*(50-len(file_name))} {current_time}  {size}')
            content.extend(['</pre>', '<hr>', '</body>', '</html>'])
            return web.Response(text='\n'.join(content), content_type='text/html')

    elif path_parts[0] == 'Series':
        if len(path_parts) == 1:
            cursor.execute("""
                SELECT DISTINCT title
                FROM files
                WHERE type = 'episode'
                GROUP BY title
                ORDER BY title
            """)
            series = cursor.fetchall()
            content = [
                '<html>',
                '<head>',
                '<title>Index of /Series</title>',
                '<style>',
                'body { font-family: monospace; }',
                'pre { margin: 0; }',
                '.directory { color: #0000ee; }',
                '</style>',
                '</head>',
                '<body>',
                '<h1>Index of /Series</h1>',
                '<hr>',
                '<pre>'
            ]
            content.append('<a href="../" class="directory">../</a>')
            for serie in series:
                safe_title = re.sub(r'[<>:"/\\|?*]', '', serie[0])
                content.append(f'<a href="{safe_title}/" class="directory">{serie[0]}/</a>{" "*(50-len(serie[0]))} {current_time}  -')
            content.extend(['</pre>', '<hr>', '</body>', '</html>'])
            return web.Response(text='\n'.join(content), content_type='text/html')

        elif len(path_parts) == 2:
            serie_title = path_parts[1]
            cursor.execute("""
                SELECT DISTINCT season
                FROM files
                WHERE type = 'episode' AND title = ?
                GROUP BY season
                ORDER BY season
            """, (serie_title,))
            seasons = cursor.fetchall()
            content = [
                '<html>',
                '<head>',
                f'<title>Index of /Series/{serie_title}</title>',
                '<style>',
                'body { font-family: monospace; }',
                'pre { margin: 0; }',
                '.directory { color: #0000ee; }',
                '</style>',
                '</head>',
                '<body>',
                f'<h1>Index of /Series/{serie_title}</h1>',
                '<hr>',
                '<pre>'
            ]
            content.append('<a href="../" class="directory">../</a>')
            for season in seasons:
                season_dir = f"Season {season[0]:02d}"
                content.append(f'<a href="{season_dir}/" class="directory">{season_dir}/</a>{" "*(50-len(season_dir))} {current_time}  -')
            content.extend(['</pre>', '<hr>', '</body>', '</html>'])
            return web.Response(text='\n'.join(content), content_type='text/html')

        elif len(path_parts) == 3 and path_parts[2].startswith('Season '):
            serie_title = path_parts[1]
            season = int(path_parts[2].replace('Season ', ''))
            cursor.execute("""
                SELECT id, title, season, episode, year, file_size, mime_type
                FROM files
                WHERE type = 'episode' AND title = ? AND season = ?
                ORDER BY episode
            """, (serie_title, season))
            episodes = cursor.fetchall()
            content = [
                '<html>',
                '<head>',
                f'<title>Index of /Series/{serie_title}/{path_parts[2]}</title>',
                '<style>',
                'body { font-family: monospace; }',
                'pre { margin: 0; }',
                '</style>',
                '</head>',
                '<body>',
                f'<h1>Index of /Series/{serie_title}/{path_parts[2]}</h1>',
                '<hr>',
                '<pre>'
            ]
            content.append('<a href="../">../</a>')
            for ep in episodes:
                ep_title = f"{ep[1]} S{ep[2]:02d}E{ep[3]:02d}"
                if ep[4]:  # año
                    ep_title += f" ({ep[4]})"
                size = f"{ep[5]/1024/1024:.1f}M" if ep[5] else "-"
                ext = '.mkv' if ep[6] and 'matroska' in ep[6].lower() else '.mp4'
                file_name = f"{ep_title}{ext}"
                # Usar la ruta completa para el archivo
                content.append(f'<a href="/Series/{serie_title}/Season {season:02d}/{file_name}">{file_name}</a>{" "*(50-len(file_name))} {current_time}  {size}')
            content.extend(['</pre>', '<hr>', '</body>', '</html>'])
            return web.Response(text='\n'.join(content), content_type='text/html')

    # Manejo de archivos
    if len(path_parts) >= 3 and path_parts[0] == 'Series':
        serie_title = path_parts[1]
        season_match = re.search(r'Season (\d+)', path_parts[2])
        if season_match:
            season_num = int(season_match.group(1))
            filename = path_parts[3]

            cursor.execute("""
                SELECT id
                FROM files
                WHERE type = 'episode'
                AND title = ?
                AND season = ?
            """, (serie_title, season_num))

            result = cursor.fetchone()
            if result:
                return await stream_video(request, result[0])

    elif len(path_parts) == 2 and path_parts[0] == 'Movies':
        filename = path_parts[1]
        title_match = re.match(r'(.+?)(?:\s*\((\d{4})\))?\.(mkv|mp4)$', filename)

        if title_match:
            title = title_match.group(1)
            year = title_match.group(2)

            if year:
                cursor.execute("""
                    SELECT id
                    FROM files
                    WHERE type = 'movie'
                    AND title = ?
                    AND year = ?
                """, (title, int(year)))
            else:
                cursor.execute("""
                    SELECT id
                    FROM files
                    WHERE type = 'movie'
                    AND title = ?
                """, (title,))

            result = cursor.fetchone()
            if result:
                return await stream_video(request, result[0])

    return web.Response(status=404, text='<html><head><title>404 Not Found</title></head><body><h1>404 Not Found</h1></body></html>',
                       content_type='text/html')

@routes.get('/stream/{file_id}/{filename}')
async def stream_video(request, file_id=None):
    if file_id is None:
        file_id = request.match_info['file_id']

    try:
        cursor.execute("""
            SELECT file_id, channel, title, type, mime_type, id
            FROM files
            WHERE id = ?
        """, (file_id,))

        file_info = cursor.fetchone()
        if not file_info:
            logger.error(f"Archivo no encontrado en DB: {file_id}")
            return web.Response(status=404, text="Archivo no encontrado en base de datos")

        message_id = int(file_info[0])
        channel_id = int(file_info[1])

        try:
            message = await client.get_messages(channel_id, ids=message_id)
            if not message or not message.media:
                logger.error(f"Archivo no encontrado en Telegram: Channel {channel_id}, Message {message_id}")
                return web.Response(status=404, text="Archivo no encontrado en Telegram")

            file_size = message.media.document.size
            mime_type = file_info[4] or 'video/mp4'

            range_header = request.headers.get('Range')

            if range_header:
                try:
                    range_match = re.match(r'bytes=(\d+)-(\d*)', range_header)
                    start = int(range_match.group(1))
                    end = int(range_match.group(2)) if range_match.group(2) else file_size - 1
                except:
                    start = 0
                    end = file_size - 1
            else:
                start = 0
                end = file_size - 1

            headers = {
                'Content-Type': mime_type,
                'Accept-Ranges': 'bytes',
                'Content-Range': f'bytes {start}-{end}/{file_size}',
                'Content-Length': str(end - start + 1)
            }

            status = 206 if range_header else 200

            response = web.StreamResponse(status=status, headers=headers)
            await response.prepare(request)

            try:
                async for chunk in client.iter_download(message.media.document, offset=start, limit=end-start+1):
                    await response.write(chunk)
                    await response.drain()
            except Exception as e:
                logger.error(f"Error durante el streaming del archivo {file_id}: {str(e)}")
                return web.Response(status=500, text="Error durante el streaming")

            await response.write_eof()
            return response

        except Exception as e:
            logger.error(f"Error al obtener mensaje de Telegram: {str(e)}")
            return web.Response(status=500, text="Error al obtener archivo de Telegram")

    except Exception as e:
        logger.error(f"Error general en stream_video: {str(e)}")
        return web.Response(status=500, text="Error interno del servidor")

async def authenticate():
    logger.info("Iniciando autenticación con Telegram")
    await client.connect()
    if not await client.is_user_authorized():
        logger.info("Usuario no autorizado, solicitando código")
        await client.send_code_request(PHONE_NUMBER)
        try:
            code = input("Introduce el código de Telegram: ")
            await client.sign_in(PHONE_NUMBER, code)
            logger.info("Autenticación completada exitosamente")
        except SessionPasswordNeededError:
            logger.info("Se requiere autenticación 2FA")
            password = input("Introduce tu contraseña de verificación en dos pasos: ")
            await client.sign_in(password=password)
            logger.info("Autenticación 2FA completada")
    else:
        logger.info("Usuario ya autenticado")

async def setup_control_channel(rate_limiter):  # Añadimos rate_limiter como parámetro
    @client.on(events.NewMessage(chats=[CONTROL_CHANNEL]))
    async def control_handler(event):
        if not event.message.text.startswith('/'):
            return

        command = event.message.text.split()[0].lower()
        args = event.message.text.split()[1:] if len(event.message.text.split()) > 1 else []

        try:
            if command == '/add':
                if not args:
                    await event.reply("Uso: /add <channel_id>")
                    return

                channel_id = int(args[0])
                indexer = ChannelIndexer(channel_id, rate_limiter)  # Usamos el rate_limiter pasado como parámetro
                info = await indexer.get_channel_info()

                if not info:
                    await event.reply(f"Error: No se pudo obtener información del canal {channel_id}")
                    return

                try:
                    cursor.execute("""
                        INSERT INTO channels (channel_id, channel_name, total_messages, status)
                        VALUES (?, ?, ?, 'pending')
                    """, (channel_id, info['channel_name'], info['total_messages']))
                    conn.commit()
                    await event.reply(f"Canal añadido: {info['channel_name']} ({channel_id})")
                    # Iniciar escaneo del nuevo canal
                    await indexer.initial_scan()
                except sqlite3.IntegrityError:
                    await event.reply(f"Error: El canal {channel_id} ya existe en la base de datos")

            elif command == '/del':
                if not args:
                    await event.reply("Uso: /del <channel_id>")
                    return

                channel_id = int(args[0])

                # Primero borramos los archivos asociados al canal
                cursor.execute("""
                    DELETE FROM files
                    WHERE channel = ?
                """, (str(channel_id),))

                # Luego borramos el canal
                cursor.execute("""
                    DELETE FROM channels
                    WHERE channel_id = ?
                """, (channel_id,))

                deleted_files = cursor.rowcount
                conn.commit()

                await event.reply(f"Canal {channel_id} eliminado. Se eliminaron {deleted_files} archivos asociados.")

            elif command == '/status':
                cursor.execute("SELECT * FROM channels")
                channels = cursor.fetchall()
                response = "Estado de los canales:\n\n"
                for channel in channels:
                    response += f"Canal: {channel[2]} ({channel[1]})\n"
                    response += f"Mensajes indexados: {channel[4]}/{channel[3]}\n"
                    response += f"Estado: {channel[6]}\n"
                    response += f"Último escaneo: {channel[5]}\n\n"
                await event.reply(response)

        except Exception as e:
            logger.error(f"Error en comando de control: {str(e)}")
            await event.reply(f"Error: {str(e)}")

async def main():
    try:
        await authenticate()

        app = web.Application()
        rate_limiter = RateLimiter(TELEGRAM_REQUESTS_PER_MINUTE)
        app['rate_limiter'] = rate_limiter

        # Pasamos el rate_limiter a setup_control_channel
        await setup_control_channel(rate_limiter)

        app.add_routes(routes)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, SERVER_HOST, SERVER_PORT)

        await site.start()
        logger.info(f"Servidor HTTP iniciado en http://{SERVER_HOST}:{SERVER_PORT}")

        try:
            while True:
                await asyncio.sleep(3600)
        except KeyboardInterrupt:
            logger.info("Señal de interrupción recibida, iniciando apagado...")
            await runner.cleanup()
            conn.close()
            await client.disconnect()
            logger.info("Servidor detenido y recursos liberados")

    except Exception as e:
        logger.error(f"Error fatal en main: {str(e)}")
        raise

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Programa terminado por el usuario")
    except Exception as e:
        logger.error(f"Error no manejado: {str(e)}")
        raise
