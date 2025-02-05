
# Robingood - Telegram downloader with vitamins.


What make the difference?

- Use Telethon to monitor 2 channels (Movies and Series) each 1 min
- If you download 7z , zip or rar multipart in a joined message, it extract for you
- for let your gallery ready to use in Kodi or other media center, it execute tiny Media Manager (TMM), You have to configure TMM in GUI first, adding the paths and your setup.
- If you send a joined message, it questions you about a name folder, useful for TV shows, TMM needs a folder manually to make their work with TV Shows.
- Control channel for 3 commands. /start to start the monitoring, /stop to kill the process, /TMM to force TMM task now.
- Channel 2 Request the option to create a folder if you send a TV Show joined message.

# Robingood_STRM - Telegram streamer from your own channel.

- Suitable files max 4 gb allowed by telegram, it allows to streaming video message / video files in one part.
- It creates a web internal proxy in localhost, linked with a sqlite database that monitor the path of files.
- It creates folders and.STRM files that could be opened with VLC, kodi, JellyFin,etc (no web browsers)
- It creates a .nfo files for helping kodi, JellyFin, etc to request metadata. 
- If you delete the file in the chat, it delete in the system ,alongside with the sub folders, STRM and .nfo.


How to configure?

- Go to https://my.telegram.org/auth for discover your API_ID and API_HASH
- For channel id, you could use https://t.me/getInfoByIdBot ot come to telegram web and take the -10000000 and the end in the URL.
- Fill this configuration variables in top of the example  .env file. rename to .env
- pip install -r requirements.txt
- When it configured, you could add as service in Linux. tested in Steam Deck.

# Common vars
- API_ID=''  # Replace with your API ID de Telegram
- API_HASH=''  # Replace with your API Hash de Telegram
- PHONE_NUMBER=''  #  
- DOWNLOAD_SESSION_FILE='download'
- STREAMING_SESSION_FILE='streaming'
- WAIT_TIME=60  # in seconds

# vars for robingood.py
- MOVIES_DOWNLOAD_CHANNEL_ID=' '
- SERIES_DOWNLOAD_CHANNEL_ID=' '
- CONTROL_DOWNLOAD_CHANNEL_ID=' '
- MOVIES_DOWNLOAD_TEMP_FOLDER=' '
- SERIES_DOWNLOAD_TEMP_FOLDER=' '
- MOVIES_DOWNLOAD_FOLDER=' '
- SERIES_DOWNLOAD_FOLDER=' '
 
- USE_TMM=True  # Use TinyMediaManager (True/False)
- STATE_FILE=download_state.json

#  vars for robingood_streaming.py
- PROXY_PORT=8080  # streaming server port
- MOVIES_CHANNEL_ID= 
- SERIES_CHANNEL_ID=
- MOVIES_FOLDER=  
- SERIES_FOLDER= 
- DB_PATH=streaming_index.db  # path of database of processed files
- VALIDATE_DUPLICATES=true

# configurable language messages inside robingood.py

- MESSAGE_PROMPT_FOLDER = "Se detectó un grupo con ID `{grouped_id}`. ¿Quieres guardar los archivos en una carpeta nueva? Responde con `Y` o `N`."
- MESSAGE_ENTER_FOLDER_NAME = "Por favor, introduce el nombre de la carpeta:"
- MESSAGE_TIMEOUT_FOLDER = "No se recibió respuesta. Procediendo con la descarga y extracción."
- MESSAGE_FOLDER_CREATED = "Carpeta `{folder_name}` creada exitosamente."
- MESSAGE_PROCESS_COMPLETE = "Archivos del grupo `{grouped_id}` procesados y guardados en `{folder_path}`."
- MESSAGE_NO_FILES_FOUND = "No se encontraron archivos en el grupo."


# TODO

- ~~robingood_streaming needs some improvement with guessit and some strange file names.~~ robingood_streaming it's ready for use. 
- Replace TMM in robingood.py with guessit.
- Windows .exe precompiled, too much round for deploy all the dependencies in python. 
- Make a kodi addon for robingood_streaming (maybe, some day)
