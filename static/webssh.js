/*
 * WebSSH 前端逻辑 — HTTP 长轮询版本
 * 依赖：xterm.js（已在 index.html 中通过 CDN 引入）
 * 后端 API：
 *   POST /api/webssh/connect  → 建立 SSH 连接，返回 session_id
 *   POST /api/webssh/send     → 发送输入到 SSH
 *   GET  /api/webssh/recv     → 长轮询获取 SSH 输出
 *   POST /api/webssh/resize   → 调整终端大小
 *   POST /api/webssh/disconnect → 关闭 SSH 连接
 */

// ============ 状态 ============
let _webssh_term = null;       // xterm Terminal 实例
let _webssh_fit = null;        // xterm fit addon
let _webssh_connected = false;
let _webssh_alias = '';        // 当前连接的 alias
let _webssh_session_id = null; // 后端会话 ID
let _webssh_polling = false;   // 轮询循环控制
let _webssh_poll_abort = null; // 当前 fetch 的 AbortController
let _webssh_reconnect_tried = false; // 是否已尝试过自动重连
let _webssh_last_params = null; // 上次连接参数（用于自动重连）
let _webssh_theme_observer = null; // 主题变化观察器
let _webssh_user_disconnect = false; // 用户主动断开标记（区分异常断开）

// 输入缓冲：合并 30ms 内的按键批量发送，大幅减少 HTTP 请求
let _webssh_input_buf = '';
let _webssh_input_timer = null;
let _webssh_file_mode = 'exec'; // 文件管理模式：'sftp' 或 'exec'

// ============ 命令历史 ============
let _webssh_cmd_buf = '';        // 当前行输入缓冲（用于命令捕获）
let _webssh_cmd_history = [];    // 命令历史数组
const _CMD_HISTORY_MAX = 200;    // 最多保留 200 条

function _cmd_history_key() {
    // 按主机名隔离命令历史
    return 'webssh-cmd-history-' + (_webssh_last_params?.hostname || 'default');
}

function _load_cmd_history() {
    try {
        const raw = localStorage.getItem(_cmd_history_key());
        _webssh_cmd_history = raw ? JSON.parse(raw) : [];
    } catch (e) {
        _webssh_cmd_history = [];
    }
    renderCmdHistory();
}

function _save_cmd_history() {
    try {
        localStorage.setItem(_cmd_history_key(), JSON.stringify(_webssh_cmd_history));
    } catch (e) {}
}

function _track_cmd_input(data) {
    // 逐字符处理，捕获命令行内容
    for (let i = 0; i < data.length; i++) {
        const ch = data[i];
        const code = data.charCodeAt(i);
        if (ch === '\r' || ch === '\n') {
            // Enter → 命令完成
            const cmd = _webssh_cmd_buf.trim();
            if (cmd) {
                _record_command(cmd);
            }
            _webssh_cmd_buf = '';
        } else if (code === 127 || code === 8) {
            // Backspace / Ctrl+H → 删除最后一个字符
            _webssh_cmd_buf = _webssh_cmd_buf.slice(0, -1);
        } else if (code === 3) {
            // Ctrl+C → 中断当前命令
            _webssh_cmd_buf = '';
        } else if (code === 21) {
            // Ctrl+U → 清除整行
            _webssh_cmd_buf = '';
        } else if (code === 11) {
            // Ctrl+K → 删除到行尾（简化处理）
        } else if (code === 27) {
            // Escape 序列开头（方向键、功能键等）— 跳过整个序列
            // 简化处理：跳过 ESC 及其后 1-2 个字符
            i++; // 跳过 ESC 后的 [
            if (i < data.length && data[i] === '[') {
                i++; // 跳过 [
                // 跳过直到遇到 ~ 或字母
                while (i < data.length && !/[~a-zA-Z]/.test(data[i])) i++;
            }
        } else if (code >= 32) {
            // 可打印字符
            _webssh_cmd_buf += ch;
        }
    }
}

function _record_command(cmd) {
    // 去重：如果和上一条相同则不重复添加
    if (_webssh_cmd_history.length > 0 && _webssh_cmd_history[0].cmd === cmd) {
        return;
    }
    _webssh_cmd_history.unshift({
        cmd: cmd,
        time: Date.now(),
    });
    if (_webssh_cmd_history.length > _CMD_HISTORY_MAX) {
        _webssh_cmd_history = _webssh_cmd_history.slice(0, _CMD_HISTORY_MAX);
    }
    _save_cmd_history();
    renderCmdHistory();
}

function renderCmdHistory() {
    const list = document.getElementById('websshCmdHistoryList');
    if (!list) return;
    if (_webssh_cmd_history.length === 0) {
        list.innerHTML = '<div class="sidebar-empty">暂无命令历史</div>';
        return;
    }
    list.innerHTML = _webssh_cmd_history.map((item, idx) => {
        const timeStr = new Date(item.time).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
        const escaped = item.cmd.replace(/</g, '&lt;').replace(/>/g, '&gt;');
        return `<div class="cmd-history-item" onclick="runCmdFromHistory(${idx})" title="${escaped}">
            <span class="cmd-icon">▶</span>
            <span class="cmd-text">${escaped}</span>
            <span class="cmd-time">${timeStr}</span>
        </div>`;
    }).join('');
}

function runCmdFromHistory(idx) {
    if (idx < 0 || idx >= _webssh_cmd_history.length) return;
    if (!_webssh_connected || !_webssh_session_id) {
        showToast('请先连接服务器', 'error');
        return;
    }
    const cmd = _webssh_cmd_history[idx].cmd;
    // 发送命令 + 回车
    _webssh_input_buf += cmd + '\r';
    if (!_webssh_input_timer) {
        _webssh_input_timer = setTimeout(_flush_input, 30);
    }
}

function clearCmdHistory() {
    _webssh_cmd_history = [];
    _save_cmd_history();
    renderCmdHistory();
    showToast('命令历史已清空', 'info');
}

function _flush_input() {
    _webssh_input_timer = null;
    if (!_webssh_input_buf || !_webssh_connected || !_webssh_session_id) {
        _webssh_input_buf = '';
        return;
    }
    const data = _webssh_input_buf;
    _webssh_input_buf = '';
    fetch('/api/webssh/send', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: _webssh_session_id, data }),
    }).catch(e => console.error('[WebSSH] 发送失败:', e.message));
}

// ============ 终端主题（跟随 App 深浅色） ============
const _TERM_THEMES = {
    dark: {
        background: '#1a1a2e',
        foreground: '#e0e0e0',
        cursor: '#ffffff',
        cursorAccent: '#1a1a2e',
        selectionBackground: 'rgba(100, 161, 157, 0.3)',
        black: '#1a1a2e', red: '#e06c75', green: '#98c379', yellow: '#e5c07b',
        blue: '#61afef', magenta: '#c678dd', cyan: '#56b6c2', white: '#e0e0e0',
        brightBlack: '#5c6370', brightRed: '#e06c75', brightGreen: '#98c379',
        brightYellow: '#e5c07b', brightBlue: '#61afef', brightMagenta: '#c678dd',
        brightCyan: '#56b6c2', brightWhite: '#ffffff',
    },
    light: {
        background: '#ffffff',
        foreground: '#1a1a2e',
        cursor: '#1a1a2e',
        cursorAccent: '#ffffff',
        selectionBackground: 'rgba(100, 161, 157, 0.25)',
        black: '#1a1a2e', red: '#c0392b', green: '#27ae60', yellow: '#f39c12',
        blue: '#2980b9', magenta: '#8e44ad', cyan: '#16a085', white: '#ffffff',
        brightBlack: '#5c6370', brightRed: '#c0392b', brightGreen: '#27ae60',
        brightYellow: '#f39c12', brightBlue: '#2980b9', brightMagenta: '#8e44ad',
        brightCyan: '#16a085', brightWhite: '#1a1a2e',
    },
};

function _is_dark_theme() {
    return document.documentElement.classList.contains('dark');
}

function _apply_terminal_theme() {
    if (!_webssh_term) return;
    _webssh_term.options.theme = _is_dark_theme() ? _TERM_THEMES.dark : _TERM_THEMES.light;
}

// ============ 初始化终端 ============
function initWebSSHTerminal() {
    if (_webssh_term) return;
    if (typeof Terminal === 'undefined') {
        console.error('[WebSSH] xterm.Terminal 未加载！请检查 CDN 引入');
        showToast('终端组件未加载，请刷新页面', 'error');
        return;
    }
    const term = new Terminal({
        fontFamily: 'var(--font-mono)',
        fontSize: 13,
        lineHeight: 1.5,
        theme: _is_dark_theme() ? _TERM_THEMES.dark : _TERM_THEMES.light,
        cursorBlink: true,
        allowProposedApi: true,
    });
    const fitAddon = new FitAddon.FitAddon();
    term.loadAddon(fitAddon);

    const container = document.getElementById('websshTerminal');
    if (container) {
        term.open(container);
        setTimeout(() => { try { fitAddon.fit(); } catch (e) {} }, 100);
    }

    // 用户输入 → 缓冲合并后批量发送（30ms 内的按键合并为一个请求）
    term.onData(data => {
        if (_webssh_connected && _webssh_session_id) {
            _webssh_input_buf += data;
            if (!_webssh_input_timer) {
                _webssh_input_timer = setTimeout(_flush_input, 30);
            }
            // 同时追踪命令历史
            _track_cmd_input(data);
        }
    });

    _webssh_term = term;
    _webssh_fit = fitAddon;

    // 监听 App 主题切换，自动更新终端主题
    if (!_webssh_theme_observer) {
        _webssh_theme_observer = new MutationObserver(() => _apply_terminal_theme());
        _webssh_theme_observer.observe(document.documentElement, {
            attributes: true, attributeFilter: ['class', 'data-theme'],
        });
    }

    // 窗口缩放 → 通知后端调整 pty 大小（节流）
    let _resize_timer = null;
    window.addEventListener('resize', () => {
        if (_resize_timer) clearTimeout(_resize_timer);
        _resize_timer = setTimeout(() => {
            if (_webssh_term && _webssh_connected && _webssh_session_id) {
                try { fitAddon.fit(); } catch (e) {}
                const cols = term.cols;
                const rows = term.rows;
                fetch('/api/webssh/resize', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ session_id: _webssh_session_id, cols, rows }),
                }).catch(e => {});
            }
        }, 200);
    });

    // 右键粘贴：在终端区域右键直接粘贴剪贴板内容
    if (container) {
        container.addEventListener('contextmenu', async (e) => {
            e.preventDefault();
            try {
                const text = await navigator.clipboard.readText();
                if (text && _webssh_connected && _webssh_session_id) {
                    _webssh_input_buf += text;
                    if (!_webssh_input_timer) {
                        _webssh_input_timer = setTimeout(_flush_input, 30);
                    }
                }
            } catch (err) {
                // 剪贴板权限被拒绝或不可用
            }
        });
    }
}

// ============ 连接 ============
function connectWebSSH(params) {
    if (!_webssh_term) { initWebSSHTerminal(); }
    if (!_webssh_term) {
        showToast('终端初始化失败', 'error');
        return;
    }
    if (_webssh_connected) {
        disconnectWebSSH(() => { _do_connect(params); });
    } else {
        _do_connect(params);
    }
}

async function _do_connect(params) {
    // 表单校验
    if (!params.hostname || !params.hostname.trim()) {
        showToast('请填写服务器地址', 'error');
        updateWebSSHStatus('disconnected', '未连接');
        return;
    }
    if (!params.username || !params.username.trim()) {
        showToast('请填写用户名', 'error');
        updateWebSSHStatus('disconnected', '未连接');
        return;
    }

    // 保存参数用于自动重连
    _webssh_last_params = { ...params };
    _webssh_reconnect_tried = false;

    updateWebSSHStatus('connecting', '正在连接...');
    try {
        const res = await fetch('/api/webssh/connect', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                alias: params.alias || '',
                hostname: params.hostname,
                port: params.port || 22,
                username: params.username,
                password: params.password || '',
                identity_file: params.identity_file || '',
                term: 'xterm-256color',
                cols: _webssh_term.cols,
                rows: _webssh_term.rows,
            }),
        });
        const data = await res.json();
        if (!data.success) {
            updateWebSSHStatus('error', data.error || '连接失败');
            showToast(data.error || '连接失败', 'error');
            return;
        }
        _webssh_session_id = data.session_id;
        _webssh_connected = true;
        _webssh_alias = params.alias || '';
        _webssh_file_mode = data.file_mode || 'exec';
        _webssh_cmd_buf = ''; // 清空当前命令缓冲
        _load_cmd_history(); // 加载该主机的命令历史
        updateWebSSHStatus('connected', data.message || '已连接');
        showToast(data.message || '已连接', 'success');

        // 保存连接历史（调用 script.js 里的函数）
        if (typeof saveWebSSHHistory === 'function') {
            saveWebSSHHistory({
                alias: params.alias || '',
                hostname: params.hostname,
                port: params.port || 22,
                username: params.username,
                identity_file: params.identity_file || '',
            });
        }

        // 适配终端大小
        try { _webssh_fit.fit(); } catch (e) {}
        const cols = _webssh_term.cols;
        const rows = _webssh_term.rows;
        fetch('/api/webssh/resize', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: _webssh_session_id, cols, rows }),
        }).catch(e => {});

        // 初始化远程文件管理
        sftpInitOnConnect();

        // 启动长轮询
        _start_poll();
    } catch (e) {
        updateWebSSHStatus('error', '连接失败: ' + e.message);
        showToast('连接失败: ' + e.message, 'error');
    }
}

// ============ 长轮询接收输出 ============
function _start_poll() {
    _webssh_polling = true;
    _poll_once();
}

function _poll_once() {
    if (!_webssh_polling || !_webssh_session_id) return;

    const controller = new AbortController();
    _webssh_poll_abort = controller;

    fetch(`/api/webssh/recv?session_id=${encodeURIComponent(_webssh_session_id)}&timeout=5`, {
        signal: controller.signal,
    }).then(res => res.json())
      .then(data => {
          _webssh_poll_abort = null;
          if (!_webssh_polling) return;

          if (!data.success) {
              _webssh_term.write('\r\n[会话不存在]\r\n');
              _on_disconnected();
              return;
          }
          if (data.closed) {
              _webssh_term.write('\r\n[连接已关闭]\r\n');
              _on_disconnected();
              return;
          }
          if (data.data) {
              _webssh_term.write(data.data);
          }
          // 继续下一次轮询
          _poll_once();
      })
      .catch(err => {
          _webssh_poll_abort = null;
          if (!_webssh_polling) return;
          if (err.name === 'AbortError') return;
          // 网络错误，短暂等待后重试
          setTimeout(_poll_once, 1000);
      });
}

function _on_disconnected() {
    _webssh_connected = false;
    _webssh_polling = false;
    _webssh_session_id = null;
    _webssh_cmd_buf = ''; // 清空命令缓冲

    // 清理文件管理器
    sftpResetOnDisconnect();

    // 清理输入缓冲
    if (_webssh_input_timer) {
        clearTimeout(_webssh_input_timer);
        _webssh_input_timer = null;
    }
    _webssh_input_buf = '';

    // 用户主动断开 → 不自动重连
    if (_webssh_user_disconnect) {
        _webssh_user_disconnect = false;
        updateWebSSHStatus('disconnected', '已断开');
        return;
    }

    // 异常断开 → 尝试自动重连一次
    if (!_webssh_reconnect_tried && _webssh_last_params) {
        _webssh_reconnect_tried = true;
        updateWebSSHStatus('connecting', '连接断开，正在重连...');
        if (_webssh_term) _webssh_term.write('\r\n\x1b[33m[连接断开，正在重连...]\x1b[0m\r\n');
        setTimeout(() => {
            if (!_webssh_connected) {
                _do_connect(_webssh_last_params);
            }
        }, 2000);
    } else {
        updateWebSSHStatus('disconnected', '连接已断开');
    }
}

// ============ 断开连接 ============
function disconnectWebSSH(callback) {
    _webssh_user_disconnect = true; // 标记为用户主动断开，阻止自动重连

    // 清理输入缓冲
    if (_webssh_input_timer) {
        clearTimeout(_webssh_input_timer);
        _webssh_input_timer = null;
    }
    _webssh_input_buf = '';

    if (_webssh_connected && _webssh_session_id) {
        _webssh_polling = false;
        // 取消正在进行的轮询请求
        if (_webssh_poll_abort) {
            try { _webssh_poll_abort.abort(); } catch (e) {}
        }
        fetch('/api/webssh/disconnect', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: _webssh_session_id }),
        }).catch(e => {});
        _webssh_connected = false;
        _webssh_session_id = null;
        updateWebSSHStatus('disconnected', '已断开');
        sftpResetOnDisconnect();
    }
    if (callback) callback();
}

// ============ 状态显示 ============
function updateWebSSHStatus(status, message) {
    const el = document.getElementById('websshStatus');
    if (!el) return;
    const dot = el.querySelector('.status-dot');
    const text = el.querySelector('.status-text');
    const btn = document.getElementById('websshDisconnectBtn');
    const info = document.getElementById('websshConnInfo');
    const connText = document.getElementById('websshConnText');

    if (dot) {
        dot.className = 'status-dot';
        if (status === 'connected') dot.classList.add('connected');
        else if (status === 'disconnected' || status === 'error') dot.classList.add('disconnected');
        else if (status === 'connecting') dot.classList.add('connecting');
    }
    if (text) text.textContent = message || status;
    if (btn) btn.style.display = status === 'connected' ? 'inline-flex' : 'none';
    if (info) info.style.display = status === 'connected' ? 'block' : 'none';
    if (connText) connText.textContent = message || '';
}

// ============ 从「我的连接」一键打开终端 ============
async function openWebSSHTerminal(alias) {
    if (!alias) {
        showToast('未指定连接别名', 'error');
        return;
    }

    // 切换到 WebSSH 面板
    const panelId = 'panel-webssh';
    document.querySelectorAll('.menu-list li[data-panel]').forEach(si => {
        si.classList.toggle('active', si.dataset.panel === panelId);
    });
    if (typeof updateNavContext === 'function') updateNavContext(panelId);
    if (typeof switchPanel === 'function') switchPanel(panelId);

    // 初始化终端
    if (!_webssh_term) {
        initWebSSHTerminal();
        await new Promise(r => setTimeout(r, 300));
    }

    try {
        const res = await fetch('/api/connections');
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (!data.success) return showToast('加载连接信息失败', 'error');
        const conn = (data.connections || []).find(c => c.alias === alias);
        if (!conn) return showToast('未找到连接: ' + alias, 'error');

        showToast(`正在连接到 ${alias}...`, 'info');

        const params = {
            alias: conn.alias,
            hostname: conn.hostname,
            port: conn.port || 22,
            username: conn.user,
            identity_file: conn.identity_file || '',
        };
        connectWebSSH(params);
    } catch (e) {
        showToast('连接失败: ' + e.message, 'error');
    }
}

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
