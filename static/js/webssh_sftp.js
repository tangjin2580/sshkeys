/*
 * WebSSH SFTP 远程文件管理
 * 依赖：webssh_terminal.js（读取 _webssh_connected, _webssh_session_id 等全局状态）
 */

// ============================================================
// SFTP 远程文件管理
// ============================================================
let _sftp_cwd = '';          // 当前目录
let _sftp_selected = null;   // 当前选中的文件路径

function _sftp_api(method, path, params = {}) {
    if (!_webssh_connected || !_webssh_session_id) {
        showToast('请先连接服务器', 'error');
        return null;
    }
    params.session_id = _webssh_session_id;
    const url = `/api/webssh/sftp/${method}${path ? `?${new URLSearchParams(params).toString()}` : ''}`;
    return url;
}

function _format_file_size(bytes) {
    if (bytes === 0) return '0 B';
    const units = ['B', 'K', 'M', 'G', 'T'];
    const i = Math.floor(Math.log(bytes) / Math.log(1024));
    return (bytes / Math.pow(1024, i)).toFixed(i > 0 ? 1 : 0) + ' ' + units[i];
}

function _file_icon(name, isDir, isLink) {
    if (isDir) return '📁';
    if (isLink) return '🔗';
    const ext = name.split('.').pop().toLowerCase();
    const map = {
        tar: '📦', gz: '📦', zip: '📦', rar: '📦', '7z': '📦', bz2: '📦', xz: '📦',
        sh: '📜', py: '📜', js: '📜', ts: '📜', rb: '📜', pl: '📜', lua: '📜',
        conf: '⚙️', cfg: '⚙️', ini: '⚙️', yml: '⚙️', yaml: '⚙️', json: '⚙️',
        jpg: '🖼️', jpeg: '🖼️', png: '🖼️', gif: '🖼️', svg: '🖼️', webp: '🖼️', bmp: '🖼️',
        mp4: '🎬', mkv: '🎬', avi: '🎬', mov: '🎬',
        mp3: '🎵', wav: '🎵', flac: '🎵',
        pdf: '📕', doc: '📘', docx: '📘', xls: '📗', xlsx: '📗', ppt: '📙', pptx: '📙',
        md: '📝', txt: '📝', log: '📝',
        so: '🔧', dll: '🔧', exe: '🔧', bin: '🔧',
    };
    return map[ext] || '📄';
}

async function sftpList(path) {
    if (!_webssh_connected || !_webssh_session_id) return;
    const listEl = document.getElementById('sftpFileList');
    if (listEl) listEl.innerHTML = '<div class="sidebar-empty">⏳ 加载中...</div>';
    // 传空字符串让后端自动获取家目录，不传 ~（部分服务器不支持）
    const pathParam = path && path !== '~' ? path : '';
    const url = `/api/webssh/sftp/ls?session_id=${encodeURIComponent(_webssh_session_id)}&path=${encodeURIComponent(pathParam)}`;
    try {
        const res = await fetch(url);
        const data = await res.json();
        if (!data.success) {
            showToast(data.error || '读取目录失败', 'error');
            return;
        }
        _sftp_cwd = data.path;
        renderSftpBreadcrumb(data.path);
        renderSftpFileList(data.entries);
    } catch (e) {
        showToast('读取目录失败: ' + e.message, 'error');
    }
}

function renderSftpBreadcrumb(path) {
    const el = document.getElementById('sftpBreadcrumb');
    if (!el) return;
    if (!path) {
        el.innerHTML = '<span class="sftp-path-hint">连接后可浏览远程文件</span>';
        return;
    }
    // 将路径拆分为面包屑
    const parts = path.split('/').filter(p => p);
    let html = '<span class="sftp-crumb" onclick="sftpList(\'/\')">/</span>';
    let cur = '';
    for (const part of parts) {
        cur += '/' + part;
        html += `<span class="sftp-crumb-sep">›</span>`;
        html += `<span class="sftp-crumb" onclick="sftpList('${cur.replace(/'/g, "\\'")}')">${part}</span>`;
    }
    el.innerHTML = html;
}

function renderSftpFileList(entries) {
    const el = document.getElementById('sftpFileList');
    if (!el) return;

    // 构建 HTML：先加 .. 返回上级
    let html = '';
    if (_sftp_cwd && _sftp_cwd !== '/') {
        html += `<div class="sftp-file-item sftp-file-up" onclick="sftpGoUp()">
            <span class="sftp-file-icon">📁</span>
            <span class="sftp-file-name is-dir">..</span>
            <span class="sftp-file-size"></span>
            <span class="sftp-file-actions"></span>
        </div>`;
    }

    if (!entries || entries.length === 0) {
        if (!html) html += '<div class="sidebar-empty">空目录</div>';
        el.innerHTML = html;
        return;
    }
    html += entries.map(e => {
        const icon = _file_icon(e.name, e.is_dir, e.is_link);
        const sizeStr = e.is_dir ? '' : _format_file_size(e.size);
        const fullPath = (_sftp_cwd === '/' ? '' : _sftp_cwd) + '/' + e.name;
        const escapedName = e.name.replace(/</g, '&lt;').replace(/>/g, '&gt;');
        const safePath = fullPath.replace(/'/g, "\\'");
        const clickHandler = e.is_dir
            ? `ondblclick="sftpList('${safePath}')"`
            : `ondblclick="sftpDownload('${safePath}')"`;
        return `<div class="sftp-file-item" data-path="${fullPath}" onclick="sftpSelectItem(this)" ${clickHandler}>
            <span class="sftp-file-icon">${icon}</span>
            <span class="sftp-file-name ${e.is_dir ? 'is-dir' : ''}">${escapedName}</span>
            <span class="sftp-file-size">${sizeStr}</span>
            <span class="sftp-file-actions">
                <button class="sftp-file-action-btn" onclick="event.stopPropagation();sftpRename('${safePath}','${escapedName}')" title="重命名">✏️</button>
                <button class="sftp-file-action-btn danger" onclick="event.stopPropagation();sftpDelete('${safePath}','${escapedName}')" title="删除">🗑️</button>
            </span>
        </div>`;
    }).join('');
    el.innerHTML = html;
}

function sftpSelectItem(el) {
    document.querySelectorAll('.sftp-file-item.selected').forEach(s => s.classList.remove('selected'));
    el.classList.add('selected');
    _sftp_selected = el.dataset.path;
}

function sftpGoUp() {
    if (!_sftp_cwd || _sftp_cwd === '/') return;
    const parts = _sftp_cwd.split('/').filter(p => p);
    parts.pop();
    sftpList(parts.length > 0 ? '/' + parts.join('/') : '/');
}

function sftpRefresh() {
    sftpList(_sftp_cwd || '~');
}

function sftpDownload(path) {
    if (!_webssh_connected || !_webssh_session_id) return;
    // 使用隐藏 <a> 触发下载
    const url = `/api/webssh/sftp/download?session_id=${encodeURIComponent(_webssh_session_id)}&path=${encodeURIComponent(path)}`;
    const a = document.createElement('a');
    a.href = url;
    a.style.display = 'none';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
}

function sftpUploadTrigger() {
    if (!_webssh_connected || !_webssh_session_id) {
        showToast('请先连接服务器', 'error');
        return;
    }
    document.getElementById('sftpUploadInput').click();
}

async function sftpUploadFile(input) {
    if (!input.files || !input.files[0]) return;
    const file = input.files[0];
    const formData = new FormData();
    formData.append('session_id', _webssh_session_id);
    // 传实际路径，空字符串时后端会自动获取家目录
    formData.append('path', _sftp_cwd || '');
    formData.append('file', file);
    try {
        showToast(`正在上传 ${file.name}...`, 'info');
        const res = await fetch('/api/webssh/sftp/upload', { method: 'POST', body: formData });
        const data = await res.json();
        if (data.success) {
            showToast(data.message || '上传成功', 'success');
            sftpRefresh();
        } else {
            showToast(data.error || '上传失败', 'error');
        }
    } catch (e) {
        showToast('上传失败: ' + e.message, 'error');
    }
    // 清空 input 允许重复上传同一文件
    input.value = '';
}

async function sftpDelete(path, name) {
    if (!path) return;
    // 使用全局确认弹窗
    if (typeof showConfirm === 'function') {
        showConfirm(`确认删除 "${name}"`, '此操作不可撤销，确定要删除吗？', async () => {
            try {
                const res = await fetch('/api/webssh/sftp/delete', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ session_id: _webssh_session_id, path }),
                });
                const data = await res.json();
                if (data.success) {
                    showToast(data.message || '删除成功', 'success');
                    sftpRefresh();
                } else {
                    showToast(data.error || '删除失败', 'error');
                }
            } catch (e) {
                showToast('删除失败: ' + e.message, 'error');
            }
        });
    } else if (confirm(`确认删除 "${name}"？此操作不可撤销。`)) {
        try {
            const res = await fetch('/api/webssh/sftp/delete', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ session_id: _webssh_session_id, path }),
            });
            const data = await res.json();
            if (data.success) {
                showToast(data.message || '删除成功', 'success');
                sftpRefresh();
            } else {
                showToast(data.error || '删除失败', 'error');
            }
        } catch (e) {
            showToast('删除失败: ' + e.message, 'error');
        }
    }
}

async function sftpNewFolder() {
    if (!_webssh_connected || !_webssh_session_id) {
        showToast('请先连接服务器', 'error');
        return;
    }
    const name = prompt('请输入新文件夹名称：');
    if (!name || !name.trim()) return;
    const fullPath = (_sftp_cwd === '/' ? '' : _sftp_cwd) + '/' + name.trim();
    try {
        const res = await fetch('/api/webssh/sftp/mkdir', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: _webssh_session_id, path: fullPath }),
        });
        const data = await res.json();
        if (data.success) {
            showToast(data.message || '文件夹已创建', 'success');
            sftpRefresh();
        } else {
            showToast(data.error || '创建失败', 'error');
        }
    } catch (e) {
        showToast('创建失败: ' + e.message, 'error');
    }
}

async function sftpRename(path, oldName) {
    if (!path) return;
    const newName = prompt(`重命名 "${oldName}" 为：`, oldName);
    if (!newName || !newName.trim() || newName.trim() === oldName) return;
    const dir = path.substring(0, path.lastIndexOf('/'));
    const newPath = dir + '/' + newName.trim();
    try {
        const res = await fetch('/api/webssh/sftp/rename', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: _webssh_session_id, old_path: path, new_path: newPath }),
        });
        const data = await res.json();
        if (data.success) {
            showToast('重命名成功', 'success');
            sftpRefresh();
        } else {
            showToast(data.error || '重命名失败', 'error');
        }
    } catch (e) {
        showToast('重命名失败: ' + e.message, 'error');
    }
}

function sftpInitOnConnect() {
    // 连接成功后自动加载家目录（不传 path，让后端自动获取）
    sftpList('');
    // 显示文件管理模式
    const panel = document.getElementById('websshFilePanel');
    if (panel) {
        const modeBadge = document.createElement('div');
        modeBadge.className = 'sftp-mode-badge';
        modeBadge.textContent = _webssh_file_mode === 'sftp' ? 'SFTP' : 'EXEC';
        modeBadge.title = _webssh_file_mode === 'sftp'
            ? '使用 SFTP 协议（完整支持）'
            : '服务器未启用 SFTP，降级为 SSH exec 模式';
        const header = panel.querySelector('.sidebar-header');
        if (header && !header.querySelector('.sftp-mode-badge')) {
            header.appendChild(modeBadge);
        }
    }
}

function sftpResetOnDisconnect() {
    _sftp_cwd = '';
    _sftp_selected = null;
    _webssh_file_mode = 'exec';
    const list = document.getElementById('sftpFileList');
    const breadcrumb = document.getElementById('sftpBreadcrumb');
    if (list) list.innerHTML = '<div class="sidebar-empty">未连接</div>';
    if (breadcrumb) breadcrumb.innerHTML = '<span class="sftp-path-hint">连接后可浏览远程文件</span>';
    // 移除模式标记
    const badge = document.querySelector('.sftp-mode-badge');
    if (badge) badge.remove();
}
