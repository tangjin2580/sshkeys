// ============ 状态 ============
let sseSource = null;
let currentDeployProps = null;

// ============ 初始化 ============
document.addEventListener("DOMContentLoaded", () => {
    initSSE();
    loadExistingConfig();
});

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
        document.getElementById("btnSetupGo").disabled = false;
        loadExistingConfig();
    });
    sseSource.onerror = () => log("连接中断", "warning");
}

// ============ 日志 ============
function log(msg, level = "info") {
    const box = document.getElementById("logBox");
    const section = document.getElementById("logSection");
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
    document.getElementById("modalTitle").textContent = title;
    document.getElementById("modalBody").textContent = body;
    modalCallback = onConfirm;
    document.getElementById("confirmModal").style.display = "flex";
    document.getElementById("modalConfirmBtn").focus();
}

function closeModal() {
    document.getElementById("confirmModal").style.display = "none";
    modalCallback = null;
}

document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && document.getElementById("confirmModal").style.display === "flex") {
        closeModal();
    }
});

// 弹窗背景点击关闭
document.getElementById("confirmModal").addEventListener("click", (e) => {
    if (e.target === document.getElementById("confirmModal")) closeModal();
});

document.getElementById("modalConfirmBtn").addEventListener("click", () => {
    if (modalCallback) modalCallback();
    closeModal();
});

// ============ 密钥生成 ============
async function generateKey() {
    const btn = document.getElementById("btnGenerate");
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
            document.getElementById("uploadSection").style.display = "block";
            document.getElementById("setupSection").style.display = "block";
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
    document.getElementById("keyDisplaySection").style.display = "block";
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
    const pubKey = document.getElementById("publicKeyText").textContent.trim();
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
    const pubKey = document.getElementById("publicKeyText").textContent.trim();

    if (!hostAlias || !hostname || !user) return showToast("请填写完整信息", "error");
    if (!pubKey) return showToast("请先生成密钥", "error");

    const btn = document.getElementById("btnSetupGo");
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
    const txt = document.getElementById("publicKeyText").textContent;
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

// ============ 工具 ============
function esc(s) {
    if (!s) return "";
    return s.replace(/\\/g, "\\\\").replace(/'/g, "\\'").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function escHtml(s) {
    if (!s) return "";
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}