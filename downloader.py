"""
downloader.py

MultiStreamDownloader — parallel HTTP downloader with optional RL-based
stream count optimization.

Design principles:
- No RL logic here
- No Q-table, no reward function, no agent
- When use_rl=True, calls adapter.decide() and adapter.learn() — that's all
- All RL decisions flow through the adapter → engine → EdgePolicy → RLAgent
"""

import os
import requests
import threading
from urllib.parse import urlparse, unquote
import time
import subprocess
import platform
import logging

from config import (
    DEFAULT_NUM_STREAMS, MIN_STREAMS, MAX_STREAMS,
    DEFAULT_CHUNK_SIZE, MIN_CHUNK_SIZE, BUFFER_SIZE,
    CONNECTION_TIMEOUT, READ_TIMEOUT,
    RL_MONITORING_INTERVAL, DOWNLOAD_FOLDER,
    ENABLE_VERBOSE_LOGGING, LOG_NETWORK_METRICS,
)
from adapter import adapter

logger = logging.getLogger(__name__)


class MultiStreamDownloader:
    """
    Multi-stream HTTP downloader with optional RL-based stream optimization.

    When use_rl=True:
        - Calls adapter.decide() at each monitoring interval
        - Calls adapter.learn() after each interval to update the engine
        - Never touches Q-tables, rewards, or epsilon directly

    When use_rl=False:
        - Uses a fixed stream count throughout
    """

    def __init__(
        self,
        url: str,
        num_streams: int = DEFAULT_NUM_STREAMS,
        progress_callback=None,
        use_rl: bool = False,
    ):
        self.url = url
        self.num_streams = min(max(num_streams, MIN_STREAMS), MAX_STREAMS)
        self.progress_callback = progress_callback
        self.use_rl = use_rl

        # Download state
        self.file_size = 0
        self.downloaded_bytes = 0
        self.chunks = []
        self.temp_files = []
        self.is_downloading = False
        self.threads = []
        self.lock = threading.Lock()
        self.start_time = None

        # Chunk metrics
        self.chunk_start_times = {}
        self.chunk_end_times = {}
        self.chunk_speeds = {}
        self.chunk_bytes = {}
        self.failed_chunks = set()
        self.active_chunks = set()

        # Connection reset tracking — used to signal server overload to RL
        self._connection_resets = 0
        self._connection_resets_since_last_mi = 0

        # Network metrics (updated each monitoring interval)
        self.network_metrics = {
            "throughput": 0.0,
            "rtt": 100.0,
            "packet_loss": 0.1,
            "last_update": time.time(),
        }
        self._last_packet_loss = 0.1

        # Throughput tracking — rolling window for accurate measurement
        self._throughput_window = []        # list of (timestamp, bytes) samples
        self._throughput_window_size = 10   # keep last 10 samples
        self._last_speed_mbps = 0.0

        # Monitoring interval tracking
        self._last_mi_time = time.time()
        self._last_mi_bytes = 0

        # Current stream count
        if self.use_rl:
            self.current_stream_count = adapter.current_connections
            logger.info("RL Mode enabled — initial streams: %d", self.current_stream_count)
        else:
            self.current_stream_count = self.num_streams
            logger.info("Static Mode — streams: %d", self.num_streams)

    # -----------------------------------------------------------------------
    # Network metrics
    # -----------------------------------------------------------------------

    def measure_rtt(self) -> float:
        """Measure RTT via ping, fall back to chunk-based estimate."""
        try:
            hostname = urlparse(self.url).hostname
            param = "-n" if platform.system().lower() == "windows" else "-c"
            result = subprocess.run(
                ["ping", param, "1", "-W", "2", hostname],
                capture_output=True, text=True, timeout=3,
            )
            output = result.stdout.lower()
            if "time=" in output:
                rtt_str = output.split("time=")[1].split()[0]
                return float(rtt_str.replace("ms", ""))
        except Exception as e:
            logger.debug("RTT ping failed: %s", e)
        return self._estimate_rtt_from_chunks()

    def _estimate_rtt_from_chunks(self) -> float:
        if len(self.chunk_start_times) < 2:
            return 100.0
        starts = sorted(self.chunk_start_times.values())
        gaps = [starts[i + 1] - starts[i] for i in range(len(starts) - 1)]
        min_gap = min(gaps) if gaps else 0.1
        return min(1000.0, max(10.0, min_gap * 1000))

    def _estimate_packet_loss(self) -> float:
        """
        Estimate packet loss using:
        1. Connection reset rate — most reliable signal
        2. Chunk failure rate
        3. Speed variance (fallback)
        """
        total = len(self.chunks)
        failed = len(self.failed_chunks)
        failure_rate = failed / total if total > 0 else 0

        # Connection resets since last MI are a strong signal of server overload
        reset_penalty = min(3.0, self._connection_resets_since_last_mi * 0.5)

        # Chunk failure contribution
        loss_from_failures = min(3.0, failure_rate * 15.0)

        # Speed variance contribution (minor)
        loss_from_variance = 0.0
        if self.chunk_speeds and len(self.chunk_speeds) >= 3:
            recent_speeds = list(self.chunk_speeds.values())[-5:]
            avg_speed = sum(recent_speeds) / len(recent_speeds)
            if avg_speed > 0.1:
                variance = sum((s - avg_speed) ** 2 for s in recent_speeds) / len(recent_speeds)
                cv = (variance ** 0.5) / avg_speed
                loss_from_variance = min(1.0, cv * 1.0)  # reduced from 2.0

        estimated = (
            reset_penalty * 0.5
            + loss_from_failures * 0.3
            + loss_from_variance * 0.2
        )

        smoothed = 0.7 * self._last_packet_loss + 0.3 * estimated
        self._last_packet_loss = smoothed

        # Reset per-interval counter
        self._connection_resets_since_last_mi = 0

        return max(0.1, min(5.0, smoothed))

    def _add_throughput_sample(self):
        """Add a throughput sample to the rolling window."""
        now = time.time()
        with self.lock:
            bytes_now = self.downloaded_bytes
        self._throughput_window.append((now, bytes_now))
        # Keep only last N samples
        if len(self._throughput_window) > self._throughput_window_size:
            self._throughput_window.pop(0)

    def calculate_throughput(self) -> float:
        """
        Calculate total download throughput in Mbps using a rolling window.
        This measures ALL bytes across ALL streams — not per-stream.
        """
        self._add_throughput_sample()

        if len(self._throughput_window) < 2:
            # Not enough samples — fall back to total average
            if self.start_time and self.downloaded_bytes > 0:
                total_elapsed = time.time() - self.start_time
                if total_elapsed > 0.1:
                    speed = (self.downloaded_bytes * 8) / (total_elapsed * 1024 * 1024)
                    self._last_speed_mbps = speed
                    return speed
            return 0.0

        # Use oldest and newest sample in window for stable measurement
        oldest_time, oldest_bytes = self._throughput_window[0]
        newest_time, newest_bytes = self._throughput_window[-1]

        elapsed = newest_time - oldest_time
        if elapsed < 0.1:
            return self._last_speed_mbps

        bytes_delta = newest_bytes - oldest_bytes
        if bytes_delta < 0:
            return self._last_speed_mbps

        # Convert to Mbps (megabits per second)
        speed_mbps = (bytes_delta * 8) / (elapsed * 1024 * 1024)
        self._last_speed_mbps = speed_mbps
        return speed_mbps

    def get_speed(self) -> float:
        """Return current speed in MB/s."""
        return self.calculate_throughput() / 8

    def get_current_streams(self) -> int:
        """Return current number of active streams."""
        return self.current_stream_count

    def _update_network_metrics(self):
        throughput = self.calculate_throughput()
        rtt = self.measure_rtt()
        packet_loss = self._estimate_packet_loss()

        self.network_metrics.update({
            "throughput": throughput,
            "rtt": rtt,
            "packet_loss": packet_loss,
            "last_update": time.time(),
        })

        if LOG_NETWORK_METRICS:
            logger.info(
                "Network: T=%.2fMbps RTT=%.1fms Loss=%.2f%%",
                throughput, rtt, packet_loss,
            )

        return throughput, rtt, packet_loss

    # -----------------------------------------------------------------------
    # Monitoring interval — calls adapter, not RL directly
    # -----------------------------------------------------------------------

    def _should_run_mi(self) -> bool:
        return time.time() - self._last_mi_time >= RL_MONITORING_INTERVAL

    def _run_monitoring_interval(self) -> None:
        """
        One monitoring interval cycle:
          1. Measure network state (total throughput across all streams)
          2. Tell engine what happened (learn)
          3. Ask engine what to do next (decide)
          4. Apply the decision
        """
        if not self.use_rl or not self._should_run_mi():
            return

        try:
            throughput, rtt, packet_loss = self._update_network_metrics()

            # Step 2: learn from what happened since last decision
            adapter.learn(throughput, rtt, packet_loss)

            # Step 3: get new stream count recommendation
            new_stream_count = adapter.decide(throughput, rtt, packet_loss)

            # Step 4: apply it
            if new_stream_count != self.current_stream_count:
                logger.info(
                    "Streams adjusted: %d → %d",
                    self.current_stream_count, new_stream_count,
                )
                self.current_stream_count = new_stream_count

            self._last_mi_time = time.time()
            self._last_mi_bytes = self.downloaded_bytes

        except Exception as e:
            logger.error("Monitoring interval error: %s", e)

    # -----------------------------------------------------------------------
    # Chunk management
    # -----------------------------------------------------------------------

    def _get_filename_from_url(self) -> str:
        path = urlparse(self.url).path
        filename = unquote(os.path.basename(path))
        return filename if filename else "downloaded_file"

    def check_download_support(self):
        """Check if server supports range requests."""
        try:
            response = requests.head(
                self.url, timeout=CONNECTION_TIMEOUT, allow_redirects=True
            )
            supports_ranges = response.headers.get("Accept-Ranges") == "bytes"
            file_size = int(response.headers.get("Content-Length", 0))
            cd = response.headers.get("Content-Disposition", "")
            filename = (
                cd.split("filename=")[1].strip('"')
                if "filename=" in cd
                else self._get_filename_from_url()
            )
            return supports_ranges, file_size, filename

        except Exception:
            try:
                response = requests.get(
                    self.url,
                    headers={"Range": "bytes=0-0"},
                    timeout=CONNECTION_TIMEOUT,
                    stream=True,
                )
                supports_ranges = response.status_code == 206
                if "Content-Range" in response.headers:
                    file_size = int(response.headers["Content-Range"].split("/")[-1])
                else:
                    file_size = int(response.headers.get("Content-Length", 0))
                response.close()
                return supports_ranges, file_size, self._get_filename_from_url()
            except Exception as e:
                logger.error("Range check failed: %s", e)
                return False, 0, self._get_filename_from_url()

    def _calculate_chunks(self, file_size: int, max_streams: int) -> list:
        min_chunk_size = max(MIN_CHUNK_SIZE, 1024 * 1024)
        if file_size < min_chunk_size * max_streams:
            actual_chunks = max(1, file_size // min_chunk_size)
        else:
            actual_chunks = max_streams

        chunk_size = file_size // actual_chunks
        chunks = []
        for i in range(actual_chunks):
            start = i * chunk_size
            end = file_size - 1 if i == actual_chunks - 1 else (i + 1) * chunk_size - 1
            chunks.append((start, end))

        logger.info("Created %d chunks (%.1f MB each)", len(chunks), chunk_size / (1024 * 1024))
        return chunks

    def _download_chunk(self, chunk_id: int, start: int, end: int, temp_file: str) -> None:
        headers = {"Range": f"bytes={start}-{end}"}

        with self.lock:
            self.chunk_start_times[chunk_id] = time.time()
            self.active_chunks.add(chunk_id)

        chunk_bytes = 0
        chunk_start = time.time()

        try:
            with requests.get(
                self.url,
                headers=headers,
                stream=True,
                timeout=(CONNECTION_TIMEOUT, READ_TIMEOUT),
            ) as r:
                if r.status_code not in [200, 206]:
                    logger.warning("Chunk %d: bad status %d", chunk_id, r.status_code)
                    with self.lock:
                        self.failed_chunks.add(chunk_id)
                        self.active_chunks.discard(chunk_id)
                    return

                with open(temp_file, "wb") as f:
                    for data in r.iter_content(chunk_size=BUFFER_SIZE):
                        if not self.is_downloading:
                            break
                        f.write(data)
                        chunk_bytes += len(data)
                        with self.lock:
                            self.downloaded_bytes += len(data)
                            if self.progress_callback:
                                self.progress_callback(self.downloaded_bytes, self.file_size)

            chunk_elapsed = time.time() - chunk_start
            with self.lock:
                self.chunk_end_times[chunk_id] = time.time()
                self.chunk_bytes[chunk_id] = chunk_bytes
                self.chunk_speeds[chunk_id] = (
                    (chunk_bytes / (1024 * 1024)) / max(chunk_elapsed, 0.1)
                )
                self.active_chunks.discard(chunk_id)

        except Exception as e:
            logger.error("Chunk %d failed: %s", chunk_id, e)
            with self.lock:
                self.failed_chunks.add(chunk_id)
                self.active_chunks.discard(chunk_id)
                # Track connection resets specifically — signals server overload to RL
                error_str = str(e).lower()
                if "connection" in error_str and (
                    "reset" in error_str or "aborted" in error_str or "closed" in error_str
                ):
                    self._connection_resets += 1
                    self._connection_resets_since_last_mi += 1
                    logger.debug(
                        "Connection reset on chunk %d (total resets: %d)",
                        chunk_id, self._connection_resets
                    )
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except Exception:
                    pass

    def _start_chunk_download(self, chunk_id: int, output_path: str) -> None:
        if chunk_id >= len(self.chunks):
            return
        start, end = self.chunks[chunk_id]
        temp_file = f"{output_path}.part{chunk_id}"
        if temp_file not in self.temp_files:
            self.temp_files.append(temp_file)
        thread = threading.Thread(
            target=self._download_chunk,
            args=(chunk_id, start, end, temp_file),
            daemon=True,
        )
        thread.start()
        self.threads.append(thread)

    # -----------------------------------------------------------------------
    # Download strategies
    # -----------------------------------------------------------------------

    def _download_with_rl(self, output_path: str) -> bool:
        """Adaptive download — stream count driven by TurboLaneEngine."""
        logger.info("Starting RL-based adaptive download")
        self.is_downloading = True
        self.downloaded_bytes = 0
        self.start_time = time.time()
        self._last_mi_time = self.start_time
        self._throughput_window = []
        self.threads, self.temp_files = [], []

        self.chunks = self._calculate_chunks(self.file_size, MAX_STREAMS)
        remaining = set(range(len(self.chunks)))

        initial = min(self.current_stream_count, len(remaining))
        logger.info("Starting with %d streams", initial)
        for _ in range(initial):
            if remaining:
                self._start_chunk_download(remaining.pop(), output_path)

        last_progress_log = time.time()

        while (remaining or self.threads) and self.is_downloading:
            self._run_monitoring_interval()
            self.threads = [t for t in self.threads if t.is_alive()]

            available_slots = self.current_stream_count - len(self.threads)
            if available_slots > 0 and remaining:
                for _ in range(min(available_slots, len(remaining))):
                    if remaining:
                        self._start_chunk_download(remaining.pop(), output_path)

            if time.time() - last_progress_log >= 5:
                if self.downloaded_bytes > 0 and self.file_size > 0:
                    progress = (self.downloaded_bytes / self.file_size) * 100
                    logger.info(
                        "Progress: %.1f%% | Active: %d | Speed: %.1f Mbps | Streams: %d",
                        progress, len(self.active_chunks),
                        self.calculate_throughput(),
                        self.current_stream_count,
                    )
                last_progress_log = time.time()

            time.sleep(0.5)

        for thread in self.threads:
            thread.join(timeout=60)

        success = len(self.failed_chunks) == 0
        total_time = time.time() - self.start_time
        logger.info(
            "Download complete in %.1fs | Success rate: %.1f%%",
            total_time,
            (len(self.chunks) - len(self.failed_chunks)) / max(len(self.chunks), 1) * 100,
        )
        return success

    def _download_static(self, output_path: str) -> bool:
        """Fixed-stream download — no RL."""
        logger.info("Starting static download with %d streams", self.num_streams)
        self.is_downloading = True
        self.downloaded_bytes = 0
        self.start_time = time.time()

        self.chunks = self._calculate_chunks(self.file_size, self.num_streams)
        for i in range(len(self.chunks)):
            self._start_chunk_download(i, output_path)

        for thread in self.threads:
            thread.join(timeout=300)

        success = len(self.failed_chunks) == 0
        logger.info("Download complete in %.1fs", time.time() - self.start_time)
        return success

    # -----------------------------------------------------------------------
    # File assembly
    # -----------------------------------------------------------------------

    def _assemble_file(self, output_file: str) -> None:
        logger.info("Assembling %d parts", len(self.temp_files))
        try:
            with open(output_file, "wb") as out:
                for tmp in self.temp_files:
                    if os.path.exists(tmp):
                        with open(tmp, "rb") as part:
                            out.write(part.read())
                        os.remove(tmp)

            if os.path.exists(output_file):
                actual_size = os.path.getsize(output_file)
                if actual_size == self.file_size:
                    logger.info("File assembled and verified")
                else:
                    logger.warning(
                        "Size mismatch: expected %d, got %d",
                        self.file_size, actual_size,
                    )
        except Exception as e:
            logger.error("File assembly error: %s", e)

    def cleanup(self) -> None:
        for f in self.temp_files:
            if os.path.exists(f):
                try:
                    os.remove(f)
                except Exception:
                    pass

    def cancel(self) -> None:
        logger.info("Cancelling download")
        self.is_downloading = False
        time.sleep(1)
        self.cleanup()

    # -----------------------------------------------------------------------
    # Main entry point
    # -----------------------------------------------------------------------

    def download(self, output_path: str = None) -> str | None:
        try:
            supports_ranges, file_size, filename = self.check_download_support()
            self.file_size = file_size

            if not supports_ranges:
                logger.warning("Range requests not supported — falling back to single stream")
                self.num_streams = 1
                self.use_rl = False

            output_path = output_path or os.path.join(DOWNLOAD_FOLDER, filename)
            os.makedirs(os.path.dirname(output_path), exist_ok=True)

            logger.info("Output: %s | Size: %.1f MB", output_path, file_size / (1024 * 1024))

            if self.use_rl:
                success = self._download_with_rl(output_path)
            else:
                success = self._download_static(output_path)

            if success and self.is_downloading:
                self._assemble_file(output_path)

                if self.use_rl:
                    adapter.save()

                logger.info("Average throughput: %.2f Mbps", self.calculate_throughput())
                return output_path
            else:
                self.cleanup()
                return None

        except Exception as e:
            logger.error("Download error: %s", e)
            import traceback
            traceback.print_exc()
            self.cleanup()
            return None

    # -----------------------------------------------------------------------
    # Stats / metrics
    # -----------------------------------------------------------------------

    def get_stats(self) -> dict:
        elapsed = time.time() - self.start_time if self.start_time else 0
        stats = {
            "elapsed_time": elapsed,
            "downloaded_bytes": self.downloaded_bytes,
            "file_size": self.file_size,
            "progress": (self.downloaded_bytes / self.file_size * 100) if self.file_size > 0 else 0,
            "throughput_mbps": self.calculate_throughput(),
            "num_chunks": len(self.chunks),
            "completed_chunks": len(self.chunk_end_times),
            "failed_chunks": len(self.failed_chunks),
            "active_chunks": len(self.active_chunks),
            "connection_resets": self._connection_resets,
        }
        if self.use_rl:
            stats["rl_stats"] = adapter.get_stats()
        return stats

    def get_detailed_metrics(self) -> dict:
        stats = self.get_stats()
        return {
            **stats,
            "current_stream_count": self.current_stream_count,
            "use_rl": self.use_rl,
            "url": self.url,
            "is_downloading": self.is_downloading,
            "network_metrics": self.network_metrics.copy(),
            "chunk_progress": {
                "total": len(self.chunks),
                "completed": len(self.chunk_end_times),
                "failed": len(self.failed_chunks),
                "active": len(self.active_chunks),
            },
        }