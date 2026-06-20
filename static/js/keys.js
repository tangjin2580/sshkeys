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
                    // 使用统一错误处理（自动展示建议信息）
                    handleApiError(data, "密钥生成失败");
                    log(data.error || "生成失败", "error");
                }
            } catch (err) {
                showToast("网络请求失败: " + err.message, "error");
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
    if (data.fingerprint) {
        info.innerHTML += `<span>指纹: ${data.fingerprint}</span>`;
        // 同时填充专用指纹展示区
        const fpSection = document.getElementById("keyFingerprint");
        const fpText = document.getElementById("fingerprintText");
        if (fpSection && fpText) {
            fpText.textContent = data.fingerprint;
            fpSection.style.display = "flex";
        }
    }
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

function copyFingerprint() {
    const el = document.getElementById("fingerprintText");
    if (!el) return;
    navigator.clipboard.writeText(el.textContent).then(() => showToast("已复制指纹", "success")).catch(() => showToast("复制失败", "error"));
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
