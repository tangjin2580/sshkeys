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
        if (data.success) {
            showToast(data.message || "终端已打开", "success");
        } else {
            // 使用统一错误处理（自动展示建议信息）
            handleApiError(data, "启动终端失败");
        }
    } catch (e) {
        showToast("网络请求失败: " + e.message, "error");
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
        input.value = "C:\\Program Files\\Git\\bin\\bash.exe";
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

    // 1. 获取当前平台信息
    let platformInfo = {platform: "unknown", platform_name: "Unknown"};
    try {
        const resp = await fetch("/api/platform-info");
        platformInfo = await resp.json();
    } catch(e) {
        console.warn("无法获取平台信息，使用默认检测", e);
    }

    // 2. 根据平台选择终端候选列表
    let candidates = [];
    const platform = platformInfo.platform;

    if (platform === "win32") {
        // Windows 终端
        candidates = [
            {name: "Windows Terminal", path: "wt.exe"},
            {name: "PowerShell", path: "powershell.exe"},
            {name: "PowerShell 7", path: "pwsh.exe"},
            {name: "CMD", path: "cmd.exe"},
            {name: "Git Bash", path: "C:\\Program Files\\Git\\bin\\bash.exe"},
            {name: "Git Bash (x86)", path: "C:\\Program Files (x86)\\Git\\bin\\bash.exe"},
        ];
    } else if (platform === "darwin") {
        // macOS 终端
        candidates = [
            {name: "Terminal.app", path: "/Applications/Utilities/Terminal.app"},
            {name: "iTerm2", path: "/Applications/iTerm.app"},
            {name: "Alacritty", path: "alacritty"},
            {name: "kitty", path: "kitty"},
        ];
    } else if (platform === "linux") {
        // Linux 终端
        candidates = [
            {name: "GNOME Terminal", path: "gnome-terminal"},
            {name: "Konsole", path: "konsole"},
            {name: "Xterm", path: "xterm"},
            {name: "Alacritty", path: "alacritty"},
            {name: "kitty", path: "kitty"},
            {name: "Terminator", path: "terminator"},
            {name: "xfce4-terminal", path: "xfce4-terminal"},
        ];
    } else {
        // 未知平台，尝试常见终端
        candidates = [
            {name: "系统默认终端", path: "xterm"},
            {name: "bash", path: "bash"},
        ];
    }

    // 3. 检测每个候选终端
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

    // 4. 显示检测结果
    if (found.length > 0) {
        resultDiv.className = "path-check-result valid";
        let html = `✅ 检测到 ${found.length} 个终端 (${platformInfo.platform_name || platform})：<br>`;
        found.forEach((t, i) => {
            const escapedPath = t.path.replace(/\\/g, '\\\\').replace(/'/g, "\\'");
            html += `<div style="margin:6px 0; cursor:pointer; text-decoration:underline;" onclick="selectDetectedTerminal('${escapedPath}')">${t.name} <span class="detected-path">${t.path}</span></div>`;
        });
        resultDiv.innerHTML = html;
    } else {
        resultDiv.className = "path-check-result invalid";
        resultDiv.innerHTML = `❌ 未检测到常见终端<br><small>当前平台: ${platformInfo.platform_name || platform}<br>请手动输入终端路径</small>`;
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
