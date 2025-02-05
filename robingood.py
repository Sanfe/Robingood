#!/usr/bin/env python3
import os
import asyncio
import shutil
import zipfile
import py7zr
import rarfile
import subprocess
import signal
import json
from telethon import TelegramClient, events, types
from telethon.errors import SessionPasswordNeededError, RPCError

from dotenv import load_dotenv

# Cargar el archivo .env
load_dotenv()

# Configuración
API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
PHONE_NUMBER = os.getenv('PHONE_NUMBER')
SESSION_FILE = os.getenv('DOWNLOAD_SESSION_FILE', 'robingood')

# IDs de los canales
CHANNEL_ID_1 = (os.getenv('MOVIES_DOWNLOAD_CHANNEL_ID'))  # Canal de películas
CHANNEL_ID_2 = (os.getenv('SERIES_DOWNLOAD_CHANNEL_ID'))  # Canal de series
CONTROL_CHANNEL_ID = int(os.getenv('CONTROL_DOWNLOAD_CHANNEL_ID'))  # Canal de control

# Directorios para guardar y extraer archivos
SAVE_DIR_1 = os.getenv('MOVIES_DOWNLOAD_TEMP_FOLDER')
SAVE_DIR_2 = os.getenv('SERIES_DOWNLOAD_TEMP_FOLDER')
EXTRACT_DIR_1 = os.getenv('MOVIES_DOWNLOAD_FOLDER')
EXTRACT_DIR_2 = os.getenv('SERIES_DOWNLOAD_FOLDER')

# Tiempo de espera entre cada ciclo de revisión (en segundos)
WAIT_TIME = int(os.getenv('WAIT_TIME'))

# Configuración de TinyMediaManager
USE_TMM = os.getenv('USE_TMM', 'True').lower() == 'true'
TMM_CHANNEL_ID_1_COMMAND = "/home/deck/Applications/tinyMediaManager/./tinyMediaManager movie -u -n -r"
TMM_CHANNEL_ID_2_COMMAND = "/home/deck/Applications/tinyMediaManager/./tinyMediaManager tvshow -u -n -r"

# Mensajes configurables
MESSAGE_PROMPT_FOLDER = "Se detectó un grupo con ID `{grouped_id}`. ¿Quieres guardar los archivos en una carpeta nueva? Responde con `Y` o `N`."
MESSAGE_ENTER_FOLDER_NAME = "Por favor, introduce el nombre de la carpeta:"
MESSAGE_TIMEOUT_FOLDER = "No se recibió respuesta. Procediendo con la descarga y extracción."
MESSAGE_FOLDER_CREATED = "Carpeta `{folder_name}` creada exitosamente."
MESSAGE_PROCESS_COMPLETE = "Archivos del grupo `{grouped_id}` procesados y guardados en `{folder_path}`."
MESSAGE_NO_FILES_FOUND = "No se encontraron archivos en el grupo."

# Variables globales
is_running = False

def get_file_name(message):
    if message.document is None:
        return None
    for attr in message.document.attributes:
        if isinstance(attr, types.DocumentAttributeFilename):
            return attr.file_name
    return f"unknown_file_{message.id}"

def join_multipart_files(base_file_path):
    dir_path = os.path.dirname(base_file_path)
    file_name = os.path.basename(base_file_path)
    file_name_without_ext = os.path.splitext(file_name)[0]

    if file_name.endswith(('.7z.001', '.zip.001')):
        parts = sorted([f for f in os.listdir(dir_path) if f.startswith(file_name_without_ext)])
        output_file = os.path.join(dir_path, file_name_without_ext)

        with open(output_file, 'wb') as outfile:
            for part in parts:
                with open(os.path.join(dir_path, part), 'rb') as infile:
                    shutil.copyfileobj(infile, outfile)

        print(f"Partes unidas en: {output_file}")
        return output_file
    else:
        return base_file_path

async def extract_file(file_path, extract_dir):
    file_extension = os.path.splitext(file_path)[1].lower()

    try:
        if file_extension == '.zip':
            with zipfile.ZipFile(file_path, 'r') as zip_ref:
                zip_ref.extractall(extract_dir)
        elif file_extension == '.7z':
            with py7zr.SevenZipFile(file_path, mode='r') as z:
                z.extractall(extract_dir)
        elif file_extension == '.rar':
            with rarfile.RarFile(file_path) as rar_ref:
                rar_ref.extractall(extract_dir)
        else:
            raise ValueError(f"Formato de archivo no soportado para extracción: {file_extension}")

        print(f"Archivo extraído: {file_path}")
        os.remove(file_path)
        return True
    except Exception as e:
        print(f"Error al extraer {file_path}: {str(e)}")
        dest_path = os.path.join(extract_dir, os.path.basename(file_path))
        shutil.move(file_path, dest_path)
        print(f"Archivo movido debido a error: {dest_path}")
        return False

async def wait_for_response(client, chat_id, timeout=30):
    loop = asyncio.get_event_loop()
    future_response = loop.create_future()

    async def response_handler(event):
        if event.chat_id == chat_id and not future_response.done():
            future_response.set_result(event)

    client.add_event_handler(response_handler, events.NewMessage(chats=chat_id))

    try:
        return await asyncio.wait_for(future_response, timeout=timeout)
    except asyncio.TimeoutError:
        return None
    finally:
        client.remove_event_handler(response_handler)

async def process_grouped_files(client, message, save_dir, extract_dir):
    if message.grouped_id is None:
        return await process_single_file(client, message, save_dir, extract_dir)

    grouped_id = message.grouped_id
    max_amp = 10
    search_ids = list(range(message.id - max_amp, message.id + max_amp + 1))
    messages = await client.get_messages(message.chat_id, ids=search_ids)
    group_files = [msg for msg in messages if msg and msg.grouped_id == grouped_id and msg.document]

    if not group_files:
        await client.send_message(message.chat_id, MESSAGE_NO_FILES_FOUND)
        return message.id, False

    # Preguntar al usuario si desea crear una carpeta específica
    await client.send_message(message.chat_id, MESSAGE_PROMPT_FOLDER.format(grouped_id=grouped_id))

    # Esperar respuesta del usuario
    folder_name = None
    response_event = await wait_for_response(client, message.chat_id)
    if response_event and response_event.raw_text.strip().lower() == 'y':
        await client.send_message(message.chat_id, MESSAGE_ENTER_FOLDER_NAME)
        folder_name_event = await wait_for_response(client, message.chat_id)
        if folder_name_event:
            folder_name = folder_name_event.raw_text.strip()
            destination_folder = os.path.join(extract_dir, folder_name)
            os.makedirs(destination_folder, exist_ok=True)
            await client.send_message(message.chat_id, MESSAGE_FOLDER_CREATED.format(folder_name=folder_name))
        else:
            await client.send_message(message.chat_id, MESSAGE_TIMEOUT_FOLDER)
            destination_folder = extract_dir
    else:
        await client.send_message(message.chat_id, MESSAGE_TIMEOUT_FOLDER)
        destination_folder = extract_dir

    # Descargar y procesar los archivos del grupo
    file_paths = []
    is_archive = False
    archive_type = None
    rar_main_file = None

    for part in group_files:
        file_name = get_file_name(part)
        if file_name is None:
            continue
        file_path = os.path.join(save_dir, file_name)
        file_paths.append(file_path)
        print(f"Descargando parte: {file_name}")
        await client.download_media(part, file_path)

        if file_name.endswith('.7z.001') or file_name.endswith('.zip.001'):
            is_archive = True
            archive_type = '7z' if file_name.endswith('.7z.001') else 'zip'
        elif file_name.endswith('.part1.rar'):
            is_archive = True
            archive_type = 'rar'
            rar_main_file = file_path

    files_processed = False
    if is_archive:
        if archive_type in ['7z', 'zip']:
            joined_file_path = join_multipart_files(file_paths[0])
            files_processed = await extract_file(joined_file_path, destination_folder)
        elif archive_type == 'rar':
            files_processed = await extract_file(rar_main_file, destination_folder)
        for file_path in file_paths:
            if os.path.exists(file_path):
                os.remove(file_path)
    else:
        for file_path in file_paths:
            dest_path = os.path.join(destination_folder, os.path.basename(file_path))
            shutil.move(file_path, dest_path)
            print(f"Archivo movido: {dest_path}")
        files_processed = True

    await client.send_message(
        message.chat_id,
        MESSAGE_PROCESS_COMPLETE.format(grouped_id=grouped_id, folder_path=destination_folder)
    )

    # Borrar todos los mensajes del grupo después de procesarlos
    for part in group_files:
        await delete_message(client, part)

    return max(msg.id for msg in group_files), files_processed

async def process_single_file(client, message, save_dir, extract_dir):
    file_name = get_file_name(message)
    if file_name is None:
        return message.id, False
    file_path = os.path.join(save_dir, file_name)
    print(f"Descargando: {file_name}")
    await client.download_media(message, file_path)
    dest_path = os.path.join(extract_dir, file_name)
    shutil.move(file_path, dest_path)
    print(f"Archivo movido: {dest_path}")
    await delete_message(client, message)
    return message.id, True

async def delete_message(client, message):
    try:
        await client.delete_messages(message.chat_id, [message.id])
        print(f"Mensaje borrado del canal: {message.id}")
    except Exception as e:
        print(f"Error al borrar el mensaje {message.id}: {str(e)}")

def execute_tmm(command):
    try:
        result = subprocess.run(command, shell=True, check=True, capture_output=True, text=True)
        print(f"Comando TMM ejecutado: {command}\nSalida:\n{result.stdout}\nError:\n{result.stderr}")
    except subprocess.CalledProcessError as e:
        print(f"Error al ejecutar el comando TMM: {e}\nSalida:\n{e.output}\nError:\n{e.stderr}")

async def process_channel(client, channel_id, save_dir, extract_dir, last_message_id, tmm_command):
    try:
        channel = await client.get_entity(channel_id)
        print(f"Procesando canal: {channel.title}")
        files_processed = False
        async for message in client.iter_messages(channel, min_id=last_message_id):
            if not is_running:
                break
            new_last_id, processed = await process_grouped_files(client, message, save_dir, extract_dir)
            last_message_id = max(last_message_id, new_last_id)
            files_processed |= processed
        if files_processed and USE_TMM:
            execute_tmm(tmm_command)
        return last_message_id
    except Exception as e:
        print(f"Error al procesar el canal {channel_id}: {str(e)}")
        return last_message_id

async def main_loop(client):
    global is_running
    last_message_id_1 = 0
    last_message_id_2 = 0
    while is_running:
        try:
            print("Esperando mensajes...")
            last_message_id_1 = await process_channel(client, CHANNEL_ID_1, SAVE_DIR_1, EXTRACT_DIR_1, last_message_id_1, TMM_CHANNEL_ID_1_COMMAND)
            last_message_id_2 = await process_channel(client, CHANNEL_ID_2, SAVE_DIR_2, EXTRACT_DIR_2, last_message_id_2, TMM_CHANNEL_ID_2_COMMAND)
            print("Ciclo completado. Esperando...")
            await asyncio.sleep(WAIT_TIME)
        except Exception as e:
            print(f"Error en el ciclo principal: {str(e)}")
            await asyncio.sleep(WAIT_TIME)

async def main():
    client = TelegramClient(SESSION_FILE, API_ID, API_HASH)
    await client.start()

    if not await client.is_user_authorized():
        await client.send_code_request(PHONE_NUMBER)
        try:
            await client.sign_in(PHONE_NUMBER, input('Enter the code: '))
        except SessionPasswordNeededError:
            await client.sign_in(password=input('Password: '))

    @client.on(events.NewMessage(chats=CONTROL_CHANNEL_ID))
    async def command_handler(event):
        global is_running
        if event.raw_text == "/start":
            if not is_running:
                is_running = True
                await event.reply("Script iniciado.")
                await main_loop(client)
            else:
                await event.reply("El script ya está en ejecución.")
        elif event.raw_text == "/stop":
            if is_running:
                is_running = False
                await event.reply("Script detenido.")
            else:
                await event.reply("El script no está en ejecución.")

    print("Bot de control iniciado. Esperando comandos...")
    await client.run_until_disconnected()

if __name__ == '__main__':
    asyncio.run(main())
