"""hmp 核心逻辑：工作区、项目登记表与 Git 操作。

仅依赖 Python 3 标准库；所有 Git 交互都通过子进程调用系统 ``git``。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone

VERSION = 1
WORKSPACE_ENV = "HMP_WORKSPACE"
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class HmpError(Exception):
    """用户可预期的错误，CLI 层统一转换为友好提示。"""


# --------------------------------------------------------------------------- #
# 通用辅助
# --------------------------------------------------------------------------- #
def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _validate_name(name: str) -> str:
    if not name or not _NAME_RE.match(name) or ".." in name:
        raise HmpError(
            f"非法项目名 {name!r}：只允许字母、数字、点、下划线和连字符，"
            "且必须以字母或数字开头"
        )
    return name


def registry_file(root: str) -> str:
    return os.path.join(root, "registry.json")


def config_file() -> str:
    """用户级配置文件路径：``~/.config/hmp/config.json``。"""
    return os.path.join(os.path.expanduser("~"), ".config", "hmp", "config.json")


def load_config() -> dict:
    """读取用户级配置；文件缺失或损坏时按空配置处理。"""
    try:
        with open(config_file(), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_config(data: dict) -> None:
    """原子写入用户级配置，必要时创建 ``~/.config/hmp``。"""
    path = config_file()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
    except OSError as exc:
        raise HmpError(f"无法写入用户配置 {path}：{exc}") from exc


def remembered_workspace() -> str | None:
    """返回用户配置中记住的工作区绝对路径；未记住时返回 None。"""
    value = load_config().get("workspace")
    if isinstance(value, str) and value.strip():
        return os.path.abspath(os.path.expanduser(value))
    return None


def remember_workspace(root: str) -> str:
    """把工作区路径写入用户配置，返回规范化后的绝对路径。"""
    root = os.path.abspath(os.path.expanduser(root))
    data = load_config()
    data["workspace"] = root
    save_config(data)
    return root


def _empty_registry() -> dict:
    return {"version": VERSION, "projects": {}}


def _write_registry(root: str, data: dict) -> None:
    """原子写入登记表，避免中途失败留下半个 JSON。"""
    path = registry_file(root)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def load_registry(root: str) -> dict:
    path = registry_file(root)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError as exc:
        raise HmpError(f"未找到登记表：{path}") from exc
    except json.JSONDecodeError as exc:
        raise HmpError(f"登记表格式错误：{path}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("projects"), dict):
        raise HmpError(f"登记表结构无效：{path}")
    data.setdefault("version", VERSION)
    return data


def _search_upwards() -> str | None:
    """从当前目录向上查找含 registry.json 的目录。"""
    cur = os.getcwd()
    while True:
        if os.path.isfile(registry_file(cur)):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def find_workspace(explicit: str | None = None) -> str:
    """定位工作区。

    优先级：命令行参数 > 环境变量 > 当前目录向上查找 > 用户配置中记住的工作区。
    """
    from_config = False
    if explicit:
        root = os.path.abspath(os.path.expanduser(explicit))
    elif os.environ.get(WORKSPACE_ENV):
        root = os.path.abspath(os.path.expanduser(os.environ[WORKSPACE_ENV]))
    else:
        root = _search_upwards()
        if root is None:
            root = remembered_workspace()
            from_config = True

    if root is None:
        raise HmpError(
            "未找到工作区：请先运行 hmp init <dir> 创建工作区，"
            "或用 hmp use <dir> 指定已有工作区；也可使用 -w 或设置 "
            f"{WORKSPACE_ENV} 环境变量"
        )
    if not os.path.isfile(registry_file(root)):
        if from_config:
            raise HmpError(
                f"记住的工作区已失效（缺少 registry.json）：{root}；"
                "可用 hmp use <dir> 重新指定，或运行 hmp init <dir>"
            )
        raise HmpError(f"不是有效的 hmp 工作区（缺少 registry.json）：{root}")
    return root


def get_entry(root: str, name: str) -> tuple[dict, dict]:
    data = load_registry(root)
    entry = data["projects"].get(name)
    if entry is None:
        raise HmpError(f"项目未登记：{name}")
    return data, entry


def entry_path(root: str, entry: dict) -> str:
    return os.path.abspath(os.path.join(root, entry.get("path", "")))


# --------------------------------------------------------------------------- #
# Git 辅助
# --------------------------------------------------------------------------- #
def git(args, cwd: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["git"] + list(args),
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or f"退出码 {proc.returncode}"
        raise HmpError(f"git {' '.join(args)} 失败：{detail}")
    return proc


def is_git_repo(path: str) -> bool:
    if not os.path.isdir(path):
        return False
    proc = git(["rev-parse", "--is-inside-work-tree"], cwd=path, check=False)
    return proc.returncode == 0 and proc.stdout.strip() == "true"


def _branch(path: str) -> str:
    proc = git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=path, check=False)
    return proc.stdout.strip() if proc.returncode == 0 and proc.stdout.strip() else "-"


def _dirty_files(path: str) -> list[str]:
    proc = git(["status", "--porcelain"], cwd=path, check=False)
    if proc.returncode != 0:
        return []
    return [line for line in proc.stdout.splitlines() if line.strip()]


def _last_commit(path: str) -> str:
    proc = git(["log", "-1", "--pretty=format:%h %s"], cwd=path, check=False)
    return proc.stdout.strip() if proc.returncode == 0 and proc.stdout.strip() else "-"


# --------------------------------------------------------------------------- #
# 命令实现
# --------------------------------------------------------------------------- #
def init_workspace(target: str) -> tuple[str, bool]:
    """创建工作区，返回 (绝对路径, 是否为本次新建)。"""
    root = os.path.abspath(os.path.expanduser(target))
    os.makedirs(os.path.join(root, "projects"), exist_ok=True)
    os.makedirs(os.path.join(root, "archive"), exist_ok=True)
    created = not os.path.exists(registry_file(root))
    if created:
        _write_registry(root, _empty_registry())
    return root, created


def use_workspace(target: str) -> str:
    """切换到指定工作区并写入用户配置，返回其绝对路径。"""
    root = os.path.abspath(os.path.expanduser(target))
    if not os.path.isfile(registry_file(root)):
        raise HmpError(
            f"不是有效的 hmp 工作区（缺少 registry.json）：{root}；"
            "可先运行 hmp init <dir> 创建"
        )
    return remember_workspace(root)


def add_project(root: str, name: str, url: str) -> dict:
    _validate_name(name)
    data = load_registry(root)
    if name in data["projects"]:
        raise HmpError(f"项目已登记：{name}")
    target = os.path.join(root, "projects", name)
    if os.path.exists(target):
        raise HmpError(f"目标目录已存在：{target}")

    proc = git(["clone", "--", url, target], check=False)
    if proc.returncode != 0:
        if os.path.isdir(target):
            shutil.rmtree(target, ignore_errors=True)
        detail = proc.stderr.strip() or proc.stdout.strip()
        raise HmpError(f"克隆失败：{detail}")

    entry = {
        "name": name,
        "url": url,
        "path": os.path.join("projects", name),
        "archived": False,
        "added_at": _utc_now(),
    }
    data["projects"][name] = entry
    _write_registry(root, data)
    return entry


def list_projects(root: str) -> list[dict]:
    data = load_registry(root)
    rows = []
    for name, entry in sorted(data["projects"].items()):
        path = entry_path(root, entry)
        if is_git_repo(path):
            branch = _branch(path)
            dirty = "是" if _dirty_files(path) else "否"
            last_commit = _last_commit(path)
        else:
            branch, dirty, last_commit = "-", "-", "-"
        rows.append(
            {
                "name": name,
                "branch": branch,
                "dirty": dirty,
                "last_commit": last_commit,
                "path": path,
                "archived": bool(entry.get("archived")),
            }
        )
    return rows


def project_status(root: str, name: str) -> dict:
    _, entry = get_entry(root, name)
    path = entry_path(root, entry)
    if not is_git_repo(path):
        raise HmpError(f"项目目录不存在或不是 Git 仓库：{name}（{path}）")

    branch = _branch(path)
    ahead = behind = None
    upstream = git(
        ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"],
        cwd=path,
        check=False,
    )
    if upstream.returncode == 0 and upstream.stdout.strip():
        counts = git(
            ["rev-list", "--left-right", "--count", "HEAD...@{upstream}"],
            cwd=path,
            check=False,
        )
        if counts.returncode == 0:
            parts = counts.stdout.split()
            if len(parts) == 2:
                ahead, behind = int(parts[0]), int(parts[1])

    return {
        "name": name,
        "path": path,
        "branch": branch,
        "ahead": ahead,
        "behind": behind,
        "dirty_files": _dirty_files(path),
        "archived": bool(entry.get("archived")),
    }


def project_log(root: str, name: str, n: int = 10) -> dict:
    _, entry = get_entry(root, name)
    path = entry_path(root, entry)
    if not is_git_repo(path):
        raise HmpError(f"项目目录不存在或不是 Git 仓库：{name}（{path}）")
    if n <= 0:
        n = 10
    proc = git(
        ["log", "-n", str(n), "--pretty=format:%h%x09%ad%x09%s", "--date=short"],
        cwd=path,
        check=False,
    )
    commits = proc.stdout.splitlines() if proc.returncode == 0 else []
    return {"name": name, "commits": commits}


def open_project(root: str, name: str) -> str:
    _, entry = get_entry(root, name)
    return entry_path(root, entry)


def archive_project(root: str, name: str) -> dict:
    data, entry = get_entry(root, name)
    if entry.get("archived"):
        raise HmpError(f"项目已归档：{name}")
    src = entry_path(root, entry)
    if not os.path.isdir(src):
        raise HmpError(f"项目目录不存在：{src}")
    dst = os.path.join(root, "archive", name)
    if os.path.exists(dst):
        raise HmpError(f"归档目录已存在：{dst}")

    os.makedirs(os.path.join(root, "archive"), exist_ok=True)
    shutil.move(src, dst)
    entry["archived"] = True
    entry["path"] = os.path.join("archive", name)
    entry["archived_at"] = _utc_now()
    _write_registry(root, data)
    return entry


def remove_project(root: str, name: str, delete: bool = False) -> dict:
    data, entry = get_entry(root, name)
    path = entry_path(root, entry)
    existed = os.path.isdir(path)
    deleted = False
    if delete and existed:
        if os.path.commonpath([os.path.abspath(root), path]) != os.path.abspath(root):
            raise HmpError(f"拒绝删除工作区之外的路径：{path}")
        shutil.rmtree(path)
        deleted = True
    del data["projects"][name]
    _write_registry(root, data)
    return {"name": name, "deleted": deleted}
