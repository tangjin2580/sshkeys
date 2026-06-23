"""
SSH Config 读写模块
- 扫描 ~/.ssh 下已有密钥
- 解析 / 写入 ~/.ssh/config
- 保存密钥到 ~/.ssh 目录
"""

import os
import re
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

from modules.utils import safe_chmod

logger = logging.getLogger(__name__)

# 文件操作线程锁（防止并发读写 SSH config 和密钥文件）
_file_lock = threading.Lock()


def get_ssh_dir() -> Path:
    """获取用户 .ssh 目录路径（跨平台）"""
    home = Path.home()
    ssh_dir = home / ".ssh"
    return ssh_dir


def get_config_path() -> Path:
    """获取 SSH config 文件路径"""
    return get_ssh_dir() / "config"


def list_existing_keys() -> list[dict]:
    """
    递归扫描 ~/.ssh 目录及其子目录中所有的私钥文件（多线程并行）

    Returns:
        [{"name": "id_ed25519", "path": "/Users/xxx/.ssh/id_ed25519",
          "has_pub": true, "size": 411, "type": "ed25519"}, ...]
    """
    ssh_dir = get_ssh_dir()
    logger.info(f"扫描 SSH 目录: {ssh_dir}")

    if not ssh_dir.exists():
        logger.warning(f"SSH 目录不存在: {ssh_dir}")
        return []

    # 排除非密钥文件
    skip_names = {"known_hosts", "known_hosts.old", "authorized_keys", "config", "environment"}
    skip_prefixes = (".",)

    # --- 阶段 1: 快速收集候选文件路径 ---
    candidates = []
    try:
        for entry in sorted(ssh_dir.rglob("*"), key=lambda x: str(x)):
            if not entry.is_file():
                continue
            if entry.name.endswith(".pub"):
                continue
            if entry.name in skip_names:
                continue
            if entry.name.startswith(skip_prefixes):
                continue
            candidates.append(entry)
    except PermissionError:
        logger.warning("无法读取 ~/.ssh 目录")
        return []

    if not candidates:
        return []

    # --- 阶段 2: 多线程并行检测密钥类型 ---
    def _process_file(entry: Path) -> dict | None:
        key_type = _guess_key_type(entry)
        if key_type == "unknown":
            return None
        pub_path = entry.parent / f"{entry.name}.pub"
        try:
            rel_path = entry.relative_to(ssh_dir)
            display_name = str(rel_path).replace("\\", "/")
        except ValueError:
            display_name = entry.name
        # 获取文件时间戳
        stat = entry.stat()
        return {
            "name": display_name,
            "path": str(entry),
            "has_pub": pub_path.exists(),
            "size": stat.st_size,
            "type": key_type,
            "mtime": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            "ctime": datetime.fromtimestamp(stat.st_ctime).strftime("%Y-%m-%d %H:%M:%S"),
        }

    keys = []
    with ThreadPoolExecutor(max_workers=min(8, len(candidates))) as pool:
        futures = {pool.submit(_process_file, f): f for f in candidates}
        for future in as_completed(futures):
            try:
                result = future.result()
                if result:
                    keys.append(result)
            except Exception as e:
                logger.warning(f"扫描文件失败 {futures[future]}: {e}")

    # 按路径排序保持显示顺序一致
    keys.sort(key=lambda k: k["path"])
    logger.info(f"找到 {len(keys)} 个密钥: {[k['name'] for k in keys]}")
    return keys


def _guess_key_type(filepath: Path) -> str:
    """通过文件内容猜测密钥类型"""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            first_line = f.readline().strip()
        if "BEGIN OPENSSH PRIVATE KEY" in first_line:
            return "ed25519/ecdsa"
        elif "BEGIN RSA PRIVATE KEY" in first_line:
            return "rsa"
        elif "BEGIN EC PRIVATE KEY" in first_line:
            return "ecdsa"
    except Exception:
        pass
    return "unknown"


def parse_ssh_config() -> list[dict]:
    """
    解析 ~/.ssh/config，返回 Host 条目列表

    Returns:
        [{"host": "myserver", "hostname": "1.2.3.4", "user": "root",
          "port": 22, "identityfile": "~/.ssh/id_ed25519"}, ...]
    """
    config_path = get_config_path()
    if not config_path.exists():
        return []

    entries = []
    current = {}

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                # 跳过空行和注释
                if not line or line.startswith("#"):
                    continue

                # 匹配 Host / HostName / User / Port / IdentityFile
                # 使用正则，不区分大小写
                m = re.match(r"^\s*(Host|HostName|Hostname|User|Port|IdentityFile|IdentitiesOnly)\s+(.+)", line, re.IGNORECASE)
                if not m:
                    continue

                key = m.group(1).lower()
                value = m.group(2).strip()

                if key == "host":
                    # 遇到新 Host，保存上一个
                    if current:
                        entries.append(current)
                    # Host 行可能有多个值（别名 + IP），只取第一个作为别名
                    host_alias = value.split()[0] if value.split() else value
                    current = {"host": host_alias, "hostname": "", "user": "", "port": 22, "identityfile": ""}
                elif key == "hostname":
                    current["hostname"] = value
                elif key == "user":
                    current["user"] = value
                elif key == "port":
                    try:
                        current["port"] = int(value)
                    except ValueError:
                        current["port"] = 22
                elif key == "identityfile":
                    current["identityfile"] = value

            # 最后一个条目
            if current:
                entries.append(current)
    except Exception as e:
        logger.warning(f"解析 SSH config 失败: {e}")

    return entries


def add_or_update_host(
    host_alias: str,
    hostname: str,
    user: str,
    identity_file: str,
    port: int = 22,
) -> bool:
    """
    在 ~/.ssh/config 中添加或更新一个 Host 条目

    Args:
        host_alias: Host 别名，如 "myserver"
        hostname: 服务器地址
        user: SSH 用户名
        identity_file: 私钥路径，如 "~/.ssh/id_ed25519"
        port: SSH 端口

    Returns:
        True 表示成功
    """
    config_path = get_config_path()
    ssh_dir = get_ssh_dir()

    with _file_lock:
        # 确保 ~/.ssh 目录存在
        ssh_dir.mkdir(mode=0o700, exist_ok=True)

        # 读取现有配置
        lines = []
        if config_path.exists():
            with open(config_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        else:
            lines = []

        # 构建新条目：Host 同时包含别名和 IP/域名，两者均可免密登录
        host_pattern = f"{host_alias} {hostname}" if hostname != host_alias else host_alias
        new_entry = (
            f"Host {host_pattern}\n"
            f"    HostName {hostname}\n"
            f"    User {user}\n"
            f"    Port {port}\n"
            f"    IdentityFile {identity_file}\n"
            f"    IdentitiesOnly yes\n"
        )

        # 查找是否已存在同名 Host，存在则替换
        replaced = _replace_host_block(lines, host_alias, new_entry)

        if not replaced:
            # 追加到文件末尾
            if lines and not lines[-1].endswith("\n"):
                lines.append("\n")
            if lines and lines[-1].strip() != "":
                lines.append("\n")
            lines.append(new_entry)
            lines.append("\n")

        # 写入
        new_content = "".join(lines)
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(new_content)
        safe_chmod(str(config_path), 0o600)

    logger.info(f"SSH config 已更新: Host {host_alias}")
    return True


def _replace_host_block(lines: list[str], host_alias: str, new_entry: str) -> bool:
    """
    在 config 内容中替换指定 Host 块。就地修改 lines。

    Returns:
        True 如果找到并替换了
    """
    # 找到 Host alias 块的范围
    in_block = False
    block_start = -1
    block_end = -1

    for i, line in enumerate(lines):
        m = re.match(r"^\s*Host\s+(.+)", line, re.IGNORECASE)
        if m:
            # 如果之前在块中，那这个就是块结束
            if in_block:
                block_end = i
                break

            hosts = m.group(1).split()
            # 支持部分匹配：如 host_alias 出现在多值 Host 行 "myvps 10.0.0.1" 中
            if host_alias in hosts or host_alias == m.group(1).strip():
                in_block = True
                block_start = i

    if in_block and block_start >= 0:
        # 块结束于下一个 Host 或文件末尾
        if block_end < 0:
            block_end = len(lines)  # 到文件末尾

        # 删除旧块
        del lines[block_start:block_end]
        # 插入新块
        lines.insert(block_start, new_entry)
        # 确保块后有空行
        if block_start + 1 < len(lines) and lines[block_start + 1].strip() != "":
            lines.insert(block_start + 1, "\n")
        return True

    return False


def save_key_to_ssh_dir(
    private_key_str: str,
    public_key_str: str,
    key_type: str,
) -> dict:
    """
    将生成的密钥保存到 ~/.ssh 目录，自动避免文件名冲突

    Returns:
        {"private_path": str, "public_path": str, "filename": str}
    """
    ssh_dir = get_ssh_dir()
    ssh_dir.mkdir(mode=0o700, exist_ok=True)

    with _file_lock:
        # 生成不冲突的文件名
        base_name = f"id_{key_type}"
        filename = base_name
        counter = 1
        while (ssh_dir / filename).exists():
            filename = f"{base_name}_{counter}"
            counter += 1

        private_path = ssh_dir / filename
        public_path = ssh_dir / f"{filename}.pub"

        # 写入私钥
        with open(private_path, "w", encoding="utf-8") as f:
            f.write(private_key_str)
        safe_chmod(str(private_path), 0o600)

        # 写入公钥
        with open(public_path, "w", encoding="utf-8") as f:
            f.write(public_key_str + "\n")
        safe_chmod(str(public_path), 0o644)

    logger.info(f"密钥已保存到 ~/.ssh/: {filename}")
    return {
        "private_path": str(private_path),
        "public_path": str(public_path),
        "filename": filename,
    }


def delete_key_file(key_name: str) -> dict:
    """
    删除 ~/.ssh 下的密钥文件（私钥 + 公钥）

    Returns:
        {"success": bool, "message": str, "deleted": [str]}
    """
    ssh_dir = get_ssh_dir()
    private_path = ssh_dir / key_name
    public_path = ssh_dir / f"{key_name}.pub"
    deleted = []

    # 安全检查：确保只删除 ~/.ssh 下的文件
    private_resolved = private_path.resolve()
    public_resolved = public_path.resolve()
    ssh_resolved = ssh_dir.resolve()
    if not str(private_resolved).startswith(str(ssh_resolved)):
        return {"success": False, "message": "安全限制：只能删除 ~/.ssh 目录下的文件", "deleted": []}

    if private_path.exists():
        private_path.unlink()
        deleted.append(str(private_path))
        logger.info(f"已删除私钥: {private_path}")

    if public_path.exists():
        public_path.unlink()
        deleted.append(str(public_path))
        logger.info(f"已删除公钥: {public_path}")

    if not deleted:
        return {"success": False, "message": f"密钥文件不存在: {key_name}", "deleted": []}

    return {"success": True, "message": f"已删除 {len(deleted)} 个文件", "deleted": deleted}


def remove_host_from_config(host_alias: str) -> dict:
    """
    从 ~/.ssh/config 中移除指定 Host 条目

    Returns:
        {"success": bool, "message": str}
    """
    config_path = get_config_path()
    if not config_path.exists():
        return {"success": False, "message": "SSH config 文件不存在"}

    with _file_lock:
        with open(config_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        in_block = False
        block_start = -1
        block_end = -1

        for i, line in enumerate(lines):
            m = re.match(r"^\s*Host\s+(.+)", line, re.IGNORECASE)
            if m:
                if in_block:
                    block_end = i
                    break
                hosts = m.group(1).split()
                # 支持部分匹配：如 host_alias 出现在多值 Host 行 "myvps 10.0.0.1" 中
                if host_alias in hosts or host_alias == m.group(1).strip():
                    in_block = True
                    block_start = i

        if not in_block or block_start < 0:
            return {"success": False, "message": f"未找到 Host 条目: {host_alias}"}

        if block_end < 0:
            block_end = len(lines)

        # 删除块（包括块后紧跟的空行）
        del lines[block_start:block_end]
        # 清理可能残留的空行
        while block_start < len(lines) and lines[block_start].strip() == "":
            del lines[block_start]
        if block_start > 0 and block_start < len(lines) and lines[block_start - 1].strip() == "":
            del lines[block_start - 1]

        with open(config_path, "w", encoding="utf-8") as f:
            f.writelines(lines)
        safe_chmod(str(config_path), 0o600)

    logger.info(f"已从 SSH config 移除: Host {host_alias}")
    return {"success": True, "message": f"已移除 Host 条目: {host_alias}"}
