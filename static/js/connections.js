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

// ============ 搜索过滤 + 批量操作 ============
let _batchMode = false;

function filterConnections() {
    const query = (document.getElementById("connSearch")?.value || "").toLowerCase();
    const cards = document.querySelectorAll("#connectionsGrid .conn-card");
    cards.forEach(card => {
        const text = card.textContent.toLowerCase();
        card.style.display = text.includes(query) ? "" : "none";
    });
}

function toggleBatchMode() {
    _batchMode = true;
    const grid = document.getElementById("connectionsGrid");
    if (!grid) return;

    // 给每个卡片添加 checkbox
    grid.querySelectorAll(".conn-card").forEach(card => {
        if (card.querySelector(".batch-checkbox")) return;
        const cb = document.createElement("input");
        cb.type = "checkbox";
        cb.className = "batch-checkbox";
        cb.style.cssText = "position:absolute;top:8px;right:8px;width:18px;height:18px;cursor:pointer;accent-color:var(--primary);";
        card.style.position = "relative";
        card.appendChild(cb);
    });

    // 切换按钮显示状态
    document.getElementById("batchToggleBtn").style.display = "none";
    document.getElementById("batchDeleteBtn").style.display = "";
    document.getElementById("batchUploadBtn").style.display = "";
    document.getElementById("batchCancelBtn").style.display = "";
}

function cancelBatchMode() {
    _batchMode = false;
    // 移除所有 checkbox
    document.querySelectorAll(".batch-checkbox").forEach(cb => cb.remove());

    // 恢复按钮
    document.getElementById("batchToggleBtn").style.display = "";
    document.getElementById("batchDeleteBtn").style.display = "none";
    document.getElementById("batchUploadBtn").style.display = "none";
    document.getElementById("batchCancelBtn").style.display = "none";
}

function _getSelectedBatchAliases() {
    const aliases = [];
    document.querySelectorAll("#connectionsGrid .conn-card").forEach(card => {
        const cb = card.querySelector(".batch-checkbox");
        if (cb && cb.checked) {
            const aliasEl = card.querySelector(".conn-alias code");
            if (aliasEl) aliases.push(aliasEl.textContent.trim());
        }
    });
    return aliases;
}

function batchDelete() {
    const aliases = _getSelectedBatchAliases();
    if (aliases.length === 0) return showToast("请先勾选要删除的连接", "error");

    showConfirm(
        `批量删除 ${aliases.length} 个连接？`,
        `将删除: ${aliases.join(", ")}\n此操作不可撤销。`,
        async () => {
            let ok = 0, fail = 0;
            for (const alias of aliases) {
                try {
                    const res = await fetch(`/api/connections/by-alias/${encodeURIComponent(alias)}`, { method: "DELETE" });
                    const data = await res.json();
                    data.success ? ok++ : fail++;
                } catch { fail++; }
            }
            showToast(`删除完成: ${ok} 成功${fail ? `, ${fail} 失败` : ""}`, fail ? "error" : "success");
            cancelBatchMode();
            loadConnections();
        }
    );
}

function batchUpload() {
    const aliases = _getSelectedBatchAliases();
    if (aliases.length === 0) return showToast("请先勾选要上传的连接", "error");
    showToast(`批量上传暂未实现，请逐个连接后使用"保存并部署"功能`, "info");
}
