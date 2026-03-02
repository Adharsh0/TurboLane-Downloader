// app.js — TurboLane Desktop UI
class DownloadManager {
    constructor() {
        this.activeDownloads = new Map();
        this.updateInterval  = null;
        this.currentPage     = 'downloads';
        this.init();
    }

    init() {
        // Navigation
        document.querySelectorAll('.nav-item[data-page]').forEach(el => {
            el.addEventListener('click', () => this.switchPage(el.dataset.page));
        });

        // Toolbar buttons
        document.getElementById('startDownloadBtn').addEventListener('click', () => {
            document.getElementById('url').focus();
        });
        document.getElementById('submitDownloadBtn').addEventListener('click', () => this.startDownload());
        document.getElementById('url').addEventListener('keydown', e => {
            if (e.key === 'Enter') this.startDownload();
        });
        document.getElementById('clearCompletedBtn').addEventListener('click', () => this.clearCompleted());
        document.getElementById('optimizeAllBtn').addEventListener('click', () => {
            this.toast('RL optimization applied to active downloads', 'info');
        });
        document.getElementById('refreshFiles').addEventListener('click', () => this.loadFiles());
        document.getElementById('openDownloadsFolder').addEventListener('click', () => this.switchPage('files'));
        document.getElementById('refreshDashboard').addEventListener('click', () => this.loadDashboard());
        document.getElementById('showRlStats').addEventListener('click', () => this.switchPage('dashboard'));
        document.getElementById('resetRlLearning').addEventListener('click', () => this.resetRL());

        // Mode change
        document.querySelectorAll('input[name="mode"]').forEach(r => {
            r.addEventListener('change', () => this.onModeChange());
        });

        // RL toggle disables manual streams
        document.getElementById('rlMode').addEventListener('change', () => this.onModeChange());

        this.onModeChange();
        this.startPolling();
        this.loadFiles();
        this.loadRLStats();
    }

    // ── PAGE SWITCHING ────────────────────────────────────────
    switchPage(page) {
        document.querySelectorAll('.nav-item[data-page]').forEach(el =>
            el.classList.toggle('active', el.dataset.page === page));
        document.querySelectorAll('.page').forEach(el =>
            el.classList.toggle('active', el.id === `${page}-page`));
        this.currentPage = page;
        if (page === 'files')     this.loadFiles();
        if (page === 'dashboard') this.loadDashboard();
    }

    // ── FORM OPTIONS ─────────────────────────────────────────
    onModeChange() {
        const multi = document.getElementById('modeMulti').checked;
        const rl    = document.getElementById('rlMode').checked;
        const streamsInput = document.getElementById('numStreams');
        document.getElementById('streamsGroup').style.display = multi ? 'flex' : 'none';
        streamsInput.disabled = !multi || rl;
    }

    // ── START DOWNLOAD ────────────────────────────────────────
    async startDownload() {
        const url     = document.getElementById('url').value.trim();
        const mode    = document.querySelector('input[name="mode"]:checked').value;
        const streams = parseInt(document.getElementById('numStreams').value) || 8;
        const useRL   = document.getElementById('rlMode').checked;

        if (!url) { this.toast('Please enter a URL', 'error'); return; }
        if (!url.startsWith('http://') && !url.startsWith('https://')) {
            this.toast('URL must start with http:// or https://', 'error'); return;
        }

        try {
            const res = await fetch('/api/downloads', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url, mode, num_streams: streams, use_rl: useRL })
            });
            const data = await res.json();
            if (res.ok) {
                document.getElementById('url').value = '';
                this.toast('Download started' + (useRL ? ' (AI mode)' : ''), 'success');
                this.addDownloadItem(data.download_id, url, mode, useRL);
                this.loadDashboard();
            } else {
                this.toast('Error: ' + (data.error || 'Unknown error'), 'error');
            }
        } catch (e) {
            this.toast('Network error: ' + e.message, 'error');
        }
    }

    // ── ADD DOWNLOAD ITEM TO UI ───────────────────────────────
    addDownloadItem(downloadId, url, mode, useRL) {
        document.getElementById('noActiveDownloads')?.remove();

        const tmpl  = document.getElementById('downloadItemTemplate');
        const clone = tmpl.content.cloneNode(true);
        const item  = clone.querySelector('.download-item');

        item.dataset.downloadId = downloadId;
        item.classList.add('downloading');

        const filename = this.getFilenameFromUrl(url);
        item.querySelector('.dl-filename').textContent = filename;
        item.querySelector('.dl-urltext').textContent  = url;

        if (useRL) {
            const tag = document.createElement('span');
            tag.className   = 'dl-tag rl';
            tag.textContent = 'AI';
            item.querySelector('.dl-meta').prepend(tag);
        }

        // Hide stream count for single mode
        if (mode === 'single') {
            const streamsSpan = item.querySelector('.dl-streams');
            if (streamsSpan) streamsSpan.style.display = 'none';
        }

        item.querySelector('.cancel-download').addEventListener('click', () =>
            this.cancelDownload(downloadId));
        item.querySelector('.view-metrics').addEventListener('click', () =>
            this.viewMetrics(downloadId));

        document.getElementById('activeDownloads').appendChild(clone);

        this.activeDownloads.set(downloadId, {
            url, mode, useRL,
            status: 'downloading',
            lastStreamCount: null,
            decisionHistory: [],
            filename: filename
        });
        this.updateCounts();
    }

    // ── POLLING ───────────────────────────────────────────────
    startPolling() {
        this.updateInterval = setInterval(() => this.poll(), 1000);
    }

    async poll() {
        let totalSpeed = 0;

        for (const [id, info] of this.activeDownloads) {
            if (['completed', 'failed', 'cancelled'].includes(info.status)) continue;
            try {
                const res  = await fetch(`/api/downloads/${id}`);
                if (!res.ok) continue;
                const data = await res.json();
                this.updateDownloadUI(id, data, info);
                if (data.speed) totalSpeed += data.speed;
            } catch (_) {}
        }

        this.updateStatusBar(totalSpeed);
        if (Math.random() < 0.15) this.loadRLStats();
    }

    // ── UPDATE SINGLE DOWNLOAD ────────────────────────────────
    updateDownloadUI(id, data, info) {
        const item = document.querySelector(`.download-item[data-download-id="${id}"]`);
        if (!item) return;

        const status = data.status || 'downloading';
        const pct    = Math.round(data.progress || 0);
        const speed  = data.speed || 0; // Speed in MB/s from backend
        const streams = data.stream_count || data.current_streams;
        const totalSize = data.total_size || 0;
        const downloadedSize = data.downloaded_size || 0;

        // Progress bar
        const bar = item.querySelector('.dl-bar');
        bar.style.width = `${pct}%`;
        bar.className = 'dl-bar' + (['completed','failed','cancelled'].includes(status) ? ` ${status}` : '');

        item.querySelector('.dl-pct').textContent = `${pct}%`;
        
        // FIXED: Display speed in MB/s (no conversion)
        if (speed > 0) {
            item.querySelector('.dl-spd').textContent = this.fmtSpeedMBps(speed);
        } else {
            item.querySelector('.dl-spd').textContent = '—';
        }

        // Update file size display
        const sizeElement = item.querySelector('.dl-size-value');
        if (sizeElement) {
            if (totalSize > 0) {
                if (downloadedSize > 0 && downloadedSize < totalSize) {
                    sizeElement.textContent = `${this.formatFileSize(downloadedSize)} / ${this.formatFileSize(totalSize)}`;
                } else {
                    sizeElement.textContent = this.formatFileSize(totalSize);
                }
            }
        }

        // Update stream count display
        const streamsElement = item.querySelector('.dl-streams-value');
        if (streamsElement && streams) {
            streamsElement.textContent = streams;
            
            // Show/hide based on mode
            const streamsSpan = item.querySelector('.dl-streams');
            if (streamsSpan) {
                streamsSpan.style.display = info.mode === 'single' ? 'none' : 'inline-flex';
            }
            
            // Add visual indicator for stream changes
            if (info.lastStreamCount && info.lastStreamCount !== streams) {
                streamsElement.style.color = 'var(--green)';
                setTimeout(() => {
                    streamsElement.style.color = '';
                }, 1000);
            }
        }

        // Status line
        const statusIcon = item.querySelector('.dl-status-icon');
        const statusText = item.querySelector('.dl-status-text');
        const iconMap = {
            downloading: '<i class="fas fa-spinner fa-spin"></i>',
            assembling:  '<i class="fas fa-cog fa-spin" style="color:var(--yellow)"></i>',
            completed:   '<i class="fas fa-check" style="color:var(--green)"></i>',
            failed:      '<i class="fas fa-times" style="color:var(--red)"></i>',
            cancelled:   '<i class="fas fa-ban" style="color:var(--yellow)"></i>',
        };
        statusIcon.innerHTML = iconMap[status] || iconMap.downloading;
        statusText.textContent = data.status_message || status.charAt(0).toUpperCase() + status.slice(1);

        // Border colour
        item.className = `download-item ${status}`;

        // RL row
        if (info.useRL && streams) {
            let rlRow = item.querySelector('.dl-rl-row');
            if (!rlRow) {
                rlRow = document.createElement('div');
                rlRow.className = 'dl-rl-row';
                item.appendChild(rlRow);
            }
            const changed = info.lastStreamCount !== null && info.lastStreamCount !== streams;
            let decision = streams > (info.lastStreamCount||streams) ? `↑ ${streams}` :
                           streams < (info.lastStreamCount||streams) ? `↓ ${streams}` : `= ${streams}`;
            if (changed) {
                info.decisionHistory.unshift(`${new Date().toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',second:'2-digit'})}: ${decision} streams`);
                if (info.decisionHistory.length > 3) info.decisionHistory.pop();
            }
            info.lastStreamCount = streams;

            rlRow.innerHTML = `
                <i class="fas fa-robot"></i>
                <span>AI-managed</span>
                <span class="dl-rl-streams">${streams} streams</span>
                <span class="dl-rl-history">${info.decisionHistory[0] || ''}</span>
            `;
        }

        // Move to history on terminal state
        if (['completed','failed','cancelled'].includes(status) && info.status === 'downloading') {
            info.status = status;
            this.activeDownloads.set(id, info);
            setTimeout(() => this.moveToHistory(id, data), 2000);
            if (status === 'completed') {
                this.toast(`✓ ${info.filename || this.getFilenameFromUrl(info.url)}`, 'success');
                this.loadFiles();
            }
        }
    }

    moveToHistory(id, data) {
        const item = document.querySelector(`.download-item[data-download-id="${id}"]`);
        if (!item) return;

        const hist = document.getElementById('downloadHistory');
        hist.querySelector('.empty-state')?.remove();

        // Remove cancel btn, add open btn if completed
        item.querySelector('.cancel-download')?.remove();
        if (data.status === 'completed' && data.filename) {
            const openBtn = document.createElement('button');
            openBtn.className = 'dl-btn';
            openBtn.title = 'Open file';
            openBtn.innerHTML = '<i class="fas fa-folder-open"></i>';
            openBtn.onclick = () => window.open(`/downloads/${data.filename}`, '_blank');
            item.querySelector('.dl-actions').prepend(openBtn);
        }

        hist.prepend(item);

        const active = document.getElementById('activeDownloads');
        if (active.children.length === 0) {
            active.innerHTML = `<div class="empty-state" id="noActiveDownloads">
                <i class="fas fa-cloud-download-alt"></i><p>No active downloads</p></div>`;
        }
        this.updateCounts();
    }

    async cancelDownload(id) {
        try {
            await fetch(`/api/downloads/${id}/cancel`, { method: 'POST' });
            this.toast('Download cancelled', 'warning');
        } catch (_) { this.toast('Cancel failed', 'error'); }
    }

    async viewMetrics(id) {
        try {
            const res = await fetch(`/api/downloads/${id}/metrics`);
            if (res.ok) {
                const m = await res.json();
                this.switchPage('dashboard');
                this.toast(`Metrics loaded for download ${id}`, 'info');
            } else {
                this.toast('Metrics not available yet', 'warning');
            }
        } catch (_) { this.toast('Error fetching metrics', 'error'); }
    }

    clearCompleted() {
        document.querySelectorAll('#downloadHistory .download-item').forEach(el => el.remove());
        const h = document.getElementById('downloadHistory');
        if (!h.querySelector('.download-item')) {
            h.innerHTML = `<div class="empty-state"><i class="fas fa-clock"></i><p>No history yet</p></div>`;
        }
    }

    // ── RL STATS ──────────────────────────────────────────────
    async loadRLStats() {
        try {
            const res = await fetch('/api/rl/stats');
            if (!res.ok) return;
            const s = await res.json();

            // Sidebar detail
            const detail = document.getElementById('rlSidebarDetail');
            if (detail) detail.textContent = `${s.q_table_size || 0} states · ε=${((s.exploration_rate||0)*100).toFixed(1)}%`;

            // Status bar
            document.getElementById('sbRLText').textContent = `RL: ${s.q_table_size || 0} states`;

            if (this.currentPage === 'dashboard') this.renderRLStats(s);
        } catch (_) {}
    }

    renderRLStats(s) {
        const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
        set('qTableSize',      s.q_table_size   != null ? s.q_table_size : '—');
        set('explorationRate', s.exploration_rate != null ? `${(s.exploration_rate*100).toFixed(1)}%` : '—');
        set('avgReward',       s.average_reward   != null ? s.average_reward.toFixed(3) : '—');
        set('totalDecisions',  s.total_decisions  != null ? s.total_decisions : '—');
        set('rlOptimized',     s.total_decisions  || 0);

        const pct  = Math.min(100, ((s.q_table_size||0) / 120) * 100);
        const fill = document.getElementById('rlProgressFill');
        const lbl  = document.getElementById('rlLearningProgress');
        if (fill) fill.style.width = `${pct}%`;
        if (lbl)  lbl.textContent  = `${Math.round(pct)}%`;
    }

    async resetRL() {
        if (!confirm('Reset all RL learning data? This clears the Q-table.')) return;
        try {
            const res = await fetch('/api/rl/reset', { method: 'POST' });
            if (res.ok) {
                this.toast('RL learning data reset', 'info');
                this.loadRLStats();
            } else {
                this.toast('Reset failed', 'error');
            }
        } catch (_) { this.toast('Reset failed', 'error'); }
    }

    // ── DASHBOARD ─────────────────────────────────────────────
    async loadDashboard() {
        this.loadRLStats();
        try {
            const res = await fetch('/api/stats');
            if (!res.ok) return;
            const s = await res.json();
            const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
            set('totalDownloads', s.total_files || 0);
            const gb = ((s.total_size_mb || 0) / 1024).toFixed(2);
            set('totalData', gb < 1 ? `${Math.round(s.total_size_mb || 0)} MB` : `${gb} GB`);
            const active = Array.from(this.activeDownloads.values()).filter(d => d.status === 'downloading').length;
            set('activeDownloadsCount', active);
        } catch (_) {}
    }

    // ── FILES ─────────────────────────────────────────────────
    async loadFiles() {
        try {
            const res = await fetch('/api/files');
            if (!res.ok) return;
            const files = await res.json();
            this.renderFiles(files);
        } catch (_) {}
    }

    renderFiles(files) {
        const container = document.getElementById('fileManager');
        const countEl   = document.getElementById('fileCount');
        const navCount  = document.getElementById('navFileCount');
        if (countEl)  countEl.textContent  = files.length;
        if (navCount) navCount.textContent = files.length;

        if (files.length === 0) {
            container.innerHTML = `<div class="empty-state"><i class="fas fa-folder-open"></i><p>No downloaded files</p></div>`;
            return;
        }

        container.innerHTML = '';
        files.forEach(file => {
            const tmpl  = document.getElementById('fileItemTemplate');
            const clone = tmpl.content.cloneNode(true);
            const item  = clone.querySelector('.file-item');

            const ext = file.name.split('.').pop().toLowerCase();
            const iconMap = {
                zip:'fa-file-zipper', rar:'fa-file-zipper', '7z':'fa-file-zipper',
                pdf:'fa-file-pdf',
                jpg:'fa-file-image', jpeg:'fa-file-image', png:'fa-file-image', gif:'fa-file-image', webp:'fa-file-image',
                mp4:'fa-file-video', avi:'fa-file-video', mov:'fa-file-video', mkv:'fa-file-video',
                mp3:'fa-file-audio', wav:'fa-file-audio', flac:'fa-file-audio',
                doc:'fa-file-word',  docx:'fa-file-word',
                xls:'fa-file-excel', xlsx:'fa-file-excel',
                exe:'fa-gear', msi:'fa-gear',
                txt:'fa-file-lines',
            };
            const iconClass = iconMap[ext] || 'fa-file';
            item.querySelector('.file-icon-wrap').innerHTML = `<i class="fas ${iconClass}"></i>`;
            item.querySelector('.file-name').textContent = file.name;

            const sizeMB = (file.size / (1024 * 1024)).toFixed(2);
            const date   = new Date(file.modified * 1000).toLocaleDateString();
            item.querySelector('.file-meta').textContent = `${sizeMB} MB · ${date}`;

            item.querySelector('.open-file').addEventListener('click', () =>
                window.open(`/downloads/${file.name}`, '_blank'));
            item.querySelector('.download-file').addEventListener('click', () =>
                window.open(`/downloads/${file.name}`, '_blank'));
            item.querySelector('.delete-file').addEventListener('click', () =>
                this.deleteFile(file.name));

            container.appendChild(clone);
        });
    }

    async deleteFile(filename) {
        if (!confirm(`Delete "${filename}"?`)) return;
        try {
            const res = await fetch(`/api/files/${filename}`, { method: 'DELETE' });
            if (res.ok) {
                this.toast(`Deleted ${filename}`, 'success');
                this.loadFiles();
                this.loadDashboard();
            } else {
                const d = await res.json();
                this.toast('Error: ' + (d.error || 'Unknown'), 'error');
            }
        } catch (_) { this.toast('Delete failed', 'error'); }
    }

    // ── STATUS BAR ────────────────────────────────────────────
    updateStatusBar(totalSpeedMBps) {
        const active = Array.from(this.activeDownloads.values()).filter(d => d.status === 'downloading');
        const sbStatus = document.getElementById('sbStatus');
        sbStatus.innerHTML = active.length > 0
            ? `<i class="fas fa-circle" style="color:var(--green)"></i> ${active.length} downloading`
            : `<i class="fas fa-circle" style="color:var(--text3)"></i> Idle`;

        // Show total speed in MB/s (remove the * 8 conversion)
        document.getElementById('sbSpeedText').textContent =
            totalSpeedMBps > 0 ? this.fmtSpeedMBps(totalSpeedMBps) : '0 MB/s';
        
        // Update total streams in status bar
        const totalStreams = Array.from(this.activeDownloads.values())
            .filter(d => d.status === 'downloading' && d.mode !== 'single')
            .reduce((sum, d) => sum + (d.lastStreamCount || 0), 0);
        document.getElementById('sbStreams').innerHTML = totalStreams > 0 ? 
            `<i class="fas fa-network-wired"></i> ${totalStreams} total streams` : '';
    }

    updateCounts() {
        const n = Array.from(this.activeDownloads.values()).filter(d => d.status === 'downloading').length;
        document.getElementById('activeCount').textContent    = n;
        document.getElementById('navActiveCount').textContent = n;
    }

    // ── HELPERS ───────────────────────────────────────────────
    getFilenameFromUrl(url) {
        try {
            const p = new URL(url).pathname;
            const f = decodeURIComponent(p.substring(p.lastIndexOf('/') + 1));
            return f || url;
        } catch (_) { return url; }
    }

    formatFileSize(bytes) {
        if (bytes === 0) return '0 B';
        if (!bytes || bytes < 0) return '—';
        
        const units = ['B', 'KB', 'MB', 'GB', 'TB'];
        const k = 1024;
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        
        return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + units[i];
    }

    // FIXED: Format speed in MB/s (MegaBytes per second)
    fmtSpeedMBps(speedMBps) {
        if (speedMBps >= 1000) {
            return `${(speedMBps/1000).toFixed(2)} GB/s`;
        } else if (speedMBps >= 1) {
            return `${speedMBps.toFixed(1)} MB/s`;
        } else if (speedMBps > 0) {
            return `${(speedMBps * 1000).toFixed(0)} KB/s`;
        }
        return '0 MB/s';
    }

    // Keep for backward compatibility but not used
    fmtSpeed(mbps_speed) {
        const speedMBps = mbps_speed; // Actually MB/s
        return this.fmtSpeedMBps(speedMBps);
    }

    fmtSpeedMbps(mbps) {
        if (mbps >= 1000) {
            return `${(mbps/1000).toFixed(2)} Gbps`;
        } else if (mbps >= 1) {
            return `${mbps.toFixed(1)} Mbps`;
        } else if (mbps > 0) {
            return `${(mbps * 1000).toFixed(0)} Kbps`;
        }
        return '0 Mbps';
    }

    // ── TOAST NOTIFICATIONS ───────────────────────────────────
    toast(msg, type = 'info') {
        const wrap = document.getElementById('toastWrap');
        const t = document.createElement('div');
        t.className = `toast ${type}`;
        const icons = { success:'fa-check', error:'fa-times', info:'fa-info-circle', warning:'fa-exclamation-triangle' };
        t.innerHTML = `<i class="fas ${icons[type]||icons.info}"></i><span>${msg}</span>`;
        wrap.appendChild(t);
        setTimeout(() => t.remove(), 4000);
    }
}

document.addEventListener('DOMContentLoaded', () => {
    window.downloadManager = new DownloadManager();
});