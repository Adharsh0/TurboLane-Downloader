"""
app.py — Flask web interface for TurboLane Download Manager.

RL endpoints talk to the adapter — no engine or policy imports here.
"""
import sys
import webview
import os
import threading
import time
import glob
import mimetypes
from flask import Flask, render_template, request, jsonify, send_file

from downloader import MultiStreamDownloader
from simple_downloader import SimpleDownloader
from config import DOWNLOAD_FOLDER, FLASK_HOST, FLASK_PORT, FLASK_DEBUG
from adapter import adapter

app = Flask(__name__, 
            static_folder='static',
            template_folder='templates')
app.config["SECRET_KEY"] = "turbolane-downloader"


# ---------------------------------------------------------------------------
# Download manager
# ---------------------------------------------------------------------------

class DownloadManager:
    def __init__(self):
        self.active_downloads = {}

    def start_download(self, url, mode, num_streams, use_rl=False):
        download_id = str(int(time.time() * 1000))

        if mode == "single":
            downloader = SimpleDownloader(url, progress_callback=None)
        else:
            downloader = MultiStreamDownloader(
                url,
                num_streams=num_streams,
                progress_callback=None,
                use_rl=use_rl,
            )

        self.active_downloads[download_id] = {
            "downloader": downloader,
            "url": url,
            "mode": mode,
            "status": "downloading",
            "progress": 0,
            "speed": 0,
            "start_time": time.time(),
            "filename": None,
            "error": None,
            "total_size": 0,
            "downloaded_size": 0,
            "use_rl": use_rl,
            "num_streams": num_streams,  # Store initial stream count
            "current_streams": num_streams,  # Track current stream count
        }

        thread = threading.Thread(
            target=self._download_thread, args=(download_id,), daemon=True
        )
        thread.start()
        self.active_downloads[download_id]["thread"] = thread
        return download_id

    def _download_thread(self, download_id):
        info = self.active_downloads.get(download_id)
        if not info:
            return

        downloader = info["downloader"]
        try:
            if hasattr(downloader, "get_file_info"):
                file_size, filename = downloader.get_file_info()
                info["total_size"] = file_size
                info["filename"] = filename
            elif hasattr(downloader, "check_download_support"):
                _, file_size, filename = downloader.check_download_support()
                info["total_size"] = file_size
                info["filename"] = filename

            result = downloader.download()
            if result:
                info["status"] = "completed"
                info["result_path"] = result
                info["filename"] = os.path.basename(result)
                info["downloaded_size"] = info["total_size"]
                try:
                    info["metrics"] = downloader.get_detailed_metrics()
                except Exception:
                    info["metrics"] = None
            else:
                info["status"] = "failed"
                info["error"] = "Download failed"

        except Exception as e:
            info["status"] = "failed"
            info["error"] = str(e)

    def get_download_status(self, download_id):
        if download_id not in self.active_downloads:
            return None

        info = self.active_downloads[download_id]
        downloader = info.get("downloader")

        if downloader:
            try:
                # Update progress and speed
                if hasattr(downloader, "downloaded_bytes") and hasattr(downloader, "file_size"):
                    if downloader.file_size > 0:
                        info["progress"] = (downloader.downloaded_bytes / downloader.file_size) * 100
                        info["downloaded_size"] = downloader.downloaded_bytes
                        info["total_size"] = downloader.file_size
                    info["speed"] = downloader.get_speed() if hasattr(downloader, "get_speed") else 0
                
                # Update current stream count for RL mode - Always update
                if info.get("use_rl") and hasattr(downloader, "get_current_streams"):
                    info["current_streams"] = downloader.get_current_streams()
                elif hasattr(downloader, "num_streams"):
                    info["current_streams"] = downloader.num_streams
                    
            except Exception:
                pass

        # Return both stream_count and current_streams for compatibility
        return {
            "url": info["url"],
            "mode": info["mode"],
            "status": info["status"],
            "progress": info["progress"],
            "speed": info["speed"],
            "start_time": info["start_time"],
            "filename": info.get("filename"),
            "error": info.get("error"),
            "metrics": info.get("metrics"),
            "total_size": info.get("total_size", 0),
            "downloaded_size": info.get("downloaded_size", 0),
            "use_rl": info.get("use_rl", False),
            "num_streams": info.get("num_streams", 0),
            # Send both field names for compatibility
            "stream_count": info.get("current_streams", info.get("num_streams", 0)),
            "current_streams": info.get("current_streams", info.get("num_streams", 0)),
        }

    def cancel_download(self, download_id):
        if download_id in self.active_downloads:
            info = self.active_downloads[download_id]
            if info.get("downloader"):
                info["downloader"].cancel()
            info["status"] = "cancelled"
            return True
        return False


download_manager = DownloadManager()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/downloads", methods=["POST"])
def start_download():
    data = request.json
    url = data.get("url", "").strip()
    mode = data.get("mode", "multi")
    num_streams = int(data.get("num_streams", 8))
    use_rl = data.get("use_rl", False)

    if not url:
        return jsonify({"error": "URL is required"}), 400
    if not url.startswith(("http://", "https://")):
        return jsonify({"error": "URL must start with http:// or https://"}), 400

    try:
        download_id = download_manager.start_download(url, mode, num_streams, use_rl)
        return jsonify({
            "download_id": download_id,
            "message": "Download started successfully",
            "rl_enabled": use_rl,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/downloads/<download_id>")
def get_download_status(download_id):
    status = download_manager.get_download_status(download_id)
    if status:
        return jsonify(status)
    return jsonify({"error": "Download not found"}), 404


@app.route("/api/downloads/<download_id>/cancel", methods=["POST"])
def cancel_download(download_id):
    if download_manager.cancel_download(download_id):
        return jsonify({"message": "Download cancelled"})
    return jsonify({"error": "Download not found"}), 404


@app.route("/api/downloads/<download_id>/metrics")
def get_download_metrics(download_id):
    status = download_manager.get_download_status(download_id)
    if status and "metrics" in status:
        return jsonify(status["metrics"])
    return jsonify({"error": "Metrics not available"}), 404


# RL endpoints — talk to the adapter, nothing lower
@app.route("/api/rl/stats")
def get_rl_stats():
    """Get RL engine statistics."""
    return jsonify(adapter.get_stats())


@app.route("/api/rl/reset", methods=["POST"])
def reset_rl():
    """Reset the RL engine's learned state."""
    adapter.reset()
    adapter.save()
    return jsonify({"message": "RL state reset successfully"})


@app.route("/api/rl/save", methods=["POST"])
def save_rl():
    """Persist the RL engine's Q-table."""
    success = adapter.save()
    if success:
        return jsonify({"message": "RL state saved successfully"})
    return jsonify({"error": "Failed to save RL state"}), 500


# File management
@app.route("/downloads/<filename>")
def download_file(filename):
    file_path = os.path.join(DOWNLOAD_FOLDER, filename)
    if not os.path.exists(file_path):
        return jsonify({"error": "File not found"}), 404

    mime_type, _ = mimetypes.guess_type(filename)
    force_dl_exts = [".zip", ".rar", ".7z", ".exe", ".msi", ".dmg", ".pkg", ".deb", ".rpm"]
    browser_exts = [".pdf", ".jpg", ".jpeg", ".png", ".gif", ".txt", ".mp4", ".mp3"]

    as_attachment = any(filename.lower().endswith(e) for e in force_dl_exts)
    can_inline = any(filename.lower().endswith(e) for e in browser_exts)

    return send_file(
        file_path,
        as_attachment=as_attachment or not can_inline,
        download_name=filename if (as_attachment or not can_inline) else None,
        mimetype=mime_type,
    )


@app.route("/api/files")
def list_files():
    try:
        files = []
        for file_path in glob.glob(os.path.join(DOWNLOAD_FOLDER, "*")):
            if os.path.isfile(file_path):
                name = os.path.basename(file_path)
                if not (name.startswith(".") or name.endswith(".part")):
                    files.append({
                        "name": name,
                        "size": os.path.getsize(file_path),
                        "modified": os.path.getmtime(file_path),
                    })
        files.sort(key=lambda x: x["modified"], reverse=True)
        return jsonify(files)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/files/<filename>", methods=["DELETE"])
def delete_file(filename):
    if ".." in filename or "/" in filename or "\\" in filename:
        return jsonify({"error": "Invalid filename"}), 400
    file_path = os.path.join(DOWNLOAD_FOLDER, filename)
    if not os.path.exists(file_path):
        return jsonify({"error": "File not found"}), 404
    if not os.path.realpath(file_path).startswith(os.path.realpath(DOWNLOAD_FOLDER)):
        return jsonify({"error": "Access denied"}), 403
    os.remove(file_path)
    return jsonify({"message": "File deleted successfully"})


@app.route("/api/stats")
def get_stats():
    try:
        total_files = 0
        total_size = 0
        for fp in glob.glob(os.path.join(DOWNLOAD_FOLDER, "*")):
            if os.path.isfile(fp) and not os.path.basename(fp).endswith(".part"):
                total_files += 1
                total_size += os.path.getsize(fp)

        active = len([
            d for d in download_manager.active_downloads.values()
            if d.get("status") == "downloading"
        ])

        # Get RL stats
        rl_stats = adapter.get_stats()
        
        # Calculate total speed
        total_speed = 0
        for download in download_manager.active_downloads.values():
            if download.get("status") == "downloading":
                total_speed += download.get("speed", 0)

        return jsonify({
            "total_files": total_files,
            "total_size": total_size,
            "total_size_mb": total_size / (1024 * 1024),
            "total_size_gb": total_size / (1024 * 1024 * 1024),
            "active_downloads": active,
            "total_speed": total_speed,
            "download_folder": DOWNLOAD_FOLDER,
            "rl_q_table_size": rl_stats.get("q_table_size", 0),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/stats/history")
def get_stats_history():
    """Get historical statistics for charts."""
    try:
        # This would normally come from a database
        # For now, return mock data
        import random
        import time
        
        history = []
        for i in range(24):  # Last 24 hours
            history.append({
                "timestamp": time.time() - (i * 3600),
                "speed": random.uniform(1, 8),
                "active": random.randint(0, 5),
            })
        return jsonify(history)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# Desktop + Web Mode Support
# ---------------------------------------------------------------------------

def run_flask():
    os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)
    app.run(
        debug=False,
        host="127.0.0.1",
        port=5000,
        use_reloader=False
    )

def on_closed():
    try:
        adapter.save()
    except Exception:
        pass
    os._exit(0)

if __name__ == "__main__":

    os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

    print("=" * 60)
    print("TurboLane Download Manager starting...")
    print(f"Download folder: {DOWNLOAD_FOLDER}")
    print("Server: http://127.0.0.1:5000")
    print("=" * 60)

    # Desktop mode (PyInstaller EXE)
    if hasattr(sys, "_MEIPASS"):
        print("Running in DESKTOP mode")
        print("Creating window...")

        flask_thread = threading.Thread(target=run_flask)
        flask_thread.daemon = True
        flask_thread.start()

        time.sleep(2)  # Wait for Flask to start
        
        # Create window with native look
        webview.create_window(
            "TurboLane Download Manager(v1.0.1)",
            "http://127.0.0.1:5000",
            width=1200,
            height=800,
            resizable=True,
            confirm_close=True,
            text_select=True,
        )

        webview.start(gui="winforms")  # Use winforms for native Windows look

    else:
        print("Running in WEB mode")
        print(f"Open browser to: http://127.0.0.1:5000")
        run_flask()