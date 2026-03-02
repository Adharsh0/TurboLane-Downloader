
# """
# downloader.py
# MultiStreamDownloader — parallel HTTP downloader with optional RL optimization
# """

# import os
# import requests
# import threading
# from urllib.parse import urlparse, unquote
# import time
# import logging

# from config import (
#     DEFAULT_NUM_STREAMS, MIN_STREAMS, MAX_STREAMS,
#     MIN_CHUNK_SIZE, BUFFER_SIZE,
#     CONNECTION_TIMEOUT, READ_TIMEOUT,
#     RL_MONITORING_INTERVAL, DOWNLOAD_FOLDER,
# )
# from adapter import adapter

# logger = logging.getLogger(__name__)


# class MultiStreamDownloader:

#     def __init__(self, url, num_streams=DEFAULT_NUM_STREAMS, progress_callback=None, use_rl=False):
#         self.url = url
#         self.num_streams = min(max(num_streams, MIN_STREAMS), MAX_STREAMS)
#         self.use_rl = use_rl
#         self.progress_callback = progress_callback

#         self.current_stream_count = self.num_streams
#         self.file_size = 0
#         self.downloaded_bytes = 0
#         self.is_downloading = False
#         self.chunks = []
#         self.threads = []
#         self.temp_files = []
#         self.lock = threading.Lock()
#         self.start_time = None

#         self._last_mi_time = time.time()
#         self._last_mi_bytes = 0

#         if self.use_rl:
#             self.current_stream_count = adapter.current_connections
#             logger.info(f"RL enabled. Initial streams: {self.current_stream_count}")

#     # ------------------------------------------------------------
#     # CRITICAL FIX → expose current stream count to backend
#     # ------------------------------------------------------------
#     def get_current_streams(self):
#         return self.current_stream_count

#     # ------------------------------------------------------------
#     # Basic network stats
#     # ------------------------------------------------------------
#     def calculate_throughput(self):
#         now = time.time()
#         elapsed = now - self._last_mi_time
#         if elapsed <= 0:
#             return 0
#         bytes_delta = self.downloaded_bytes - self._last_mi_bytes
#         return (bytes_delta * 8) / (elapsed * 1024 * 1024)

#     def get_speed(self):
#         return self.calculate_throughput() / 8

#     # ------------------------------------------------------------
#     # RL monitoring interval
#     # ------------------------------------------------------------
#     def _run_monitoring_interval(self):
#         if not self.use_rl:
#             return

#         if time.time() - self._last_mi_time < RL_MONITORING_INTERVAL:
#             return

#         throughput = self.calculate_throughput()

#         if throughput > 0:
#             adapter.learn(throughput, 100, 0.1)

#         new_streams = adapter.decide(throughput, 100, 0.1)

#         if new_streams != self.current_stream_count:
#             logger.info(f"Streams changed {self.current_stream_count} → {new_streams}")
#             self.current_stream_count = new_streams

#         self._last_mi_bytes = self.downloaded_bytes
#         self._last_mi_time = time.time()

#     # ------------------------------------------------------------
#     # Chunk handling
#     # ------------------------------------------------------------
#     def _get_filename(self):
#         path = urlparse(self.url).path
#         filename = unquote(os.path.basename(path))
#         return filename or "downloaded_file"

#     def check_download_support(self):
#         response = requests.head(self.url, timeout=CONNECTION_TIMEOUT, allow_redirects=True)
#         supports_ranges = response.headers.get("Accept-Ranges") == "bytes"
#         size = int(response.headers.get("Content-Length", 0))
#         return supports_ranges, size, self._get_filename()

#     def _calculate_chunks(self, size, streams):
#         chunk_size = size // streams
#         chunks = []
#         for i in range(streams):
#             start = i * chunk_size
#             end = size - 1 if i == streams - 1 else (i + 1) * chunk_size - 1
#             chunks.append((start, end))
#         return chunks

#     def _download_chunk(self, chunk_id, start, end, temp_file):
#         headers = {"Range": f"bytes={start}-{end}"}
#         try:
#             with requests.get(self.url, headers=headers, stream=True,
#                               timeout=(CONNECTION_TIMEOUT, READ_TIMEOUT)) as r:
#                 with open(temp_file, "wb") as f:
#                     for data in r.iter_content(chunk_size=BUFFER_SIZE):
#                         if not self.is_downloading:
#                             break
#                         f.write(data)
#                         with self.lock:
#                             self.downloaded_bytes += len(data)
#         except Exception as e:
#             logger.error(f"Chunk {chunk_id} failed: {e}")

#     # ------------------------------------------------------------
#     # Download logic
#     # ------------------------------------------------------------
#     def download(self):
#         supports, size, filename = self.check_download_support()
#         self.file_size = size
#         output = os.path.join(DOWNLOAD_FOLDER, filename)
#         os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

#         if not supports:
#             self.num_streams = 1
#             self.current_stream_count = 1
#             self.use_rl = False

#         self.is_downloading = True
#         self.start_time = time.time()

#         if self.use_rl:
#             max_streams = MAX_STREAMS
#         else:
#             max_streams = self.num_streams

#         self.chunks = self._calculate_chunks(size, max_streams)
#         remaining = list(range(len(self.chunks)))

#         while remaining and self.is_downloading:
#             self._run_monitoring_interval()

#             active = len([t for t in self.threads if t.is_alive()])
#             available = self.current_stream_count - active

#             while available > 0 and remaining:
#                 chunk_id = remaining.pop(0)
#                 start, end = self.chunks[chunk_id]
#                 temp = f"{output}.part{chunk_id}"
#                 self.temp_files.append(temp)
#                 t = threading.Thread(target=self._download_chunk,
#                                      args=(chunk_id, start, end, temp),
#                                      daemon=True)
#                 t.start()
#                 self.threads.append(t)
#                 available -= 1

#             self.threads = [t for t in self.threads if t.is_alive()]
#             time.sleep(0.3)

#         for t in self.threads:
#             t.join()

#         self._assemble(output)
#         return output

#     def _assemble(self, output):
#         with open(output, "wb") as outfile:
#             for part in self.temp_files:
#                 if os.path.exists(part):
#                     with open(part, "rb") as pf:
#                         outfile.write(pf.read())
#                     os.remove(part)

#     def cancel(self):
#         self.is_downloading = False
"""
downloader.py
MultiStreamDownloader — parallel HTTP downloader with optional RL optimization
"""

import os
import requests
import threading
import uuid
import shutil
from urllib.parse import urlparse, unquote
import time
import logging

from config import (
    DEFAULT_NUM_STREAMS, MIN_STREAMS, MAX_STREAMS,
    MIN_CHUNK_SIZE, BUFFER_SIZE,
    CONNECTION_TIMEOUT, READ_TIMEOUT,
    RL_MONITORING_INTERVAL, DOWNLOAD_FOLDER,
)
from adapter import adapter

logger = logging.getLogger(__name__)


class MultiStreamDownloader:

    def __init__(self, url, num_streams=DEFAULT_NUM_STREAMS, progress_callback=None, use_rl=False):
        self.url = url
        self.num_streams = min(max(num_streams, MIN_STREAMS), MAX_STREAMS)
        self.use_rl = use_rl
        self.progress_callback = progress_callback
        self.download_id = str(uuid.uuid4())[:8]  # Unique ID for this download instance

        self.current_stream_count = self.num_streams
        self.file_size = 0
        self.downloaded_bytes = 0
        self.is_downloading = False
        self.chunks = []
        self.threads = []
        self.temp_files = []
        self.lock = threading.Lock()
        self.start_time = None

        self._last_mi_time = time.time()
        self._last_mi_bytes = 0

        if self.use_rl:
            self.current_stream_count = adapter.current_connections
            logger.info(f"RL enabled. Initial streams: {self.current_stream_count}")

    # ------------------------------------------------------------
    # CRITICAL FIX → expose current stream count to backend
    # ------------------------------------------------------------
    def get_current_streams(self):
        return self.current_stream_count

    # ------------------------------------------------------------
    # Basic network stats
    # ------------------------------------------------------------
    def calculate_throughput(self):
        now = time.time()
        elapsed = now - self._last_mi_time
        if elapsed <= 0:
            return 0
        bytes_delta = self.downloaded_bytes - self._last_mi_bytes
        return (bytes_delta * 8) / (elapsed * 1024 * 1024)

    def get_speed(self):
        return self.calculate_throughput() / 8

    # ------------------------------------------------------------
    # RL monitoring interval
    # ------------------------------------------------------------
    def _run_monitoring_interval(self):
        if not self.use_rl:
            return

        if time.time() - self._last_mi_time < RL_MONITORING_INTERVAL:
            return

        throughput = self.calculate_throughput()

        if throughput > 0:
            adapter.learn(throughput, 100, 0.1)

        new_streams = adapter.decide(throughput, 100, 0.1)

        if new_streams != self.current_stream_count:
            logger.info(f"Streams changed {self.current_stream_count} → {new_streams}")
            self.current_stream_count = new_streams

        self._last_mi_bytes = self.downloaded_bytes
        self._last_mi_time = time.time()

    # ------------------------------------------------------------
    # Chunk handling
    # ------------------------------------------------------------
    def _get_filename(self):
        path = urlparse(self.url).path
        filename = unquote(os.path.basename(path))
        return filename or "downloaded_file"

    def check_download_support(self):
        response = requests.head(self.url, timeout=CONNECTION_TIMEOUT, allow_redirects=True)
        supports_ranges = response.headers.get("Accept-Ranges") == "bytes"
        size = int(response.headers.get("Content-Length", 0))
        return supports_ranges, size, self._get_filename()

    def _calculate_chunks(self, size, streams):
        chunk_size = size // streams
        chunks = []
        for i in range(streams):
            start = i * chunk_size
            end = size - 1 if i == streams - 1 else (i + 1) * chunk_size - 1
            chunks.append((start, end))
        return chunks

    def _download_chunk(self, chunk_id, start, end, temp_file):
        headers = {"Range": f"bytes={start}-{end}"}
        try:
            with requests.get(self.url, headers=headers, stream=True,
                              timeout=(CONNECTION_TIMEOUT, READ_TIMEOUT)) as r:
                with open(temp_file, "wb") as f:
                    for data in r.iter_content(chunk_size=BUFFER_SIZE):
                        if not self.is_downloading:
                            break
                        f.write(data)
                        with self.lock:
                            self.downloaded_bytes += len(data)
        except Exception as e:
            logger.error(f"Chunk {chunk_id} failed: {e}")

    # ------------------------------------------------------------
    # Download logic
    # ------------------------------------------------------------
    def download(self):
        supports, size, filename = self.check_download_support()
        self.file_size = size
        output = os.path.join(DOWNLOAD_FOLDER, filename)
        os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

        if not supports:
            self.num_streams = 1
            self.current_stream_count = 1
            self.use_rl = False

        self.is_downloading = True
        self.start_time = time.time()

        if self.use_rl:
            max_streams = MAX_STREAMS
        else:
            max_streams = self.num_streams

        self.chunks = self._calculate_chunks(size, max_streams)
        remaining = list(range(len(self.chunks)))

        while remaining and self.is_downloading:
            self._run_monitoring_interval()

            active = len([t for t in self.threads if t.is_alive()])
            available = self.current_stream_count - active

            while available > 0 and remaining:
                chunk_id = remaining.pop(0)
                start, end = self.chunks[chunk_id]
                # FIXED: Use unique temp file names with download_id
                temp = f"{output}.part{chunk_id}.{self.download_id}.tmp"
                self.temp_files.append(temp)
                t = threading.Thread(target=self._download_chunk,
                                     args=(chunk_id, start, end, temp),
                                     daemon=True)
                t.start()
                self.threads.append(t)
                available -= 1

            self.threads = [t for t in self.threads if t.is_alive()]
            time.sleep(0.3)

        for t in self.threads:
            t.join()

        # FIXED: Better assembly with temporary output file
        return self._assemble(output)

    def _assemble(self, output):
        """Assemble chunks into final file with better error handling"""
        # Use a temporary file for assembly to avoid partial writes
        temp_output = f"{output}.{self.download_id}.tmp"
        
        try:
            # Sort temp files by chunk ID to ensure correct order
            self.temp_files.sort()
            
            with open(temp_output, "wb") as outfile:
                for part in self.temp_files:
                    if os.path.exists(part):
                        try:
                            with open(part, "rb") as pf:
                                shutil.copyfileobj(pf, outfile)
                        except Exception as e:
                            logger.error(f"Error reading chunk {part}: {e}")
                            raise
                    else:
                        logger.error(f"Missing chunk: {part}")
                        raise FileNotFoundError(f"Missing chunk file: {part}")

            # If original output exists and is different, handle it
            if os.path.exists(output):
                # If files are the same size and content, we can skip
                if os.path.getsize(output) == os.path.getsize(temp_output):
                    # Quick check if they're the same file
                    if not os.path.samefile(output, temp_output):
                        os.remove(temp_output)
                        return output
                else:
                    # Different file, create backup with timestamp
                    timestamp = time.strftime("%Y%m%d_%H%M%S")
                    backup = f"{output}.{timestamp}.bak"
                    shutil.move(output, backup)
                    logger.info(f"Existing file backed up to {backup}")

            # Move temp file to final destination
            shutil.move(temp_output, output)
            logger.info(f"Successfully assembled file: {output}")
            
        except Exception as e:
            logger.error(f"Assembly failed: {e}")
            # Clean up temp file if it exists
            if os.path.exists(temp_output):
                os.remove(temp_output)
            raise
        finally:
            # Clean up chunk files
            for part in self.temp_files:
                try:
                    if os.path.exists(part):
                        os.remove(part)
                except Exception as e:
                    logger.error(f"Error removing chunk {part}: {e}")

        return output

    def cancel(self):
        self.is_downloading = False