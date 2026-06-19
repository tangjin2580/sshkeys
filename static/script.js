// ============ State ============
let sseSource = null;
let wipeAnimating = false;
let logUnread = 0;

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

        // 遮罩动画时长（更快）
        const wipeDuration = '0.6s cubic-bezier(0.4, 0, 0.2, 1)';
        // 主题切换过渡时长（更自然）
        const themeDuration = '0.8s ease-in-out';

        if (isDark) {
            // 当前是黑夜，要切换到白天：白色圆圈扩散
            wipe.style.backgroundColor = 'rgba(254, 249, 240, 0.2)';
            wipe.style.transition = 'clip-path 0s';
            wipe.style.clipPath = `circle(0 at ${cx}px ${cy}px)`;
            void wipe.offsetWidth;
            wipe.style.transition = `clip-path ${wipeDuration}`;
            wipe.style.clipPath = `circle(${radius}px at ${cx}px ${cy}px)`;
        } else {
            // 当前是白天，要切换到黑夜：黑色圆圈扩散
            wipe.style.backgroundColor = 'rgba(10, 10, 26, 0.2)';
            wipe.style.transition = 'clip-path 0s';
            wipe.style.clipPath = `circle(0 at ${cx}px ${cy}px)`;
            void wipe.offsetWidth;
            wipe.style.transition = `clip-path ${wipeDuration}`;
            wipe.style.clipPath = `circle(${radius}px at ${cx}px ${cy}px)`;
        }

        function onFinish() {
            wipe.removeEventListener('transitionend', onFinish);
            
            // 遮罩覆盖完毕后，立即切换主题
            updateTheme(isDark);
            
            // 应用主题过渡动画
            docEl.classList.add('theme-transitioning');
            docEl.style.setProperty('--theme-transition-duration', themeDuration);
            
            wipe.style.clipPath = '';
            wipe.style.transition = '';
            wipe.style.backgroundColor = '';
            
            // 主题切换完成后，清理样式
            setTimeout(() => {
                docEl.classList.remove('theme-transitioning');
                docEl.style.removeProperty('--theme-transition-duration');
                wipeAnimating = false;
            }, 800);
        }
        wipe.addEventListener('transitionend', onFinish);
        setTimeout(onFinish, 650);
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

// ============ 顶栏标签页 + 上下文操作区切换 ============
function updateNavContext(panelId) {
    document.querySelectorAll('.nav-tab').forEach(tab => {
        tab.classList.toggle('active', tab.dataset.panel === panelId);
    });
    document.querySelectorAll('.nav-context').forEach(ctx => {
        ctx.classList.toggle('active', ctx.dataset.forPanel === panelId);
    });
}

function initNavTabs() {
    const tabs = document.querySelectorAll('.nav-tab[data-panel]');
    tabs.forEach(tab => {
        tab.addEventListener('click', function () {
            const panelId = this.dataset.panel;

            // 切换内容面板
            document.querySelectorAll('.content-panel').forEach(p => p.classList.remove('active'));
            const target = document.getElementById(panelId);
            if (target) target.classList.add('active');

            // 同步顶栏标签 + 操作区
            updateNavContext(panelId);

            // 同步侧边栏
            document.querySelectorAll('.menu-list li[data-panel]').forEach(si => {
                si.classList.toggle('active', si.dataset.panel === panelId);
            });

            // 面板特定加载
            if (panelId === 'panel-connections') loadConnections();
            if (panelId === 'panel-manage') loadExistingConfig();
            if (panelId === 'panel-log') { logUnread = 0; updateLogBadge(); }
        });
    });
}

// ============ 侧边栏菜单切换 ============
function initSidebarMenu() {
    const sidebar = document.querySelector('.sidebar');
    if (!sidebar) return;

    const sidebarItems = sidebar.querySelectorAll('.menu-list li[data-panel]');
    if (!sidebarItems.length) return;

    // 点击展开/收起侧边栏（点击 logo 区域）
    const logo = sidebar.querySelector('.logo');
    if (logo) {
        logo.addEventListener('click', (e) => {
            e.preventDefault();
            sidebar.classList.toggle('active');
        });
    }

    // 菜单项点击
    sidebarItems.forEach(item => {
        item.addEventListener('click', function (e) {
            e.preventDefault();
            const panelId = this.dataset.panel;

            // 更新侧边栏激活状态
            sidebarItems.forEach(si => si.classList.remove('active'));
            this.classList.add('active');

            // 同步顶部操作栏上下文
            updateNavContext(panelId);

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

            // 切换到密钥管理面板时自动加载
            if (panelId === 'panel-manage') {
                loadExistingConfig();
            }

            // 切换到日志面板时清零未读计数
            if (panelId === 'panel-log') {
                logUnread = 0;
                updateLogBadge();
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
    if (!box) return;
    const now = new Date().toLocaleTimeString();
    if (box.firstChild && box.firstChild.textContent.includes("等待操作")) box.innerHTML = "";
    const el = document.createElement("div");
    el.className = `log-entry log-${level}`;
    el.innerHTML = `<span class="log-time">${now}</span>${msg}`;
    box.appendChild(el);
    box.scrollTop = box.scrollHeight;
    // 不在日志面板时更新未读计数
    const logPanel = document.getElementById("panel-log");
    if (logPanel && !logPanel.classList.contains("active")) {
        logUnread++;
        updateLogBadge();
    }
}

function clearLog() {
    const box = document.getElementById("logBox");
    if (!box) return;
    box.innerHTML = '<div class="log-entry log-info">等待操作...</div>';
}

function updateLogBadge() {
    document.querySelectorAll('[data-panel="panel-log"]').forEach(el => {
        let badge = el.querySelector(".log-badge");
        if (logUnread > 0) {
            if (!badge) {
                badge = document.createElement("span");
                badge.className = "log-badge";
                // 侧边栏：插入到 .icon 内（已有 position:relative），角标定位在图标右上角
                const iconEl = el.querySelector('.icon');
                if (iconEl) {
                    iconEl.appendChild(badge);
                } else {
                    el.appendChild(badge);
                }
            }
            badge.textContent = logUnread > 99 ? "99+" : logUnread;
        } else if (badge) {
            badge.remove();
        }
    });
}

// ============ 顶栏快捷操作 ============
async function quickGenerateEd25519() {
    log("⚡ 快速生成 Ed25519 密钥 ...");
    try {
        const res = await fetch("/api/generate", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ key_type: "ed25519", key_size: 256, comment: "user@host", save_path: "" }),
        });
        const data = await res.json();
        if (data.success) {
            showToast("Ed25519 密钥已生成并保存！", "success");
            log("✓ Ed25519 密钥生成完成", "success");
            // 如果当前在生成面板，刷新显示
            if (typeof showKey === "function") showKey(data);
            if (typeof loadServerKeySelect === "function") loadServerKeySelect();
        } else {
            showToast(data.error || "生成失败", "error");
            log(data.error || "生成失败", "error");
        }
    } catch (err) {
        showToast("网络错误", "error");
        log("网络错误: " + err.message, "error");
    }
}

function toggleAddSection() {
    const details = document.getElementById("addSection");
    if (!details) return;
    // 如果不在连接面板，先切过去
    const connPanel = document.getElementById("panel-connections");
    if (connPanel && !connPanel.classList.contains("active")) {
        document.querySelectorAll('.content-panel').forEach(p => p.classList.remove('active'));
        connPanel.classList.add('active');
        // 同步侧边栏
        document.querySelectorAll('.menu-list li[data-panel]').forEach(si => {
            si.classList.toggle('active', si.dataset.panel === 'panel-connections');
        });
        updateNavContext("panel-connections");
        loadConnections();
    }
    details.open = !details.open;
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
    // SSH Config 模态框也支持 Escape 关闭
    const sshConfigModal = document.getElementById("sshConfigModal");
    if (e.key === "Escape" && sshConfigModal && sshConfigModal.style.display === "flex") {
        closeSSHConfigModal();
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
    // 自动保存到 ~/.ssh/ 目录
    body.save_path = "";  // 空字符串表示使用默认路径 ~/.ssh/

    try {
        const res = await fetch("/api/generate", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        });
        const data = await res.json();
        if (data.success) {
            showKey(data);
            showToast("密钥生成成功并已保存！", "success");
            // 刷新密钥列表
            loadServerKeySelect();
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
    if (section) section.style.display = "block";
    // 同时显示一键部署区
    const setup = document.getElementById("setupSection");
    if (setup) setup.style.display = "block";
    if (!section) return;
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

// ============ 加载服务器密钥下拉框 ============
async function loadServerKeySelect() {
    const select = document.getElementById("serverKeySelect");
    if (!select) return;

    try {
        const res = await fetch("/api/existing-keys");
        const data = await res.json();

        // 保留第一个选项（提示文本）
        const firstOption = select.options[0];
        select.innerHTML = "";
        select.appendChild(firstOption);

        if (data.success && data.keys && data.keys.length > 0) {
            data.keys.forEach(k => {
                const option = document.createElement("option");
                option.value = k.name;
                option.textContent = `${k.name} (${k.type.toUpperCase()})`;
                select.appendChild(option);
            });
            
            // 如果有密钥，更新提示文本
            firstOption.textContent = "-- 使用当前生成的密钥 --";
            firstOption.disabled = false;
        } else {
            firstOption.textContent = "暂无本地密钥，请在左侧生成";
            firstOption.disabled = true;
        }
    } catch (err) {
        console.error("加载密钥列表失败:", err);
    }
}

// ============ 上传 ============
async function uploadKey(target) {
    const pubKeyEl = document.getElementById("publicKeyText");
    if (!pubKeyEl) return;
    const pubKey = pubKeyEl.textContent.trim();

    let body = { target };
    
    // 如果选择了已有密钥，不需要传 public_key，后端会自己读取
    const selectedKey = document.getElementById("serverKeySelect").value;
    if (selectedKey && target === "server") {
        body.key_name = selectedKey;
    } else if (pubKey) {
        body.public_key = pubKey;
    } else {
        return showToast("请先生成密钥或选择已有密钥", "error");
    }
    
    if (target === "server") {
        body.host = document.getElementById("serverHost").value.trim();
        body.port = parseInt(document.getElementById("serverPort").value) || 22;
        body.username = document.getElementById("serverUser").value.trim();
        body.password = document.getElementById("serverPassword").value.trim();
    } else if (target === "github") {
        body.token = document.getElementById("githubToken").value.trim();
        body.title = document.getElementById("githubTitle").value.trim() || "SSH Key Manager";
    } else if (target === "gitlab") {
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
    const port = parseInt(document.getElementById("setupPort").value) || 22;
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

    try {
        const res = await fetch("/api/save-and-setup", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                host_alias: hostAlias,
                hostname: hostname,
                user: user,
                port: port,
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

function scrollToSetup() {
    const setup = document.getElementById("setupSection");
    if (!setup || setup.style.display === "none") {
        return showToast("请先在左侧生成密钥", "warning");
    }
    setup.scrollIntoView({ behavior: "smooth", block: "center" });
    document.getElementById("hostAlias").focus();
}

// ============ 已有密钥管理 ============
async function loadExistingConfig() {
    const container = document.getElementById("existingKeysContent");
    if (!container) return;
    container.innerHTML = '<span class="loading-text">⏳ 加载中...</span>';

    try {
        const configRes = await fetch("/api/ssh-config");
        const configData = await configRes.json();

        // /api/ssh-config 已包含 existing_keys，无需再调 /api/existing-keys
        const keys = configData.existing_keys || [];
        let html = "";

        // 密钥文件列表
        if (keys.length > 0) {
            html += '<div style="font-size:0.8rem;color:var(--text-secondary);margin-bottom:8px;">🔑 密钥文件</div>';
            keys.forEach(k => {
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
    const el = document.getElementById("publicKeyText");
    if (!el || !el.textContent.trim()) return showToast("请先生成密钥", "error");
    window.location.href = "/api/download-private-key";
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
                            <div class="conn-connect-split">
                        <button class="btn conn-connect-web" onclick="connectServer('${esc(c.alias)}', 'web')" ${keyValid ? '' : 'disabled'}>🌐 Web</button>
                        <button class="btn conn-connect-local" onclick="connectServer('${esc(c.alias)}', 'local')" ${keyValid ? '' : 'disabled'} title="在本地终端中打开">💻 本地</button>
                    </div>
                    <button class="btn conn-delete" onclick="deleteConn('${esc(c.id)}', '${esc(c.alias)}')" title="删除">🗑️</button>
                </div>
            </div>`;
        }).join("");
    } catch (e) {
        grid.innerHTML = `<div style="grid-column:1/-1;color:var(--danger);text-align:center;">加载失败: ${esc(e.message)}</div>`;
    }
}

async function connectServer(alias, mode = 'local') {
    alert(`[debug] connectServer called: alias=${alias}, mode=${mode}`);
    // mode: 'web' = WebSSH 浏览器终端, 'local' = 本地终端(wt.exe/Terminal)
    if (mode === 'web') {
        // WebSSH：切换到 WebSSH 面板并打开终端
        const websshItem = document.querySelector('.menu-list li[data-panel="panel-webssh"]');
        if (websshItem) {
            document.querySelectorAll(".menu-list li[data-panel]").forEach(si => si.classList.remove("active"));
            websshItem.classList.add("active");
            if (typeof updateNavContext === 'function') updateNavContext("panel-webssh");
            if (typeof switchPanel === 'function') switchPanel("panel-webssh");
        }
        if (typeof openWebSSHTerminal === 'function') {
            openWebSSHTerminal(alias);
        } else {
            showToast("WebSSH 模块未加载（请刷新页面）", "error");
        }
        return;
    }
    // mode === 'local'：本地终端（原有行为）
    showToast("正在打开本地终端...", "info");
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

// ============ SSH Config 编辑模态框 ============
let _sshKeyList = []; // 缓存 ~/.ssh 下的密钥列表

function openSSHConfigModal() {
    const modal = document.getElementById("sshConfigModal");
    if (!modal) return;
    modal.style.display = "flex";
    loadSSHConfigData();
}

async function loadSSHConfigData() {
    // 并行加载密钥列表和 config 条目
    const [keysRes, configRes] = await Promise.all([
        fetch("/api/existing-keys").then(r => r.json()).catch(() => ({ keys: [] })),
        fetch("/api/ssh-config").then(r => r.json()).catch(() => ({ config_entries: [] })),
    ]);
    // 缓存密钥文件名（仅私钥，去重）
    _sshKeyList = (keysRes.keys || []).map(k => `~/.ssh/${k.name}`);

    const container = document.getElementById("sshConfigEntries");
    if (!container) return;
    container.innerHTML = "";
    const entries = (configRes.config_entries || []);
    if (entries.length > 0) {
        entries.forEach(entry => addSSHConfigRow(entry));
    } else {
        addSSHConfigRow();
    }
}

function closeSSHConfigModal() {
    const modal = document.getElementById("sshConfigModal");
    if (modal) modal.style.display = "none";
}

async function loadSSHConfigEntries() {
    // 兼容旧调用 — 已由 loadSSHConfigData 替代
    await loadSSHConfigData();
}

function _buildIdentitySelect(currentValue) {
    // 构建 IdentityFile 下拉框，包含已有密钥 + 手动输入选项
    const options = ['<option value="">（不指定）</option>'];
    const seen = new Set();
    // 先列出 ~/.ssh 里的密钥
    _sshKeyList.forEach(path => {
        const selected = path === currentValue ? ' selected' : '';
        options.push(`<option value="${esc(path)}"${selected}>${esc(path)}</option>`);
        seen.add(path);
    });
    // 如果当前值不在列表中，也加进去
    if (currentValue && !seen.has(currentValue)) {
        options.push(`<option value="${esc(currentValue)}" selected>${esc(currentValue)}</option>`);
    }
    // 末尾加一个"手动输入"选项
    options.push('<option value="__manual__">✏️ 手动输入...</option>');
    return `<select class="form-input identity-select">${options.join('')}</select>`;
}

function addSSHConfigRow(entry = null) {
    const container = document.getElementById("sshConfigEntries");
    if (!container) return;
    // 移除加载提示
    const loading = container.querySelector(".loading-text");
    if (loading) loading.remove();

    const currentIdFile = entry?.identityfile || '';
    const row = document.createElement("div");
    row.className = "ssh-config-row";
    row.innerHTML = `
        <div class="form-group">
            <label>Host</label>
            <input type="text" class="form-input" placeholder="myserver" value="${esc(entry?.host || '')}">
        </div>
        <div class="form-group">
            <label>HostName</label>
            <input type="text" class="form-input" placeholder="192.168.1.100" value="${esc(entry?.hostname || '')}">
        </div>
        <div class="form-group">
            <label>User</label>
            <input type="text" class="form-input" placeholder="root" value="${esc(entry?.user || '')}">
        </div>
        <div class="form-group">
            <label>Port</label>
            <input type="number" class="form-input" placeholder="22" value="${entry?.port || 22}">
        </div>
        <div class="form-group identity-group">
            <label>IdentityFile</label>
            ${_buildIdentitySelect(currentIdFile)}
        </div>
        <button class="btn-row-remove" title="删除此行" onclick="this.closest('.ssh-config-row').remove()">✕</button>
    `;
    // 绑定 select change 事件：选"手动输入"时切换为 input
    const sel = row.querySelector('.identity-select');
    if (sel) {
        sel.addEventListener('change', function () {
            if (this.value === '__manual__') {
                const group = this.closest('.identity-group');
                const input = document.createElement('input');
                input.type = 'text';
                input.className = 'form-input identity-manual';
                input.placeholder = '~/.ssh/id_ed25519';
                group.innerHTML = '';
                group.appendChild(input);
                input.focus();
            }
        });
    }
    container.appendChild(row);
    container.scrollTop = container.scrollHeight;
}

async function saveSSHConfig() {
    const container = document.getElementById("sshConfigEntries");
    if (!container) return;
    const rows = container.querySelectorAll(".ssh-config-row");
    const entries = [];

    rows.forEach(row => {
        const inputs = row.querySelectorAll("input");
        const host = inputs[0].value.trim();
        const hostname = inputs[1].value.trim();
        const user = inputs[2].value.trim();
        const port = parseInt(inputs[3].value) || 22;
        // IdentityFile: 优先取 select，否则取手动 input
        const sel = row.querySelector('.identity-select');
        const manual = row.querySelector('.identity-manual');
        let identityfile = '';
        if (sel && sel.value && sel.value !== '__manual__') {
            identityfile = sel.value;
        } else if (manual) {
            identityfile = manual.value.trim();
        }
        if (host && hostname && user) {
            entries.push({ host, hostname, user, port, identityfile });
        }
    });

    if (entries.length === 0) {
        showToast("请至少填写一个有效条目", "error");
        return;
    }

    try {
        const res = await fetch("/api/ssh-config/batch", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ entries }),
        });
        const data = await res.json();
        if (data.success) {
            showToast(`已保存 ${data.count || entries.length} 个条目`, "success");
            closeSSHConfigModal();
            // 刷新密钥管理面板
            if (typeof loadExistingConfig === "function") loadExistingConfig();
        } else {
            showToast(data.error || "保存失败", "error");
        }
    } catch (err) {
        showToast("请求失败: " + err.message, "error");
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

// ============ 面板切换（WebSSH 调用）============
function switchPanel(panelId) {
    document.querySelectorAll('.content-panel').forEach(p => p.classList.remove('active'));
    const target = document.getElementById(panelId);
    if (target) target.classList.add('active');
    // 切换到连接历史面板时自动渲染
    if (panelId === 'panel-webssh-history' && typeof renderWebSSHHistory === 'function') {
        renderWebSSHHistory();
    }
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

    // SSH Config 编辑模态框：点击背景关闭
    const sshConfigModal = document.getElementById("sshConfigModal");
    if (sshConfigModal) {
        sshConfigModal.addEventListener("click", (e) => {
            if (e.target === sshConfigModal) closeSSHConfigModal();
        });
    }

    initThemeToggle();
    initNavTabs();
    initSidebarMenu();
    initSSE();
    updateNavContext("panel-generate");
    loadExistingConfig();
    loadServerKeySelect();  // 加载密钥列表
});

// ============ 本地终端下拉菜单（顶部导航栏）============
function toggleLocalTerminalDropdown() {
    const dd = document.getElementById("localTerminalDropdown");
    if (!dd) return;
    if (dd.style.display === "block") {
        dd.style.display = "none";
        return;
    }
    loadLocalTerminalConnections();
    dd.style.display = "block";
}

async function loadLocalTerminalConnections() {
    const dd = document.getElementById("localTerminalDropdown");
    if (!dd) return;
    dd.innerHTML = '<div class="local-terminal-loading">加载中...</div>';
    try {
        const res = await fetch("/api/connections");
        const data = await res.json();
        if (!data.success || !data.connections || data.connections.length === 0) {
            dd.innerHTML = '<div class="local-terminal-empty">暂无保存的连接，请先在「我的连接」中添加</div>';
            return;
        }
        let items = data.connections.map(c => `
            <div class="local-terminal-item" onclick="openLocalTerminal('${esc(c.alias)}')">
                <img src="/static/ionicons/svg/terminal-outline.svg" alt="icon" class="local-terminal-icon-img" />
                <span class="local-terminal-alias">${esc(c.alias)}</span>
                <span class="local-terminal-addr">${esc(c.user)}@${esc(c.hostname)}</span>
            </div>
        `).join("");
        items += `
            <div class="local-terminal-settings-item" onclick="openLocalTerminalSettings()">
                <img src="/static/ionicons/svg/settings-outline.svg" alt="icon" class="local-terminal-settings-icon" />
                <span>终端设置</span>
            </div>
        `;
        dd.innerHTML = items;
    } catch (e) {
        dd.innerHTML = '<div class="local-terminal-empty">加载失败</div>';
    }
}

async function openLocalTerminal(alias) {
    const dd = document.getElementById("localTerminalDropdown");
    if (dd) dd.style.display = "none";
    const terminalPath = loadLocalTerminalPath();
    showToast(`正在打开本地终端: ${alias}`, "info");
    try {
        const res = await fetch("/api/connections/connect", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ alias, terminal_path: terminalPath }),
        });
        const data = await res.json();
        showToast(data.message, data.success ? "success" : "error");
    } catch (e) {
        showToast("请求失败: " + e.message, "error");
    }
}


// 点击页面其他地方时关闭下拉
document.addEventListener("click", (e) => {
    const wrap = document.getElementById("sidebarLocalBtn");
    const dd = document.getElementById("localTerminalDropdown");
    if (dd && dd.style.display === 'block' && wrap && !wrap.contains(e.target) && !dd.contains(e.target)) {
        dd.style.display = "none";
    }
});

// ============ WebSSH 面板：添加主机表单 ============
function toggleWebSSHAddHost() {
    const section = document.getElementById("websshAddHostSection");
    if (!section) return;
    const isVisible = section.style.display !== "none";
    section.style.display = isVisible ? "none" : "block";
    if (!isVisible) {
        // 自动聚焦第一个输入框
        const firstInput = section.querySelector("input");
        if (firstInput) setTimeout(() => firstInput.focus(), 100);
    }
}

async function submitWebSSHAddHost() {
    const alias = document.getElementById("websshAlias").value.trim();
    const hostname = document.getElementById("websshHostname").value.trim();
    const port = parseInt(document.getElementById("websshPort").value) || 22;
    const username = document.getElementById("websshUsername").value.trim();
    const password = document.getElementById("websshPassword").value;
    const identityFile = document.getElementById("websshIdentityFile").value.trim();

    if (!alias || !hostname || !username) {
        return showToast("请填写连接名称、远程地址和账户名", "warning");
    }

    // 先保存到 SSH Config
    try {
        const res = await fetch("/api/connections", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                alias,
                hostname,
                port,
                user: username,
                identity_file: identityFile || undefined,
            }),
        });
        const data = await res.json();
        if (!data.success) {
            return showToast("保存失败: " + (data.error || "未知错误"), "error");
        }
        showToast("主机已保存，正在连接到 WebSSH...", "success");
    } catch (e) {
        return showToast("保存失败: " + e.message, "error");
    }

    // 保存成功后，隐藏表单并连接到 WebSSH
    document.getElementById("websshAddHostSection").style.display = "none";
    document.getElementById("websshAlias").value = "";
    document.getElementById("websshHostname").value = "";
    document.getElementById("websshPort").value = "22";
    document.getElementById("websshUsername").value = "";
    document.getElementById("websshPassword").value = "";
    document.getElementById("websshIdentityFile").value = "";

    // 刷新连接列表（供「我的连接」面板使用）
    if (typeof loadConnections === "function") loadConnections();

    // 直接打开 WebSSH 终端
    if (typeof openWebSSHTerminal === "function") {
        openWebSSHTerminal(alias);
    }
}


// ============ WebSSH 快速连接 ============
function submitQuickConnect() {
    const hostname = document.getElementById('quickHostname').value.trim();
    const port = parseInt(document.getElementById('quickPort').value) || 22;
    const username = document.getElementById('quickUsername').value.trim();
    const alias = document.getElementById('quickAlias').value.trim();
    const password = document.getElementById('quickPassword').value;
    const identityFile = document.getElementById('quickIdentityFile').value.trim();

    if (!hostname) return showToast('请输入远程地址', 'warning');
    if (!username) return showToast('请输入账户名', 'warning');

    if (typeof switchPanel === 'function') switchPanel('panel-webssh');
    if (typeof initWebSSHTerminal === 'function' && !window._webssh_term) initWebSSHTerminal();

    const params = {
        alias: alias || hostname,
        hostname: hostname,
        port: port,
        username: username,
        password: password,
        identity_file: identityFile,
    };

    if (typeof saveWebSSHHistory === 'function') saveWebSSHHistory(params);
    if (typeof connectWebSSH === 'function') connectWebSSH(params);
    else showToast('WebSSH 模块未加载', 'error');
}

// ============ WebSSH 连接历史 ============
function saveWebSSHHistory(entry) {
    try {
        const history = JSON.parse(localStorage.getItem('webssh_history') || '[]');
        const idx = history.findIndex(h => h.hostname === entry.hostname && h.username === entry.username);
        if (idx >= 0) history.splice(idx, 1);
        history.unshift({
            alias: entry.alias || '',
            hostname: entry.hostname,
            port: entry.port || 22,
            username: entry.username,
            identity_file: entry.identity_file || '',
            timestamp: Date.now(),
        });
        if (history.length > 20) history.length = 20;
        localStorage.setItem('webssh_history', JSON.stringify(history));
    } catch(e) {}
}

function loadWebSSHHistory() {
    try {
        return JSON.parse(localStorage.getItem('webssh_history') || '[]');
    } catch(e) { return []; }
}

function renderWebSSHHistory() {
    const container = document.getElementById('websshHistoryList');
    if (!container) return;
    const history = loadWebSSHHistory();
    if (!history.length) {
        container.innerHTML = '<div class="empty-hint">暂无连接历史<br><span class="text-secondary">通过「快速连接」或「我的连接」发起 WebSSH 连接后，此处会自动记录</span></div>';
        return;
    }
    container.innerHTML = '';
    history.forEach(function(h, i) {
        const card = document.createElement('div');
        card.className = 'conn-card webssh-history-card';
        const nameStr = (h.alias || h.hostname).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
        const userStr = (h.username || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
        const hostStr = (h.hostname || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
        const portStr = (h.port || 22);
        card.innerHTML =
            '<div class="conn-icon"><img src="/static/icons/history.png" alt="历史" class="history-icon" /></div>' +
            '<div class="conn-info">' +
                '<div class="conn-name">' + nameStr + '</div>' +
                '<div class="conn-detail">' + userStr + '@' + hostStr + ':' + portStr + '</div>' +
                '<div class="conn-time">' + new Date(h.timestamp).toLocaleString() + '</div>' +
            '</div>' +
            '<div class="conn-actions">' +
                '<button class="btn btn-sm btn-primary" onclick="connectFromHistory(' + i + ')">连接</button>' +
            '</div>';
        container.appendChild(card);
    });
}

function connectFromHistory(index) {
    const history = loadWebSSHHistory();
    if (!history[index]) return;
    const h = history[index];
    if (typeof switchPanel === 'function') switchPanel('panel-webssh');
    if (typeof initWebSSHTerminal === 'function' && !window._webssh_term) initWebSSHTerminal();
    const params = {
        alias: h.alias,
        hostname: h.hostname,
        port: h.port || 22,
        username: h.username,
        identity_file: h.identity_file || '',
    };
    if (typeof connectWebSSH === 'function') connectWebSSH(params);
}

function clearWebSSHHistory() {
    if (!confirm('确定清空所有连接历史？')) return;
    localStorage.removeItem('webssh_history');
    renderWebSSHHistory();
    showToast('连接历史已清空', 'success');
}

// ============ 本地终端设置 ============
function openLocalTerminalSettings() {
    const saved = loadLocalTerminalPath();
    const input = document.getElementById("localTerminalPath");
    if (input) input.value = saved || "wt.exe";
    if (typeof switchPanel === 'function') switchPanel("panel-local-terminal-settings");
    // 更新导航栏上下文
    document.querySelectorAll(".nav-context").forEach(n => n.classList.remove("active"));
    const ctx = document.querySelector('.nav-context[data-for-panel="panel-local-terminal-settings"]');
    if (ctx) ctx.classList.add("active");
}

function saveLocalTerminalPath() {
    const input = document.getElementById("localTerminalPath");
    if (!input) return;
    const path = input.value.trim() || "wt.exe";
    try {
        localStorage.setItem("local_terminal_path", path);
        showToast("终端路径已保存: " + path, "success");
    } catch(e) {
        showToast("保存失败", "error");
    }
}

function loadLocalTerminalPath() {
    try {
        return localStorage.getItem("local_terminal_path") || "wt.exe";
    } catch(e) {
        return "wt.exe";
    }
}

function setLocalTerminalPreset(preset) {
    const input = document.getElementById("localTerminalPath");
    if (!input) return;
    if (preset === "git-bash") {
        // 尝试常见 Git Bash 路径
        input.value = "C:\Program Files\Git\bin\bash.exe";
        return;
    }
    input.value = preset;
}

// 检测终端路径是否有效
async function checkTerminalPath() {
    const input = document.getElementById("localTerminalPath");
    const resultDiv = document.getElementById("terminalPathCheckResult");
    if (!input || !resultDiv) return;

    const path = input.value.trim();
    if (!path) {
        resultDiv.style.display = "block";
        resultDiv.className = "path-check-result invalid";
        resultDiv.textContent = "❌ 请输入终端路径";
        return;
    }

    resultDiv.style.display = "block";
    resultDiv.className = "path-check-result";
    resultDiv.textContent = "⏳ 检测中...";

    try {
        const resp = await fetch("/api/check-terminal-path", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({path: path})
        });
        const data = await resp.json();

        if (data.valid) {
            resultDiv.className = "path-check-result valid";
            resultDiv.innerHTML = data.message;
            if (data.path && data.path !== path) {
                resultDiv.innerHTML += `<span class="detected-path">${data.path}</span>`;
            }
        } else {
            resultDiv.className = "path-check-result invalid";
            resultDiv.textContent = data.message;
        }
    } catch(e) {
        resultDiv.className = "path-check-result invalid";
        resultDiv.textContent = "❌ 检测失败：" + e.message;
    }
}

// 自动检测系统中已安装的终端
async function autoDetectTerminal() {
    const resultDiv = document.getElementById("terminalAutoDetectResult");
    if (!resultDiv) return;

    resultDiv.style.display = "block";
    resultDiv.className = "path-check-result";
    resultDiv.textContent = "⏳ 检测中...";

    // 常见终端路径（按优先级）
    const candidates = [
        // Windows Terminal
        {name: "Windows Terminal", path: "wt.exe"},
        {name: "Windows Terminal (商店版)", path: "C:\\Program Files\\WindowsApps\\Microsoft.WindowsTerminal_8wekyb3d8bbwe\\wt.exe"},
        // PowerShell
        {name: "PowerShell", path: "powershell.exe"},
        {name: "PowerShell 7", path: "pwsh.exe"},
        // CMD
        {name: "CMD", path: "cmd.exe"},
        // Git Bash
        {name: "Git Bash", path: "C:\\Program Files\\Git\\bin\\bash.exe"},
        {name: "Git Bash (x86)", path: "C:\\Program Files (x86)\\Git\\bin\\bash.exe"},
    ];

    const found = [];

    for (const c of candidates) {
        try {
            const resp = await fetch("/api/check-terminal-path", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({path: c.path})
            });
            const data = await resp.json();
            if (data.valid) {
                found.push({name: c.name, path: data.path || c.path});
            }
        } catch(e) {
            // 忽略单个检测失败
        }
    }

    if (found.length > 0) {
        resultDiv.className = "path-check-result valid";
        let html = `✅ 检测到 ${found.length} 个终端：<br>`;
        found.forEach((t, i) => {
            // 转义路径中的反斜杠，用于 JS 字符串字面量
            const escapedPath = t.path.replace(/\\/g, '\\\\');
            html += `<div style="margin:6px 0; cursor:pointer; text-decoration:underline;" onclick="selectDetectedTerminal('${escapedPath}')">${t.name} <span class="detected-path">${t.path}</span></div>`;
        });
        resultDiv.innerHTML = html;
    } else {
        resultDiv.className = "path-check-result invalid";
        resultDiv.textContent = "❌ 未检测到常见终端，请手动输入路径";
    }
}

// 选择自动检测到的终端
function selectDetectedTerminal(path) {
    const input = document.getElementById("localTerminalPath");
    if (input) {
        input.value = path;
        showToast("已选择终端路径", "success");
    }
}

// ============ 从导航栏启动本地终端下拉 ============
async function toggleLocalTerminalDropdownFromNav() {
    const dd = document.getElementById("localTerminalDropdownNav");
    if (!dd) return;
    if (dd.style.display === "block") {
        dd.style.display = "none";
        return;
    }
    dd.innerHTML = '<div class="local-terminal-loading">加载中...</div>';
    try {
        const res = await fetch("/api/connections");
        const data = await res.json();
        if (!data.success || !data.connections || data.connections.length === 0) {
            dd.innerHTML = '<div class="local-terminal-empty">暂无保存的连接，请先在「我的连接」中添加</div>';
        } else {
            let items = data.connections.map(c => {
                const alias = (c.alias || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
                const user = (c.user || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
                const host = (c.hostname || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
                return '<div class="local-terminal-item" onclick="openLocalTerminal(\'' + alias + '\')">' +
                    '<img src="/static/ionicons/svg/terminal-outline.svg" alt="icon" class="local-terminal-icon-img" />' +
                    '<span class="local-terminal-alias">' + alias + '</span>' +
                    '<span class="local-terminal-addr">' + user + '@' + host + '</span>' +
                    '</div>';
            }).join('');
            dd.innerHTML = items;
        }
    } catch(e) {
        dd.innerHTML = '<div class="local-terminal-empty">加载失败</div>';
    }
    dd.style.display = "block";
}

// 点击页面其它区域时关闭下拉
document.addEventListener('click', function(e) {
    const dd = document.getElementById("localTerminalDropdownNav");
    if (dd && !dd.contains(e.target) && !e.target.closest('.nav-action-btn')) {
        dd.style.display = "none";
    }
});

// 页面加载后渲染历史 + 加载终端路径
// 页面加载后渲染历史
document.addEventListener('DOMContentLoaded', function() {
    setTimeout(renderWebSSHHistory, 500);
    setTimeout(() => {
        const p = loadLocalTerminalPath();
        const input = document.getElementById("localTerminalPath");
        if (input) input.value = p;
    }, 600);
});
