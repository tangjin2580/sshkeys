// ============ State ============
let sseSource = null;
let currentDeployProps = null;
let wipeAnimating = false;

// ============ 主题切换（含对角线 wipe 动画） ============
function initThemeToggle() {
    const btn = document.getElementById('theme-toggle-btn');
    if (!btn) return;

    const docEl = document.documentElement;
    const container = btn.querySelector('.theme-toggle__container');
    const wipe = document.getElementById('themeWipe');

    function updateTheme(isDark) {
        docEl.classList.toggle('dark', isDark);
        if (isDark) {
            docEl.removeAttribute('data-theme');
        } else {
            docEl.setAttribute('data-theme', 'light');
        }
        btn.setAttribute('aria-checked', String(isDark));
        btn.setAttribute('aria-label', isDark ? 'Switch to light theme' : 'Switch to dark theme');
        try {
            localStorage.setItem('app-theme', isDark ? 'dark' : 'light');
        } catch (e) {
            console.warn('Could not save theme to localStorage.', e);
        }
    }

    function runWipe(isDark) {
        if (wipeAnimating || !wipe) return;
        wipeAnimating = true;

        // 动画期间禁用页面背景过渡
        docEl.classList.add('wiping');

        // 立即切换主题：按钮动画在遮罩下同步进行
        updateTheme(isDark);

        // 计算切换按钮在视口中的中心坐标
        const btnRect = btn.getBoundingClientRect();
        const cx = btnRect.left + btnRect.width / 2;
        const cy = btnRect.top + btnRect.height / 2;

        // 计算覆盖全屏所需的最小圆半径
        const maxDist = Math.max(
            Math.hypot(cx, cy),
            Math.hypot(window.innerWidth - cx, cy),
            Math.hypot(cx, window.innerHeight - cy),
            Math.hypot(window.innerWidth - cx, window.innerHeight - cy)
        );
        const radius = Math.ceil(maxDist) + 20;

        const duration = '0.75s cubic-bezier(0.65, 0, 0.35, 1)';

        if (isDark) {
            // 黑夜模式：圆形从按钮放大到全屏
            wipe.style.transition = 'clip-path 0s';
            wipe.style.clipPath = `circle(0 at ${cx}px ${cy}px)`;
            void wipe.offsetWidth;
            wipe.style.transition = `clip-path ${duration}`;
            wipe.style.clipPath = `circle(${radius}px at ${cx}px ${cy}px)`;
        } else {
            // 白天模式：圆形从全屏缩小到按钮
            wipe.style.transition = 'clip-path 0s';
            wipe.style.clipPath = `circle(${radius}px at ${cx}px ${cy}px)`;
            void wipe.offsetWidth;
            wipe.style.transition = `clip-path ${duration}`;
            wipe.style.clipPath = `circle(0 at ${cx}px ${cy}px)`;
        }

        function onFinish() {
            wipe.removeEventListener('transitionend', onFinish);
            wipe.style.clipPath = '';
            wipe.style.transition = '';
            docEl.classList.remove('wiping');
            wipeAnimating = false;
        }
        wipe.addEventListener('transitionend', onFinish);
        setTimeout(onFinish, 800);
    }

    function handleClick() {
        const isDark = docEl.classList.contains('dark');
        if (wipe) {
            runWipe(!isDark);
        } else {
            updateTheme(!isDark);
        }
    }

    function handleContainerTransitionEnd(e) {
        if (e.target !== container || e.propertyName !== 'background-color') return;
        // No-op now, wipe handles timing
    }

    // 初始化：读取 inline 脚本设置的状态并同步
    updateTheme(docEl.classList.contains('dark'));

    btn.addEventListener('click', handleClick);
    container.addEventListener('transitionend', handleContainerTransitionEnd);

    // 监听系统偏好变化
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function (e) {
        if (!localStorage.getItem('app-theme')) {
            if (wipe) {
                runWipe(e.matches);
            } else {
                updateTheme(e.matches);
            }
        }
    });
}

// ============ 菜单面板切换 ============
function initMenuSwitching() {
    const menuItems = document.querySelectorAll('.nav-menu-item[data-panel]');
    if (!menuItems.length) return;

    menuItems.forEach(item => {
        item.addEventListener('click', function () {
            const panelId = this.dataset.panel;

            // 更新菜单激活状态
            menuItems.forEach(mi => mi.classList.remove('active'));
            this.classList.add('active');

            // 切换内容面板
            document.querySelectorAll('.content-panel').forEach(p => p.classList.remove('active'));
            const targetPanel = document.getElementById(panelId);
            if (targetPanel) {
                targetPanel.classList.add('active');
            }

            // 切换到连接面板时自动加载
            if (panelId === 'panel-connections') {
                loadConnections();
            }
        });
    });
}

// ============ SSE ============
function initSSE() {
    sseSource = new EventSource("/api/events");
    sseSource.onopen = () => log("已连接");
    sseSource.addEventListener("progress", (e) => {
        const d = JSON.parse(e.data);
        log(d.message);
    });
    sseSource.addEventListener("setup_complete", (e) => {
        const d = JSON.parse(e.data);
        log(d.message, d.success ? "success" : "warning");
        const btnSetupGo = document.getElementById("btnSetupGo");
        if (btnSetupGo) btnSetupGo.disabled = false;
        loadExistingConfig();
    });
    sseSource.onerror = () => log("连接中断", "warning");
}

// ============ 日志 ============
function log(msg, level = "info") {
    const box = document.getElementById("logBox");
    const section = document.getElementById("logSection");
    if (!box || !section) return;
    section.style.display = "block";
    const now = new Date().toLocaleTimeString();
    if (box.firstChild && box.firstChild.textContent.includes("等待操作")) box.innerHTML = "";
    const el = document.createElement("div");
    el.className = `log-entry log-${level}`;
    el.innerHTML = `<span class="log-time">${now}</span>${msg}`;
    box.appendChild(el);
    box.scrollTop = box.scrollHeight;
}

// ============ Toast ============
function showToast(msg, type = "info") {
    const t = document.createElement("div");
    t.className = `toast toast-${type}`;
    t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(() => t.remove(), 3500);
}

// ============ 确认弹窗 ============
let modalCallback = null;

function showConfirm(title, body, onConfirm) {
    const modal = document.getElementById("confirmModal");
    if (!modal) return;
    document.getElementById("modalTitle").textContent = title;
    document.getElementById("modalBody").textContent = body;
    modalCallback = onConfirm;
    modal.style.display = "flex";
    document.getElementById("modalConfirmBtn").focus();
}

function closeModal() {
    const modal = document.getElementById("confirmModal");
    if (!modal) return;
    modal.style.display = "none";
    modalCallback = null;
}

document.addEventListener("keydown", (e) => {
    const modal = document.getElementById("confirmModal");
    if (e.key === "Escape" && modal && modal.style.display === "flex") {
        closeModal();
    }
});

// ============ 密钥生成 ============
async function generateKey() {
    const btn = document.getElementById("btnGenerate");
    if (!btn) return;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span>正在生成...';

    const sel = document.getElementById("keyType");
    const opt = sel.selectedOptions[0];
    const keyType = sel.value;
    const keySize = parseInt(opt.dataset.size);
    const comment = document.getElementById("comment").value.trim();
    const passphrase = document.getElementById("passphrase").value.trim();

    const body = { key_type: keyType, key_size: keySize, comment };
    if (keyType === "ecdsa" && opt.dataset.curve) body.curve = opt.dataset.curve;
    if (passphrase) body.passphrase = passphrase;

    try {
        const res = await fetch("/api/generate", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        });
        const data = await res.json();
        if (data.success) {
            showKey(data);
            const uploadSection = document.getElementById("uploadSection");
            const setupSection = document.getElementById("setupSection");
            if (uploadSection) uploadSection.style.display = "block";
            if (setupSection) setupSection.style.display = "block";
            showToast("密钥生成成功！", "success");
        } else {
            showToast(data.error || "生成失败", "error");
            log(data.error || "生成失败", "error");
        }
    } catch (err) {
        showToast("网络错误", "error");
        log("网络错误: " + err.message, "error");
    } finally {
        btn.disabled = false;
        btn.textContent = "⚡ 一键生成密钥";
    }
}

function showKey(data) {
    const section = document.getElementById("keyDisplaySection");
    if (!section) return;
    section.style.display = "block";
    const info = document.getElementById("keyInfo");
    info.innerHTML = `<span>类型: ${data.key_type}</span><span>大小: ${data.key_size} bits</span>`;
    if (data.fingerprint) info.innerHTML += `<span>指纹: ${data.fingerprint}</span>`;
    document.getElementById("publicKeyText").textContent = data.public_key;
}

// ============ Tab 切换 ============
function switchTab(name) {
    document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
    event.target.classList.add("active");
    document.getElementById("panel-" + name).classList.add("active");
}

// ============ 上传 ============
async function uploadKey(platform) {
    const pubKeyEl = document.getElementById("publicKeyText");
    if (!pubKeyEl) return;
    const pubKey = pubKeyEl.textContent.trim();
    if (!pubKey) return showToast("请先生成密钥", "error");

    let body = { public_key: pubKey, platform };
    if (platform === "server") {
        body.host = document.getElementById("serverHost").value.trim();
        body.port = parseInt(document.getElementById("serverPort").value) || 22;
        body.username = document.getElementById("serverUser").value.trim();
        body.password = document.getElementById("serverPassword").value.trim();
    } else if (platform === "github") {
        body.token = document.getElementById("githubToken").value.trim();
        body.title = document.getElementById("githubTitle").value.trim() || "SSH Key Manager";
    } else if (platform === "gitlab") {
        body.token = document.getElementById("gitlabToken").value.trim();
        body.url = document.getElementById("gitlabUrl").value.trim();
        body.title = document.getElementById("gitlabTitle").value.trim() || "SSH Key Manager";
    }

    try {
        const res = await fetch("/api/upload", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        });
        const data = await res.json();
        showToast(data.message, data.success ? "success" : "error");
        log(data.message, data.success ? "success" : "error");
    } catch (err) {
        showToast("上传请求失败", "error");
    }
}

// ============ 一键部署 ============
async function deployOneClick() {
    const hostAlias = document.getElementById("hostAlias").value.trim();
    const hostname = document.getElementById("setupHostname").value.trim();
    const user = document.getElementById("setupUser").value.trim();
    const password = document.getElementById("setupPassword").value.trim();
    const pubKeyEl = document.getElementById("publicKeyText");
    if (!pubKeyEl) return;
    const pubKey = pubKeyEl.textContent.trim();

    if (!hostAlias || !hostname || !user) return showToast("请填写完整信息", "error");
    if (!pubKey) return showToast("请先生成密钥", "error");

    const btn = document.getElementById("btnSetupGo");
    if (!btn) return;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span>部署中...';

    currentDeployProps = { hostAlias, hostname };

    try {
        const res = await fetch("/api/save-and-setup", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                host_alias: hostAlias,
                hostname: hostname,
                user: user,
                port: 22,
                upload: !!password,
                upload_password: password || undefined,
            }),
        });
        const result = await res.json();
        if (result.success) {
            showToast("免密登录部署完成！ ssh " + hostAlias + " 或 ssh " + hostname, "success");
        } else {
            const partial = result.results && (result.results.saved || result.results.config);
            if (partial) {
                showToast("部分完成，请查看日志", "info");
            } else {
                showToast(result.error || "部署失败", "error");
            }
        }
    } catch (err) {
        showToast("请求失败", "error");
    } finally {
        btn.disabled = false;
        btn.textContent = "🚀 部署免密登录";
    }
}

// ============ 已有密钥管理 ============
async function loadExistingConfig() {
    const container = document.getElementById("existingKeysContent");
    if (!container) return;
    container.innerHTML = '<span class="loading-text">⏳ 加载中...</span>';

    try {
        const [keyRes, configRes] = await Promise.all([
            fetch("/api/existing-keys"),
            fetch("/api/ssh-config"),
        ]);
        const keyData = await keyRes.json();
        const configData = await configRes.json();

        let html = "";

        // 密钥文件列表
        if (keyData.success && keyData.keys && keyData.keys.length > 0) {
            html += '<div style="font-size:0.8rem;color:var(--text-secondary);margin-bottom:8px;">🔑 密钥文件</div>';
            keyData.keys.forEach(k => {
                html += `
                <div class="key-item">
                    <div class="key-item-left">
                        <span class="key-item-name" title="${esc(k.name)}">${esc(k.name)}</span>
                        <span class="key-item-badge">${esc(k.type.toUpperCase())}</span>
                    </div>
                    <div class="key-item-actions">
                        <button class="btn btn-sm btn-outline" onclick="copyKeyFile('${esc(k.name)}', '${esc(k.type)}')">📋</button>
                        <button class="btn btn-sm btn-outline btn-danger-text" onclick="deleteKeyFile('${esc(k.name)}')" title="删除密钥">🗑️</button>
                    </div>
                </div>`;
            });
        } else {
            html += '<div class="config-empty">暂未保存任何密钥</div>';
        }

        // Config 条目
        html += '<div style="font-size:0.8rem;color:var(--text-secondary);margin:14px 0 8px;">⚙️ SSH Config 条目</div>';
        if (configData.success && configData.config_entries && configData.config_entries.length > 0) {
            configData.config_entries.forEach(host => {
                const blockText = [
                    `Host ${escHtml(host.host)}`,
                    host.hostname ? `    HostName ${escHtml(host.hostname)}` : "",
                    host.user ? `    User ${escHtml(host.user)}` : "",
                    host.port && host.port !== 22 ? `    Port ${escHtml(String(host.port))}` : "",
                    host.identityfile ? `    IdentityFile ${escHtml(host.identityfile)}` : "",
                ].filter(Boolean).join("\n");
                html += `
                <div class="config-entry">
                    <div class="config-entry-content">${escHtml(blockText)}</div>
                    <div class="config-entry-actions">
                        <button class="btn btn-sm btn-outline btn-danger-text" onclick="deleteConfigHost('${esc(host.host)}')" title="删除此条目">🗑️</button>
                    </div>
                </div>`;
            });
        } else {
            html += '<div class="config-empty">暂无 SSH Config 条目</div>';
        }

        container.innerHTML = html;
    } catch (err) {
        container.innerHTML = '<span style="color:var(--danger);font-size:0.82rem;">加载失败: ' + esc(err.message) + '</span>';
    }
}

// ============ 删除操作 ============
async function deleteKeyFile(keyName) {
    showConfirm(
        "确认删除密钥？",
        `将永久删除 ~/.ssh/${keyName} 及其公钥文件，此操作不可撤销。`,
        async () => {
            try {
                const res = await fetch("/api/delete-key", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ key_name: keyName }),
                });
                const data = await res.json();
                showToast(data.message, data.success ? "success" : "error");
                log(data.message, data.success ? "success" : "error");
                loadExistingConfig();
            } catch (err) {
                showToast("请求失败", "error");
            }
        }
    );
}

async function deleteConfigHost(hostAlias) {
    showConfirm(
        "确认删除 Config 条目？",
        `将从 ~/.ssh/config 中移除 Host "${hostAlias}" 的配置，此操作不可撤销。`,
        async () => {
            try {
                const res = await fetch("/api/delete-config-host", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ host_alias: hostAlias }),
                });
                const data = await res.json();
                showToast(data.message, data.success ? "success" : "error");
                log(data.message, data.success ? "success" : "error");
                loadExistingConfig();
            } catch (err) {
                showToast("请求失败", "error");
            }
        }
    );
}

// ============ 复制 ============
function copyPublicKey() {
    const el = document.getElementById("publicKeyText");
    if (!el) return;
    const txt = el.textContent;
    navigator.clipboard.writeText(txt).then(() => showToast("已复制公钥", "success")).catch(() => showToast("复制失败", "error"));
}

function downloadPrivateKey() {
    showToast("私钥下载功能请在前端完成，为安全起见不在服务端暴露完整私钥", "info");
}

async function copyKeyFile(name, type) {
    try {
        const res = await fetch(`/api/existing-keys?key_name=${encodeURIComponent(name)}&key_type=${encodeURIComponent(type)}`);
        const data = await res.json();
        if (data.public_key) {
            navigator.clipboard.writeText(data.public_key).then(() => showToast("已复制公钥", "success"));
        } else {
            showToast("无法读取密钥", "error");
        }
    } catch {
        showToast("请求失败", "error");
    }
}

// ============ 连接管理 ============
async function loadConnections() {
    const grid = document.getElementById("connectionsGrid");
    if (!grid) return;
    try {
        const res = await fetch("/api/connections");
        const data = await res.json();
        if (!data.success || !data.connections || data.connections.length === 0) {
            grid.innerHTML = `
            <div class="empty-state" style="grid-column:1/-1;">
                <div class="icon">📭</div>
                <p>还没有保存任何连接</p>
                <p style="font-size:0.8rem;">去<a href="/">首页</a>生成密钥并部署，或点下方「手动添加」</p>
            </div>`;
            return;
        }
        grid.innerHTML = data.connections.map(c => {
            const keyValid = c.key_valid !== false;
            const keyClass = c.identity_file ? (keyValid ? 'valid' : 'invalid') : '';
            const keyText = c.identity_file
                ? (keyValid ? '🔑 ' + esc(c.identity_file) : '密钥文件不存在: ' + esc(c.identity_file))
                : '⚡ 无需额外配置';
            return `
            <div class="conn-card">
                <div class="conn-alias"><code>${esc(c.alias)}</code></div>
                <div class="conn-addr">${esc(c.user)}@${esc(c.hostname)}${c.port && c.port !== 22 ? ':' + c.port : ''}</div>
                <div class="conn-key ${keyClass}">${keyText}</div>
                <div class="conn-actions">
                    <button class="btn conn-connect" onclick="connectServer('${esc(c.alias)}')" ${keyValid ? '' : 'disabled title="密钥文件不存在，请先生成并部署密钥"'}>${keyValid ? '🚀 一键连接' : '🔒 密钥缺失'}</button>
                    <button class="btn conn-delete" onclick="deleteConn('${esc(c.id)}', '${esc(c.alias)}')" title="删除">🗑️</button>
                </div>
            </div>`;
        }).join("");
    } catch (e) {
        grid.innerHTML = `<div style="grid-column:1/-1;color:var(--danger);text-align:center;">加载失败: ${esc(e.message)}</div>`;
    }
}

async function connectServer(alias) {
    showToast("正在启动终端...", "info");
    try {
        const res = await fetch("/api/connections/connect", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ alias }),
        });
        const data = await res.json();
        showToast(data.message, data.success ? "success" : "error");
    } catch (e) {
        showToast("请求失败: " + e.message, "error");
    }
}

function deleteConn(id, alias) {
    showConfirm(
        "确认删除连接？",
        `将从连接管理中移除 "${alias}"，此操作不可撤销。\n（不会删除 ~/.ssh 中的密钥文件）`,
        async () => {
            try {
                const res = await fetch(`/api/connections/${id}`, { method: "DELETE" });
                const data = await res.json();
                showToast(data.message, data.success ? "success" : "error");
                loadConnections();
            } catch (e) {
                showToast("请求失败: " + e.message, "error");
            }
        }
    );
}

async function saveConnection() {
    const alias = document.getElementById("addAlias").value.trim();
    const hostname = document.getElementById("addHostname").value.trim();
    const user = document.getElementById("addUser").value.trim();
    const port = parseInt(document.getElementById("addPort").value) || 22;
    const idFile = document.getElementById("addIdFile").value.trim();

    if (!alias || !hostname || !user) return showToast("请填写必填项", "error");

    try {
        const res = await fetch("/api/connections", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ alias, hostname, user, port, identity_file: idFile }),
        });
        const data = await res.json();
        showToast(data.message, data.success ? "success" : "error");
        if (data.success) {
            document.getElementById("addAlias").value = "";
            document.getElementById("addHostname").value = "";
            document.getElementById("addUser").value = "";
            document.getElementById("addPort").value = "22";
            document.getElementById("addIdFile").value = "";
            const addSection = document.getElementById("addSection");
            if (addSection) addSection.open = false;
            loadConnections();
        }
    } catch (e) {
        showToast("请求失败: " + e.message, "error");
    }
}

// ============ 工具 ============
function esc(s) {
    if (!s) return "";
    return s.replace(/\\/g, "\\\\").replace(/'/g, "\\'").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function escHtml(s) {
    if (!s) return "";
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

// ============ 初始化 ============
document.addEventListener("DOMContentLoaded", () => {
    // Modal 事件绑定
    const modal = document.getElementById("confirmModal");
    const modalConfirmBtn = document.getElementById("modalConfirmBtn");
    if (modal) {
        modal.addEventListener("click", (e) => {
            if (e.target === modal) closeModal();
        });
    }
    if (modalConfirmBtn) {
        modalConfirmBtn.addEventListener("click", () => {
            if (modalCallback) modalCallback();
            closeModal();
        });
    }

    initThemeToggle();
    initMenuSwitching();
    initSSE();
    loadExistingConfig();

    // 连接页面自动加载
    const isConnectionsPage = !document.querySelector('.nav-menu-item[data-panel]') && document.getElementById('connectionsGrid');
    if (isConnectionsPage) {
        loadConnections();
    }
});