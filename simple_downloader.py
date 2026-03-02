# simple_downloader.py - Simple single-stream downloader (browser-style)

import os
import requests
from urllib.parse import urlparse, unquote
import time
from config import DOWNLOAD_FOLDER, CONNECTION_TIMEOUT, READ_TIMEOUT, BUFFER_SIZE

class SimpleDownloader:
    """
    Single-stream downloader using standard browser method.
    No parallel streams - just like Chrome/Edge default behavior.
    """
    
    def __init__(self, url, progress_callback=None):
        """
        Initialize the simple downloader.
        
        Args:
            url: The URL to download from
            progress_callback: Function to call with progress updates
        """
        self.url = url
        self.progress_callback = progress_callback
        self.downloaded_bytes = 0
        self.file_size = 0
        self.is_downloading = False
        self.start_time = None
    
    def get_filename_from_url(self):
        """Extract filename from URL."""
        path = urlparse(self.url).path
        filename = unquote(os.path.basename(path))
        
        if not filename or filename == '/':
            filename = 'downloaded_file'
        
        return filename
    
    def get_file_info(self):
        """Get file size and name from server."""
        try:
            response = requests.head(
                self.url,
                timeout=CONNECTION_TIMEOUT,
                allow_redirects=True
            )
            
            file_size = int(response.headers.get('Content-Length', 0))
            
            # Get filename
            content_disposition = response.headers.get('Content-Disposition', '')
            if 'filename=' in content_disposition:
                filename = content_disposition.split('filename=')[1].strip('"')
            else:
                filename = self.get_filename_from_url()
            
            return file_size, filename
            
        except:
            # If HEAD fails, try GET with stream
            response = requests.get(
                self.url,
                timeout=CONNECTION_TIMEOUT,
                allow_redirects=True,
                stream=True
            )
            
            file_size = int(response.headers.get('Content-Length', 0))
            
            content_disposition = response.headers.get('Content-Disposition', '')
            if 'filename=' in content_disposition:
                filename = content_disposition.split('filename=')[1].strip('"')
            else:
                filename = self.get_filename_from_url()
            
            response.close()
            return file_size, filename
    
    def download(self, output_path=None):
        """
        Download file using simple single-stream method (browser-style).
        
        Args:
            output_path: Where to save the file
            
        Returns:
            Path to downloaded file on success, None on failure
        """
        try:
            print("Checking file information...")
            file_size, filename = self.get_file_info()
            self.file_size = file_size
            
            print(f"File: {filename}")
            print(f"File size: {file_size / (1024*1024):.2f} MB")
            
            if file_size == 0:
                print("WARNING: File size unknown, proceeding anyway...")
            
            # Setup output path
            if output_path is None:
                output_path = os.path.join(DOWNLOAD_FOLDER, filename)
            
            print(f"Output path: {output_path}")
            print("\nStarting download (single stream - browser method)...")
            
            # Start download
            self.is_downloading = True
            self.downloaded_bytes = 0
            self.start_time = time.time()
            
            # Simple GET request - no range, just stream the whole file
            response = requests.get(
                self.url,
                stream=True,
                timeout=(CONNECTION_TIMEOUT, READ_TIMEOUT),
                allow_redirects=True
            )
            
            if response.status_code != 200:
                raise Exception(f"Server returned status code: {response.status_code}")
            
            # Download and write to file
            with open(output_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=BUFFER_SIZE):
                    if not self.is_downloading:
                        print("Download cancelled")
                        break
                    
                    if chunk:
                        f.write(chunk)
                        self.downloaded_bytes += len(chunk)
                        
                        # Update progress
                        if self.progress_callback and file_size > 0:
                            self.progress_callback(self.downloaded_bytes, file_size)
            
            # Calculate metrics
            total_time = time.time() - self.start_time
            
            if self.is_downloading:
                # Verify file
                final_size = os.path.getsize(output_path)
                print(f"\nDownload complete!")
                print(f"Final file size: {final_size / (1024*1024):.2f} MB")
                
                # Print metrics
                self.print_metrics(total_time, final_size)
                self.export_metrics(output_path, total_time, final_size)
                
                return output_path
            else:
                # Clean up if cancelled
                if os.path.exists(output_path):
                    os.remove(output_path)
                return None
                
        except Exception as e:
            print(f"\nDownload failed: {str(e)}")
            import traceback
            traceback.print_exc()
            
            # Clean up partial file
            if output_path and os.path.exists(output_path):
                try:
                    os.remove(output_path)
                except:
                    pass
            
            return None
    
    def print_metrics(self, total_time, file_size):
        """Print download metrics."""
        throughput_mbps = (file_size * 8) / (total_time * 1024 * 1024) if total_time > 0 else 0
        throughput_MBps = file_size / (total_time * 1024 * 1024) if total_time > 0 else 0
        
        print("\n" + "="*60)
        print("DOWNLOAD METRICS REPORT (SINGLE STREAM)")
        print("="*60)
        print(f"Total Download Time: {total_time:.2f} seconds")
        print(f"File Size: {file_size / (1024*1024):.2f} MB")
        print(f"Method: Single Stream (Browser-style)")
        print(f"\nThroughput:")
        print(f"  - {throughput_mbps:.2f} Mbps")
        print(f"  - {throughput_MBps:.2f} MB/s")
        print("="*60 + "\n")
    
    def export_metrics(self, output_path, total_time, file_size):
        """Export metrics to file."""
        metrics_file = output_path + "_simple_metrics.txt"
        
        throughput_mbps = (file_size * 8) / (total_time * 1024 * 1024) if total_time > 0 else 0
        throughput_MBps = file_size / (total_time * 1024 * 1024) if total_time > 0 else 0
        
        with open(metrics_file, 'w') as f:
            f.write("SIMPLE SINGLE-STREAM DOWNLOAD METRICS\n")
            f.write("="*60 + "\n\n")
            f.write(f"Download URL: {self.url}\n")
            f.write(f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            f.write(f"Total Time: {total_time:.2f} seconds\n")
            f.write(f"File Size: {file_size / (1024*1024):.2f} MB\n")
            f.write(f"Method: Single Stream (Browser-style)\n\n")
            f.write(f"Throughput: {throughput_mbps:.2f} Mbps\n")
            f.write(f"Throughput: {throughput_MBps:.2f} MB/s\n")
        
        print(f"Metrics exported to: {metrics_file}")
    
    def cancel(self):
        """Cancel the download."""
        print("Cancelling download...")
        self.is_downloading = False
    
    def get_speed(self):
        """Calculate current download speed in MB/s."""
        if self.start_time and self.downloaded_bytes > 0:
            elapsed = time.time() - self.start_time
            return (self.downloaded_bytes / (1024 * 1024)) / elapsed if elapsed > 0 else 0
        return 0

    def get_detailed_metrics(self):
        """
        Calculate detailed download metrics for Flask integration.
        Returns dictionary with all metrics.
        """
        if not self.start_time:
            return None
        
        total_time = time.time() - self.start_time
        
        # Calculate throughput
        throughput_mbps = (self.file_size * 8) / (total_time * 1024 * 1024) if total_time > 0 else 0
        throughput_MBps = self.file_size / (total_time * 1024 * 1024) if total_time > 0 else 0
        
        return {
            'total_time': total_time,
            'file_size_mb': self.file_size / (1024 * 1024),
            'throughput_mbps': throughput_mbps,
            'throughput_MBps': throughput_MBps
        }