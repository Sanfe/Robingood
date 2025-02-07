
# Robingood - Telegram downloader with vitamins.


What make the difference?

- Use Telethon to monitor 2 channels and download (Movies and Series) each 90"
- If you download 7z , zip or rar multipart in a joined message, it extract for you
- for let your gallery ready to use in Kodi or other media center, it execute tiny Media Manager (TMM), You have to configure TMM in GUI first, adding the paths and your setup.
- If you send a joined message, it questions you about a name folder, useful for TV shows, TMM needs a folder manually to make their work with TV Shows.
- Control channel 
- Channel TV Request the option to create a folder if you send a TV Show joined message.
- configurable language messages inside messages.py

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

- ST_SESSION = 'streaming'
- WAIT_TIME=60  # in seconds

# vars for robingood.py
- DW_SESSION = 'download'  # Nombre del archivo de sesión
  
- DW_MOVIES_CH_ID = "-100 # ID del canal de películas
- DW_MOVIES_T_FOLDER = 'temp folder'
- DW_MOVIES_FOLDER = 'path'

- DW_TV_CH_ID = -100 # ID del canal de series
- DW_TV_T_FOLDER = 'temp folder'
- DW_TV_FOLDER = 'path'
 
- USE_TMM=True  # Use TinyMediaManager (True/False)
- STATE_FILE=download_state.json

#  vars for robingood_streaming.py
ST_SESSION = 'streaming'
DB_PATH=streaming.sqlite
BASE_URL=http://localhost:
PROXY_PORT = 8200

ST_MOVIES_FOLDER=path
ST_TV_FOLDER=path
VALIDATE_DUPLICATES=true

# Commands

robingood download always are waiting to start

'/start download' to start tasks
'/stop' to stop robingood download listening.
'/TMM' to force tiny media manager start.

robingood streaming

'/add' channel_id
'/edit list (movie/channel) for not identified items
'/edit' (movie/channel) "name" "newname")
'/skip' to delete from the /edit list
'/status' show the streaming channels and the indexed stats
'/import' add all media from all channels (use with caution)
'/del' channel_id

# TODO


- Replace TMM in robingood.py with guessit + TMDB.
- Windows .exe precompiled, too much round for deploy all the dependencies in python. 
- Make a kodi addon for robingood_streaming (maybe, some day)
