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
        theme: {
            background: '#1a1a1a',
            foreground: '#e0e0e0',
            cursor: '#ffffff',
            selectionBackground: 'rgba(100, 161, 157, 0.3)',
        },
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

    // 用户输入 → 发送到后端
    term.onData(data => {
        if (_webssh_connected && _webssh_session_id) {
            fetch('/api/webssh/send', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ session_id: _webssh_session_id, data }),
            }).catch(e => console.error('[WebSSH] 发送失败:', e.message));
        }
    });

    _webssh_term = term;
    _webssh_fit = fitAddon;

    // 窗口缩放 → 通知后端调整 pty 大小
    window.addEventListener('resize', () => {
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
    });

    console.log('[WebSSH] 终端已初始化');
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
    updateWebSSHStatus('disconnected', '连接已断开');
}

// ============ 断开连接 ============
function disconnectWebSSH(callback) {
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
    console.log('[WebSSH] openWebSSHTerminal called, alias=', alias);
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
        console.log(`[WebSSH] 正在连接到 ${alias} (${conn.user}@${conn.hostname})`);

        const params = {
            alias: conn.alias,
            hostname: conn.hostname,
            port: conn.port || 22,
            username: conn.user,
            identity_file: conn.identity_file || '',
        };
        connectWebSSH(params);
    } catch (e) {
        console.error('[WebSSH] openWebSSHTerminal 错误:', e);
        showToast('连接失败: ' + e.message, 'error');
    }
}
