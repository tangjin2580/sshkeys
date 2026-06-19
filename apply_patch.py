js_path = 'G:/code/sshkeys/static/script.js'
js = open(js_path, 'r', encoding='utf-8').read()

# ===== 1. 替换 loadConnections() =====
old_fn = '''async function loadConnections() {
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
}'''

new_code = '''// ===== 加载连接列表（API → 缓存 → 渲染） =====
async function loadConnections() {
    const grid = document.getElementById("connectionsGrid");
    if (!grid) return;
    try {
        const res = await fetch("/api/connections");
        const data = await res.json();
        if (!data.success) {
            grid.innerHTML = `<div style="grid-column:1/-1;color:var(--danger);text-align:center;">加载失败: ${esc(data.error || "未知错误")}</div>`;
            return;
        }
        _connectionsCache = data.connections || [];
        renderConnections();
    } catch (e) {
        grid.innerHTML = `<div style="grid-column:1/-1;color:var(--danger);text-align:center;">加载失败: ${esc(e.message)}</div>`;
    }
}

// ===== 渲染连接列表（支持搜索过滤 + 批量模式） =====
function renderConnections() {
    const grid = document.getElementById("connectionsGrid");
    if (!grid) return;

    const keyword = (document.getElementById("connSearch")?.value || "").toLowerCase();
    const filtered = keyword
        ? _connectionsCache.filter(c =>
            (c.alias || "").toLowerCase().includes(keyword) ||
            (c.hostname || "").toLowerCase().includes(keyword) ||
            (c.user || "").toLowerCase().includes(keyword)
          )
        : _connectionsCache;

    if (filtered.length === 0) {
        grid.innerHTML = `
        <div class="empty-state" style="grid-column:1/-1;">
            <div class="icon">📭</div>
            <p>${keyword ? "没有匹配的连接" : "还没有保存任何连接"}</p>
            ${keyword ? "" : '<p style="font-size:0.8rem;">去<a href="/">首页</a>生成密钥并部署，或点下方「手动添加」</p>'}
        </div>`;
        return;
    }

    grid.innerHTML = filtered.map(c => {
        const keyValid = c.key_valid !== false;
        const keyClass = c.identity_file ? (keyValid ? 'valid' : 'invalid') : '';
        const keyText = c.identity_file
            ? (keyValid ? '🔑 ' + esc(c.identity_file) : '密钥文件不存在: ' + esc(c.identity_file))
            : '⚡ 无需额外配置';
        const isSelected = _selectedConns.has(c.id);
        const checkboxHtml = _batchMode
            ? `<input type="checkbox" class="batch-checkbox" ${isSelected ? "checked" : ""} onchange="toggleConnSelect('${c.id}', this.checked)" />`
            : '';
        return `
        <div class="conn-card ${isSelected ? 'selected' : ''}">
            ${checkboxHtml}
            <div class="conn-alias"><code>${esc(c.alias)}</code></div>
            <div class="conn-addr">${esc(c.user)}@${esc(c.hostname)}${c.port && c.port !== 22 ? ':' + c.port : ''}</div>
            <div class="conn-key ${keyClass}">${keyText}</div>
            <div class="conn-actions">
                <button class="btn conn-connect" onclick="connectServer('${esc(c.alias)}')" ${keyValid && !_batchMode ? '' : 'disabled'}>${keyValid ? '🚀 一键连接' : '🔒 密钥缺失'}</button>
                ${!_batchMode ? `<button class="btn conn-delete" onclick="deleteConn('${esc(c.id)}', '${esc(c.alias)}')" title="删除">🗑️</button>` : ''}
            </div>
        </div>`;
    }).join("");
}

// ===== 搜索过滤 =====
function filterConnections() {
    renderConnections();
}

// ===== 批量模式切换 =====
function toggleBatchMode() {
    _batchMode = !_batchMode;
    _selectedConns.clear();
    const btn = document.getElementById("batchToggleBtn");
    if (btn) btn.textContent = _batchMode ? "☑️ 已选 0" : "☐ 批量";
    document.getElementById("batchDeleteBtn").style.display = _batchMode ? "inline-flex" : "none";
    document.getElementById("batchUploadBtn").style.display = _batchMode ? "inline-flex" : "none";
    document.getElementById("batchCancelBtn").style.display = _batchMode ? "inline-flex" : "none";
    renderConnections();
}

function cancelBatchMode() {
    _batchMode = false;
    _selectedConns.clear();
    const btn = document.getElementById("batchToggleBtn");
    if (btn) btn.textContent = "☐ 批量";
    document.getElementById("batchDeleteBtn").style.display = "none";
    document.getElementById("batchUploadBtn").style.display = "none";
    document.getElementById("batchCancelBtn").style.display = "none";
    renderConnections();
}

function toggleConnSelect(id, checked) {
    if (checked) {
        _selectedConns.add(id);
    } else {
        _selectedConns.delete(id);
    }
    const btn = document.getElementById("batchToggleBtn");
    if (btn) btn.textContent = `☑️ 已选 ${_selectedConns.size}`;
}

// ===== 批量删除 =====
async function batchDelete() {
    if (_selectedConns.size === 0) return showToast("请先选择连接", "warning");
    showConfirm(
        `确认删除 ${_selectedConns.size} 个连接？`,
        "将从连接管理中移除选中的连接（不会删除 ~/.ssh 中的密钥文件）",
        async () => {
            let success = 0, fail = 0;
            for (const id of _selectedConns) {
                try {
                    const res = await fetch(`/api/connections/${id}`, { method: "DELETE" });
                    const data = await res.json();
                    if (data.success) success++; else fail++;
                } catch (e) { fail++; }
            }
            showToast(`完成：成功 ${success}，失败 ${fail}`, fail === 0 ? "success" : "warning");
            cancelBatchMode();
            loadConnections();
        }
    );
}

// ===== 批量上传公钥 =====
async function batchUpload() {
    if (_selectedConns.size === 0) return showToast("请先选择连接", "warning");
    const pubKeyEl = document.getElementById("publicKeyText");
    const pubKey = pubKeyEl?.textContent || "";
    if (!pubKey) return showToast("请先在首页生成或选择密钥", "error");

    const password = prompt("服务器密码（所有选中服务器使用同一密码）：");
    if (password === null) return;

    showToast(`开始批量上传到 ${_selectedConns.size} 台服务器...`, "info");
    let success = 0, fail = 0;
    const selectedIds = [..._selectedConns];
    for (const id of selectedIds) {
        const conn = _connectionsCache.find(c => c.id === id);
        if (!conn) { fail++; continue; }
        try {
            const res = await fetch("/api/upload", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    target: "server",
                    host: conn.hostname,
                    port: conn.port || 22,
                    username: conn.user,
                    password: password || undefined,
                    public_key: pubKey,
                }),
            });
            const data = await res.json();
            if (data.success) success++; else fail++;
        } catch (e) { fail++; }
    }
    showToast(`批量上传完成：成功 ${success}，失败 ${fail}`, fail === 0 ? "success" : "warning");
}'''

if old_fn in js:
    js = js.replace(old_fn, new_code, 1)
    print("OK: loadConnections replaced")
else:
    print("FAIL: old function not found")
    # 调试：找第一个差异
    idx = js.find('async function loadConnections()')
    if idx >= 0:
        # 比较实际内容和 old_fn
        actual = js[idx:idx+500]
        print(f"Actual starts with: {repr(actual[:200])}")

open(js_path, 'w', encoding='utf-8').write(js)
print("JS written.")
