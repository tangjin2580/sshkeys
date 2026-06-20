// ============ State ============
let sseSource = null;
let wipeAnimating = false;
let logUnread = 0;

// ============ Utility: Debounce & Throttle ============
function debounce(fn, delay = 300) {
    let timer = null;
    return function(...args) {
        clearTimeout(timer);
        timer = setTimeout(() => fn.apply(this, args), delay);
    };
}

function throttle(fn, limit = 100) {
    let waiting = false;
    return function(...args) {
        if (!waiting) {
            fn.apply(this, args);
            waiting = true;
            setTimeout(() => { waiting = false; }, limit);
        }
    };
}
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
    sseSource.onerror = (e) => {
        log("SSE 连接中断，3秒后自动重连...", "warning");
        // EventSource 会自动重连，但如果是致命错误需要手动重连
        if (sseSource && sseSource.readyState === 2) {
            // CLOSED — 3秒后手动重连
            setTimeout(() => {
                if (sseSource) sseSource.close();
                sseSource = null;
                initSSE();
                log("SSE 已重连", "info");
            }, 3000);
        }
    };
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
function showToast(msg, type = "info", suggestion = null) {
    const t = document.createElement("div");
    t.className = `toast toast-${type}`;
    
    // 支持富文本消息（包含建议）
    let html = msg;
    if (suggestion) {
        html += `<br><small style="opacity:0.8;">💡 ${suggestion}</small>`;
    }
    t.innerHTML = html;
    
    document.body.appendChild(t);
    
    // 不同消息类型使用不同的显示时长
    const durations = {
        "success": 2500,
        "info": 3000,
        "warning": 4000,
        "error": 5000
    };
    const duration = durations[type] || 3000;
    
    setTimeout(() => {
        t.style.opacity = "0";
        t.style.transform = "translateX(100%)";
        setTimeout(() => t.remove(), 300);
    }, duration);
}

// 统一处理 API 错误响应
// 自动解析 error/suggestion 并展示
function handleApiError(response, defaultMsg = "操作失败") {
    if (!response) {
        showToast(defaultMsg, "error");
        return;
    }
    
    const errorMsg = response.error || defaultMsg;
    const suggestion = response.suggestion || null;
    const code = response.code || null;
    
    // 根据错误码选择提示类型
    let toastType = "error";
    if (code === "CONNECTION_NOT_FOUND") toastType = "warning";
    if (code === "TERMINAL_NOT_FOUND") toastType = "warning";
    
    showToast(errorMsg, toastType, suggestion);
    
    // 如果有错误码，也在控制台输出详细信息
    if (code) {
        console.warn(`API 错误 [${code}]:`, errorMsg, suggestion ? `\n建议: ${suggestion}` : "");
    }
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
    // 切换到 WebSSH 面板时重新适配终端尺寸（三栏布局下尤其重要）
    if (panelId === 'panel-webssh' && typeof _webssh_fit !== 'undefined' && _webssh_fit) {
        setTimeout(() => { try { _webssh_fit.fit(); } catch (e) {} }, 100);
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
