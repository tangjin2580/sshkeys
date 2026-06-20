# SSH Key Manager 优化总结

**日期**：2026-06-20  
**优化重点**：性能提升 + 稳定性增强

---

## 一、修复的关键问题

### 1. WebSSH 路由 404 问题（根本原因已修复）
- **现象**：浏览器连接 WebSSH 时报 `Unexpected token '<'` 错误
- **根因**：端口 5000 上同时有 12 个僵尸 Python 进程在监听，请求被旧进程（加载旧代码）接管，返回 404 HTML 页面
- **修复**：
  - 用 PowerShell 彻底杀掉所有 Python 进程
  - 清空所有 `__pycache__` 目录
  - 干净重启服务器
- **验证**：`/api/webssh/connect` 现在返回 400/500 JSON（不再是 404 HTML）

---

## 二、代码清理（任务 #16）

### server.py 清理
- ✅ 删除调试 `print()` 语句（`[STARTUP] server.py 已加载`）
- ✅ 删除 `/api/debug/routes` 调试路由
- ✅ 删除 `/api/test` 测试路由
- ✅ 验证语法正确

---

## 三、WebSSH 会话管理优化（任务 #17）

### 新增功能
1. **会话超时机制**
   - 10 分钟无活动自动清理（`SESSION_TIMEOUT = 600`）
   - 追踪每个会话的 `last_active` 时间戳
   - 每次 `send`/`recv`/`resize` 操作更新活跃时间

2. **最大会话数限制**
   - 最多 5 个并发会话（`MAX_WEBSSH_SESSIONS = 5`）
   - 防止 DoS 攻击
   - 超过限制返回 429 错误

3. **定期清理线程**
   - 每 60 秒运行一次（`_CLEANUP_INTERVAL = 60`）
   - 自动检测并清理：
     - 超时会话（10 分钟无活动）
     - `channel` 已关闭的会话
   - 注册路由时自动启动

4. **活跃时间追踪**
   - `connect`：记录 `connected_at` 和 `last_active`
   - `send`/`recv`/`resize`：更新 `last_active`
   - 清理线程使用 `last_active` 判断超时

### 代码位置
- `modules/webssh.py`：
  - 新增 `_cleanup_stale_sessions()` 函数
  - 新增 `_start_cleanup_thread()` 函数
  - 修改 `_webssh_connect()`：添加会话数检查、记录 `last_active`
  - 修改 `_webssh_send()`/`_webssh_recv()`/`_webssh_resize()`：更新 `last_active`

---

## 四、错误处理和日志增强（任务 #18）

### 1. 请求日志
- **新增**：`@app.before_request` 和 `@app.after_request` 钩子
- **功能**：
  - 记录请求方法、路径、耗时
  - 只记录耗时 >500ms 或错误请求（避免日志洪水）
  - 跳过 SSE 和长轮询（太频繁）
- **日志格式**：
  ```
  [REQUEST] /api/webssh/connect — 500 （耗时 1234.56ms）
  [REQUEST] /api/generate — 200 （耗时 567.89ms）
  ```

### 2. 全局错误处理器
- **新增**：`@app.errorhandler(500)` 处理器
- **功能**：
  - 捕获所有未处理的 500 错误
  - 返回 JSON 格式错误（不是 HTML）
  - 防止浏览器收到 `<!DOCTYPE html>` 导致 `Unexpected token '<'`
- **错误格式**：
  ```json
  {
    "success": false,
    "error": "服务器内部错误，请查看日志",
    "code": "INTERNAL_ERROR"
  }
  ```

### 代码位置
- `modules/server.py`：
  - 新增 `_log_request_start()` 函数（before_request）
  - 新增 `_log_request_end()` 函数（after_request）
  - 新增 `_handle_500()` 函数（errorhandler）

---

## 五、前端性能优化（任务 #19）

### 1. 工具函数
- **新增**：`debounce(fn, delay)` — 防抖（默认 300ms）
- **新增**：`throttle(fn, limit)` — 节流（默认 100ms）
- **代码位置**：`static/script.js` 顶部

### 2. WebSSH 终端优化
- **resize 事件节流**：
  - 原代码：每次窗口缩放都立即发送 resize 请求
  - 优化后：200ms 防抖，避免频繁请求
  - 代码位置：`static/webssh.js` `initWebSSHTerminal()` 函数

### 3. SSE 自动重连
- **原代码**：`onerror` 只打印警告，不重连
- **优化后**：
  - 检测 `readyState === 2`（CLOSED）
  - 3 秒后自动重连
  - 避免网络抖动导致 SSE 断开
- **代码位置**：`static/script.js` `initSSE()` 函数

### 4. 防止长轮询重叠
- **优化**：添加 `_webssh_poll_pending` 标志，防止多个长轮询请求重叠
- **代码位置**：`static/webssh.js` `_poll_once()` 函数

---

## 六、稳定性增强

### 1. 进程管理
- **问题**：Windows 下 Python 进程不容易彻底杀死，导致多个进程监听同一端口
- **解决方案**：
  - 用 PowerShell `Get-Process python* | Stop-Process -Force` 彻底杀进程
  - 每次重启前清 `__pycache__`
  - 用 `netstat -ano | grep ":5000"` 验证端口占用

### 2. 缓存管理
- **问题**：修改 `.py` 文件后，Python 可能运行旧代码（从 `__pycache__` 加载）
- **解决方案**：
  - 每次重启前删除所有 `__pycache__` 目录
  - 设置 `sys.dont_write_bytecode = True`（可选）
  - 使用 `app.config['TEMPLATES_AUTO_RELOAD'] = True` 自动重载模板

### 3. 错误隔离
- **全局错误处理器**：捕获所有 500 错误，返回 JSON
- **WebSSH 错误**：连接失败返回详细错误信息（`error_response()`）
- **SSE 错误**：自动重连，不中断用户操作

---

## 七、测试建议

### 1. WebSSH 功能
- [ ] 打开浏览器，进入 WebSSH 面板
- [ ] 输入有效的 SSH 地址和用户名，点击连接
- [ ] 确认终端正常显示，可以输入命令
- [ ] 确认不再报 `Unexpected token '<'` 错误

### 2. 会话管理
- [ ] 打开 6 个 WebSSH 连接（应该第 6 个返回 429 错误）
- [ ] 等待 10 分钟，确认超时会话被自动清理
- [ ] 关闭浏览器，确认会话在 10 分钟后自动清理

### 3. 本地终端
- [ ] 点击侧边栏"本地终端"图标
- [ ] 确认跳转到本地终端设置页面
- [ ] 测试"检测终端"和"自动检测"按钮

### 4. 导航动画
- [ ] 点击侧边栏不同菜单项
- [ ] 确认面板切换有过渡动画（淡入淡出 + 位移）

### 5. SSE 重连
- [ ] 打开页面，确认 SSE 连接成功（日志显示"已连接"）
- [ ] 断开网络，等待 3 秒
- [ ] 恢复网络，确认 SSE 自动重连

### 6. 错误提示
- [ ] 尝试生成一个空密钥类型
- [ ] 确认错误提示显示友好（不是裸的 500 页面）
- [ ] 确认错误提示包含建议信息（`suggestion` 字段）

---

## 八、性能数据

### 优化前
- WebSSH 路由 404（无法使用）
- 无会话超时（内存泄漏风险）
- 无最大会话数限制（DoS 风险）
- 500 错误返回 HTML（浏览器报错）
- resize 事件无节流（频繁请求）
- SSE 断开不重连（需要手动刷新）

### 优化后
- ✅ WebSSH 路由正常（返回 JSON）
- ✅ 会话 10 分钟超时自动清理
- ✅ 最多 5 个并发会话
- ✅ 500 错误返回 JSON（友好提示）
- ✅ resize 事件 200ms 防抖
- ✅ SSE 断开后 3 秒自动重连
- ✅ 请求日志（记录慢请求和错误）
- ✅ 代码整洁（无调试路由和 print）

---

## 九、下一步建议

### 可选优化
1. **添加按钮加载状态**：防止双击（生成密钥、保存并部署等）
2. **优化列表渲染**：使用 `DocumentFragment` 批量更新 DOM
3. **添加请求限流**：防止 API 被滥用（rate limiting）
4. **优化 SSE 广播**：使用更高效的数据结构（避免遍历所有队列）
5. **添加离线支持**：使用 Service Worker 缓存静态资源

### 功能增强
1. **WebSSH 录制**：记录终端操作，支持回放
2. **WebSSH 文件管理**：集成 SFTP，支持文件上传/下载
3. **连接标签**：给 SSH 连接打标签，方便分类
4. **密钥过期提醒**：检测即将过期的密钥，提醒用户更换

---

## 十、技术要点总结

### 关键经验
1. **Windows 进程管理**：`taskkill //F //IM python.exe` 可能杀不干净，用 PowerShell `Get-Process | Stop-Process -Force` 更可靠
2. **Python 缓存**：修改 `.py` 文件后一定要清 `__pycache__`，否则运行旧代码
3. **Flask 路由注册**：`register_webssh_routes(app)` 必须在模块级别调用，不能放在未执行的函数里
4. **全局错误处理器**：`@app.errorhandler(500)` 返回 JSON，防止浏览器收到 HTML
5. **会话管理**：必须追踪活跃时间并定期清理，否则内存泄漏
6. **前端性能**：resize/scroll 事件一定要节流，搜索输入一定要防抖

### 文件清单
- `modules/server.py`：清理调试代码、添加请求日志、全局错误处理器
- `modules/webssh.py`：优化会话管理（超时、最大限制、清理线程）
- `static/script.js`：添加 debounce/throttle、优化 SSE 重连
- `static/webssh.js`：节流 resize 事件、防止长轮询重叠

---

**优化完成时间**：2026-06-20 05:35 (GMT+8)  
**服务器状态**：运行中（监听 `http://127.0.0.1:5000`）  
**下一步**：等待用户测试反馈，根据反馈继续优化
