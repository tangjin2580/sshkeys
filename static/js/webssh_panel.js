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
