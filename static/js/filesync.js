// ============================================================
//  filesync.js — 文件同步模块前端逻辑
//  依赖 core.js 的 showToast() / escHtml()
// ============================================================

let fsCurrentPath = "";
let fsSelected = new Set();
let fsLastClicked = null;
let fsItems = [];          // [{local, remote}]
let fsPollTimer = null;
let fsEntriesCache = [];
let fsPage = 1;            // 当前页（1 起）
let fsPageSize = 100;      // 每页条数
let fsTotal = 0;           // 当前目录总条目数
let fsTotalPages = 1;      // 总页数

// ---------------- 配置读取 ----------------
function fsGetConfig() {
    return {
        host: document.getElementById("fsHost").value.trim(),
        port: parseInt(document.getElementById("fsPort").value, 10) || 22,
        username: document.getElementById("fsUser").value.trim(),
        password: document.getElementById("fsPass").value,
        remote_base: document.getElementById("fsRemote").value.trim(),
        identity_file: document.getElementById("fsIdentity").value.trim(),
    };
}

// ---------------- 已保存连接下拉 ----------------
function fsLoadConnections() {
    fetch("/api/filesync/connections")
        .then(r => r.json())
        .then(d => {
            const sel = document.getElementById("fsConnSelect");
            if (!sel) return;
            sel.innerHTML = '<option value="">— 手动输入 —</option>';
            (d.connections || []).forEach(c => {
                const opt = document.createElement("option");
                opt.value = c.alias;
                opt.textContent = `${c.alias}  (${c.user}@${c.hostname}:${c.port})`;
                opt.dataset.host = c.hostname;
                opt.dataset.user = c.user;
                opt.dataset.port = c.port;
                opt.dataset.identity = c.identity_file || "";
                sel.appendChild(opt);
            });
        })
        .catch(() => {});
}

function fsOnConnChange(sel) {
    const opt = sel.selectedOptions[0];
    if (!opt || !opt.value) return;
    document.getElementById("fsHost").value = opt.dataset.host || "";
    document.getElementById("fsUser").value = opt.dataset.user || "";
    document.getElementById("fsPort").value = opt.dataset.port || 22;
    document.getElementById("fsIdentity").value = opt.dataset.identity || "";
}

// ---------------- 连接测试 ----------------
function fsTestConn() {
    const cfg = fsGetConfig();
    fetch("/api/filesync/test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(cfg),
    })
        .then(r => r.json())
        .then(d => {
            if (d.success) showToast(d.message, "success");
            else showToast(d.error || "连接失败", "error");
        })
        .catch(() => showToast("请求失败", "error"));
}

// ---------------- 文件浏览 ----------------
function fsBrowse(path, page) {
    // 传 page 表示翻页（保留选择）；不传表示切换目录（回到第 1 页）
    if (typeof page === "number") {
        fsPage = page;
    } else {
        fsPage = 1;
    }
    const isNewPath = (path || "") !== fsCurrentPath;
    fetch("/api/filesync/browse?path=" + encodeURIComponent(path || "") +
          "&page=" + fsPage + "&page_size=" + fsPageSize)
        .then(r => r.json())
        .then(d => {
            if (!d.success) { showToast(d.error || "浏览失败", "warning"); return; }
            fsCurrentPath = d.path;
            fsEntriesCache = d.entries || [];
            fsTotal = d.total || 0;
            fsTotalPages = d.total_pages || 1;
            if (isNewPath) fsSelected.clear();
            document.getElementById("fsPath").value = fsCurrentPath;
            fsRenderBrowser(fsEntriesCache);
        })
        .catch(() => showToast("浏览失败", "error"));
}

function fsGoUp() {
    if (!fsCurrentPath) return;
    const parent = fsCurrentPath.split("/").slice(0, -1).join("/") || "/";
    fsBrowse(parent);
}

function fsHome() { fsBrowse(""); }

function fsRenderBrowser(entries) {
    const body = document.getElementById("fsBrowserBody");
    if (!entries || !entries.length) {
        body.innerHTML = '<div class="sidebar-empty">此目录为空</div>';
        return;
    }
    let html = '<table class="fs-browser-table"><thead><tr>' +
        '<th class="fs-icon"></th><th>名称</th><th class="fs-size">大小</th></tr></thead><tbody>';
    entries.forEach(e => {
        const icon = e.is_dir ? "📁" : "📄";
        const size = e.is_dir ? "—" : fsFmtSize(e.size);
        const sel = fsSelected.has(e.path) ? " selected" : "";
        const esc = e.path.replace(/'/g, "\\'");
        const dbl = e.is_dir ? `fsBrowse('${esc}')` : `fsAddSingle('${esc}')`;
        html += `<tr class="${sel}" onclick="fsToggleSelect('${esc}', event)" ondblclick="${dbl}" data-path="${esc}">` +
            `<td class="fs-icon">${icon}</td><td>${e.name}</td><td class="fs-size">${size}</td></tr>`;
    });
    html += "</tbody></table>";
    // 分页条（目录条目过多时翻页，避免页面过长）
    html += '<div class="fs-pager">' +
        '<button class="btn btn-outline btn-sm" onclick="fsPagePrev()" ' + (fsPage <= 1 ? "disabled" : "") + '>‹ 上一页</button>' +
        '<span class="fs-pager-info">第 ' + fsPage + ' / ' + fsTotalPages + ' 页 · 共 ' + fsTotal + ' 项</span>' +
        '<button class="btn btn-outline btn-sm" onclick="fsPageNext()" ' + (fsPage >= fsTotalPages ? "disabled" : "") + '>下一页 ›</button>' +
        '</div>';
    body.innerHTML = html;
}

function fsPagePrev() {
    if (fsPage > 1) fsBrowse(fsCurrentPath, fsPage - 1);
}

function fsPageNext() {
    if (fsPage < fsTotalPages) fsBrowse(fsCurrentPath, fsPage + 1);
}

function fsToggleSelect(path, ev) {
    if (ev.shiftKey && fsLastClicked) {
        const rows = Array.from(document.querySelectorAll(".fs-browser-table tr[data-path]"));
        const paths = rows.map(r => r.dataset.path);
        const a = paths.indexOf(fsLastClicked), b = paths.indexOf(path);
        if (a !== -1 && b !== -1) {
            const [s, e] = a < b ? [a, b] : [b, a];
            for (let i = s; i <= e; i++) fsSelected.add(paths[i]);
        }
    } else if (fsSelected.has(path)) {
        fsSelected.delete(path);
    } else {
        fsSelected.add(path);
    }
    fsLastClicked = path;
    // 重渲染并恢复选中态
    fsRenderBrowser(fsEntriesCache);
    fsSelected.forEach(p => {
        const tr = document.querySelector(`.fs-browser-table tr[data-path="${p.replace(/'/g, "\\'")}"]`);
        if (tr) tr.classList.add("selected");
    });
}

function fsAddSelected() {
    if (!fsSelected.size) {
        showToast("请先在浏览器中选择文件或文件夹（Shift 可多选）", "warning");
        return;
    }
    const cfg = fsGetConfig();
    const base = cfg.remote_base.replace(/\\/g, "/").replace(/\/+$/, "");
    fsSelected.forEach(lp => {
        const name = lp.split("/").pop();
        const rp = base + "/" + name;
        if (!fsItems.some(it => it.local === lp)) fsItems.push({ local: lp, remote: rp });
    });
    fsSelected.clear();
    fsRenderList();
}

function fsAddSingle(path) {
    fsSelected.clear();
    fsSelected.add(path);
    fsAddSelected();
}

// ---------------- 同步列表 ----------------
function fsRenderList() {
    const el = document.getElementById("fsList");
    document.getElementById("fsCount").textContent = fsItems.length + " 项";
    if (!fsItems.length) {
        el.innerHTML = '<div class="sidebar-empty">从右侧浏览器选择文件/文件夹添加</div>';
        return;
    }
    el.innerHTML = fsItems.map((it, i) =>
        `<div class="fs-item">` +
        `<span class="fs-local" title="${escHtml(it.local)}">${escHtml(it.local.split("/").pop())}</span>` +
        `<span class="fs-arrow">→</span>` +
        `<span class="fs-remote" title="${escHtml(it.remote)}">${escHtml(it.remote)}</span>` +
        `<span class="fs-remove" onclick="fsRemove(${i})">✕</span></div>`
    ).join("");
}

function fsRemove(i) {
    fsItems.splice(i, 1);
    fsRenderList();
}

// ---------------- 开始同步 ----------------
function fsStartSync() {
    if (!fsItems.length) { showToast("请先添加要同步的文件", "warning"); return; }
    const cfg = fsGetConfig();
    const payload = {
        config: cfg,
        local_paths: fsItems.map(it => it.local),
        remote_paths: fsItems.map(it => it.remote),
    };
    fetch("/api/filesync/sync", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
    })
        .then(r => r.json())
        .then(d => {
            if (!d.success) { showToast(d.error || "启动失败", "error"); return; }
            showToast("同步已启动", "success");
            fsItems = [];
            fsRenderList();
            document.getElementById("fsProgressWrap").style.display = "block";
            document.getElementById("fsSyncBtn").disabled = true;
            document.getElementById("fsSyncBtn").textContent = "⏳ 同步中…";
            fsPoll();
        })
        .catch(() => showToast("请求失败", "error"));
}

function fsPoll() {
    fetch("/api/filesync/status")
        .then(r => r.json())
        .then(d => {
            const p = d.progress || {};
            if (p.total > 0) {
                const pct = Math.round((p.done / p.total) * 100);
                document.getElementById("fsProgressFill").style.width = pct + "%";
                document.getElementById("fsProgressText").textContent =
                    `${p.done}/${p.total} — ${p.current || ""}`;
            }
            const box = document.getElementById("fsLogBox");
            box.innerHTML = (d.logs || []).map(l =>
                `<div class="log-entry log-${l.level}"><span class="log-time">${l.ts}</span>${escHtml(l.message)}</div>`
            ).join("") || '<div class="log-entry log-info">等待同步...</div>';
            box.scrollTop = box.scrollHeight;

            if (!d.running) {
                document.getElementById("fsSyncBtn").disabled = false;
                document.getElementById("fsSyncBtn").textContent = "🚀 开始同步";
                document.getElementById("fsProgressText").textContent = "";
                document.getElementById("fsProgressWrap").style.display = "none";
                clearTimeout(fsPollTimer);
                return;
            }
            fsPollTimer = setTimeout(fsPoll, 500);
        })
        .catch(() => { fsPollTimer = setTimeout(fsPoll, 1000); });
}

function fsClearLogs() {
    fetch("/api/filesync/clear-logs", { method: "POST" })
        .then(() => {
            const box = document.getElementById("fsLogBox");
            if (box) box.innerHTML = '<div class="log-entry log-info">等待同步...</div>';
        });
}

// ---------------- 工具 ----------------
function fsFmtSize(bytes) {
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
    if (bytes < 1024 * 1024 * 1024) return (bytes / 1024 / 1024).toFixed(1) + " MB";
    return (bytes / 1024 / 1024 / 1024).toFixed(2) + " GB";
}

// ---------------- 初始化 ----------------
document.addEventListener("DOMContentLoaded", () => {
    fsLoadConnections();
    fsHome();   // 从用户主目录开始浏览
});
