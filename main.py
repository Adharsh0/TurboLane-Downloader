# main.py - GUI Application with metrics display and download mode selection

import tkinter as tk
from tkinter import ttk, messagebox, filedialog, scrolledtext
import threading
from downloader import MultiStreamDownloader
from simple_downloader import SimpleDownloader
from config import *
import os

class DownloaderGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Multi-Stream Downloader")
        self.root.geometry("750x650")
        self.root.resizable(False, False)
        
        self.downloader = None
        self.download_thread = None
        
        self.setup_ui()
    
    def setup_ui(self):
        """Create all GUI elements."""
        
        # Title
        title_label = tk.Label(
            self.root, 
            text="Multi-Stream File Downloader",
            font=("Arial", 16, "bold")
        )
        title_label.pack(pady=15)
        
        # URL Input Frame
        url_frame = tk.Frame(self.root)
        url_frame.pack(pady=10, padx=20, fill='x')
        
        tk.Label(url_frame, text="URL:", font=("Arial", 10)).pack(side='left')
        
        self.url_entry = tk.Entry(url_frame, font=("Arial", 10))
        self.url_entry.pack(side='left', padx=10, fill='x', expand=True)
        
        # Download Mode Selection Frame
        mode_frame = tk.Frame(self.root)
        mode_frame.pack(pady=10, padx=20, fill='x')
        
        tk.Label(mode_frame, text="Download Mode:", font=("Arial", 10, "bold")).pack(side='left')
        
        self.mode_var = tk.StringVar(value="multi")
        tk.Radiobutton(
            mode_frame,
            text="Multi-Stream (Parallel)",
            variable=self.mode_var,
            value="multi",
            font=("Arial", 10),
            command=self.toggle_streams_control
        ).pack(side='left', padx=10)
        
        tk.Radiobutton(
            mode_frame,
            text="Single-Stream (Browser-style)",
            variable=self.mode_var,
            value="single",
            font=("Arial", 10),
            command=self.toggle_streams_control
        ).pack(side='left', padx=10)
        
        # Streams Selection Frame
        streams_frame = tk.Frame(self.root)
        streams_frame.pack(pady=10, padx=20, fill='x')
        
        tk.Label(streams_frame, text="Number of Streams:", font=("Arial", 10)).pack(side='left')
        
        self.streams_var = tk.IntVar(value=DEFAULT_NUM_STREAMS)
        self.streams_spinbox = tk.Spinbox(
            streams_frame,
            from_=MIN_STREAMS,
            to=MAX_STREAMS,
            textvariable=self.streams_var,
            width=10,
            font=("Arial", 10)
        )
        self.streams_spinbox.pack(side='left', padx=10)
        
        # Progress Frame
        progress_frame = tk.LabelFrame(self.root, text="Download Progress", font=("Arial", 10))
        progress_frame.pack(pady=15, padx=20, fill='both', expand=True)
        
        # Progress Bar
        self.progress_bar = ttk.Progressbar(
            progress_frame,
            mode='determinate',
            length=600
        )
        self.progress_bar.pack(pady=10, padx=20)
        
        # Status Labels
        self.status_label = tk.Label(
            progress_frame,
            text="Ready to download",
            font=("Arial", 10)
        )
        self.status_label.pack(pady=5)
        
        self.speed_label = tk.Label(
            progress_frame,
            text="Speed: 0 MB/s",
            font=("Arial", 9)
        )
        self.speed_label.pack(pady=5)
        
        # Metrics Display
        metrics_label = tk.Label(
            progress_frame,
            text="Download Metrics:",
            font=("Arial", 10, "bold")
        )
        metrics_label.pack(pady=(10, 5))
        
        self.metrics_text = scrolledtext.ScrolledText(
            progress_frame,
            height=10,
            width=85,
            font=("Courier", 9),
            state='disabled',
            bg="#f5f5f5"
        )
        self.metrics_text.pack(pady=5, padx=20)
        
        # Buttons Frame
        button_frame = tk.Frame(self.root)
        button_frame.pack(pady=15)
        
        self.download_btn = tk.Button(
            button_frame,
            text="Start Download",
            command=self.start_download,
            font=("Arial", 11, "bold"),
            bg="#4CAF50",
            fg="white",
            width=15,
            height=2,
            cursor="hand2"
        )
        self.download_btn.pack(side='left', padx=10)
        
        self.cancel_btn = tk.Button(
            button_frame,
            text="Cancel",
            command=self.cancel_download,
            font=("Arial", 11),
            bg="#f44336",
            fg="white",
            width=15,
            height=2,
            state='disabled',
            cursor="hand2"
        )
        self.cancel_btn.pack(side='left', padx=10)
    
    def toggle_streams_control(self):
        """Enable/disable streams spinbox based on selected mode."""
        if self.mode_var.get() == "single":
            self.streams_spinbox.config(state='disabled')
        else:
            self.streams_spinbox.config(state='normal')
    
    def progress_callback(self, downloaded, total):
        """Update progress bar and labels."""
        progress = (downloaded / total) * 100
        self.progress_bar['value'] = progress
        
        downloaded_mb = downloaded / (1024 * 1024)
        total_mb = total / (1024 * 1024)
        
        self.status_label.config(
            text=f"Downloaded: {downloaded_mb:.2f} MB / {total_mb:.2f} MB ({progress:.1f}%)"
        )
        
        if self.downloader:
            speed = self.downloader.get_speed()
            self.speed_label.config(text=f"Speed: {speed:.2f} MB/s")
    
    def display_metrics(self, metrics):
        """Display metrics in the text widget."""
        self.metrics_text.config(state='normal')
        self.metrics_text.delete(1.0, tk.END)
        
        # Check if it's single-stream metrics (different structure)
        if 'num_streams_used' in metrics:
            # Multi-stream metrics
            report = f"""{'='*70}
DOWNLOAD COMPLETE - PERFORMANCE METRICS
{'='*70}

OVERALL PERFORMANCE:
  Total Time:        {metrics['total_time_seconds']:.2f} seconds
  File Size:         {metrics['total_size_mb']:.2f} MB
  Streams Used:      {metrics['num_streams_used']}

THROUGHPUT:
  Overall:           {metrics['throughput_mbps']:.2f} Mbps ({metrics['throughput_MBps']:.2f} MB/s)
  Per Stream Avg:    {metrics['average_speed_per_stream']:.2f} MB/s

"""
            
            if metrics['chunk_metrics']:
                report += f"""STREAM PERFORMANCE:
  {'Stream':<8} {'Size (MB)':<12} {'Time (s)':<12} {'Speed (MB/s)':<12}
  {'-'*50}
"""
                for chunk in metrics['chunk_metrics']:
                    report += f"  {chunk['chunk_id']:<8} {chunk['size_mb']:<12.2f} {chunk['time_seconds']:<12.2f} {chunk['speed_mbps']:<12.2f}\n"
                
                if metrics['fastest_chunk'] and metrics['slowest_chunk']:
                    report += f"""
STREAM STATISTICS:
  Fastest:  Stream #{metrics['fastest_chunk']['chunk_id']} at {metrics['fastest_chunk']['speed_mbps']:.2f} MB/s
  Slowest:  Stream #{metrics['slowest_chunk']['chunk_id']} at {metrics['slowest_chunk']['speed_mbps']:.2f} MB/s
"""
        else:
            # Single-stream metrics (from simple_downloader)
            report = f"""{'='*70}
DOWNLOAD COMPLETE - PERFORMANCE METRICS
{'='*70}

OVERALL PERFORMANCE:
  Total Time:        {metrics['total_time']:.2f} seconds
  File Size:         {metrics['file_size_mb']:.2f} MB
  Download Mode:     Single-Stream (Browser-style)

THROUGHPUT:
  Overall:           {metrics['throughput_mbps']:.2f} Mbps ({metrics['throughput_MBps']:.2f} MB/s)
"""
        
        report += f"\n{'='*70}"
        
        self.metrics_text.insert(1.0, report)
        self.metrics_text.config(state='disabled')
    
    def download_file(self):
        """Thread function to handle download."""
        result = self.downloader.download()
        
        # Update UI in main thread
        self.root.after(0, self.download_complete, result)
    
    def download_complete(self, result):
        """Called when download finishes."""
        self.download_btn.config(state='normal')
        self.cancel_btn.config(state='disabled')
        self.streams_spinbox.config(state='normal' if self.mode_var.get() == 'multi' else 'disabled')
        
        if result:
            # Get and display metrics
            metrics = self.downloader.get_detailed_metrics()
            if metrics:
                self.display_metrics(metrics)
            
            messagebox.showinfo(
                "Success", 
                f"File downloaded successfully!\n\n"
                f"Saved to:\n{result}"
            )
            self.status_label.config(text="Download completed!")
        else:
            self.status_label.config(text="Download failed or cancelled")
    
    def start_download(self):
        """Start the download process."""
        url = self.url_entry.get().strip()
        
        if not url:
            messagebox.showerror("Error", "Please enter a URL")
            return
        
        if not url.startswith(('http://', 'https://')):
            messagebox.showerror("Error", "URL must start with http:// or https://")
            return
        
        # Get selected mode
        mode = self.mode_var.get()
        num_streams = self.streams_var.get()
        
        # Disable controls during download
        self.download_btn.config(state='disabled')
        self.cancel_btn.config(state='normal')
        self.streams_spinbox.config(state='disabled')
        
        # Reset progress and metrics
        self.progress_bar['value'] = 0
        self.status_label.config(text="Starting download...")
        self.speed_label.config(text="Speed: 0 MB/s")
        self.metrics_text.config(state='normal')
        self.metrics_text.delete(1.0, tk.END)
        self.metrics_text.insert(1.0, f"Download in progress ({mode}-stream mode)...\nMetrics will appear after completion.")
        self.metrics_text.config(state='disabled')
        
        # Create downloader based on selected mode
        if mode == "single":
            self.downloader = SimpleDownloader(
                url,
                progress_callback=self.progress_callback
            )
        else:
            self.downloader = MultiStreamDownloader(
                url,
                num_streams=num_streams,
                progress_callback=self.progress_callback
            )
        
        # Start download in separate thread
        self.download_thread = threading.Thread(target=self.download_file)
        self.download_thread.start()
    
    def cancel_download(self):
        """Cancel the current download."""
        if self.downloader:
            self.downloader.cancel()
            self.status_label.config(text="Cancelling...")

def main():
    root = tk.Tk()
    app = DownloaderGUI(root)
    root.mainloop()

if __name__ == "__main__":
    main()