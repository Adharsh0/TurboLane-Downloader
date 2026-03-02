"""
config.py — Application configuration for TurboLane Download Manager.

RL hyperparameters are NOT here — they live in EdgePolicy.
This file contains only application-level settings.
"""
import os

# ======================== Download Settings ========================
DEFAULT_NUM_STREAMS = 8
MIN_STREAMS = 1
MAX_STREAMS = 16

DEFAULT_CHUNK_SIZE = 4 * 1024 * 1024   # 4 MB
MIN_CHUNK_SIZE = 1024 * 1024           # 1 MB
BUFFER_SIZE = 8192

# Network timeouts
CONNECTION_TIMEOUT = 10
READ_TIMEOUT = 30
MAX_RETRIES = 3
RETRY_DELAY = 2

# ======================== RL Settings ========================
# Only the monitoring interval is here — it's a download-loop timing concern.
# All Q-learning hyperparameters live in EdgePolicy.
RL_MONITORING_INTERVAL = 5.0  # seconds between RL decisions

# ======================== Application Settings ========================
DOWNLOAD_FOLDER = os.path.join(
    os.path.expanduser("~"),
    "Downloads",
    "TurboLaneDownloader",
)

# Flask web interface
FLASK_HOST = "127.0.0.1"
FLASK_PORT = 5000
FLASK_DEBUG = False

# ======================== Logging ========================
ENABLE_VERBOSE_LOGGING = True
LOG_NETWORK_METRICS = True

# Create download folder on import
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)
