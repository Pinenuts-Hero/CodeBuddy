"""hmp 命令行界面：参数解析、表格输出与命令分发。"""

from __future__ import annotations

import argparse
import sys
import unicodedata

from . import core


def _display_width(text: object) -> int:
    """按东亚字符宽度计算显示宽度，保证中文表格对齐。"""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in str(text))


def _pad(text: object, width: int) -> str:
    value = str(text)
    return value + " " * max(0, width - _display_width(value))


def print_table(headers: list[str], rows: list[tuple]) -> None:
    widths = [_display_width(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], _display_width(cell))

    def render(row) -> str:
        return "  ".join(_pad(cell, widths[i]) for i, cell in enumerate(row)).rstrip()

    print(render(headers))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print(render(row))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hmp", description="管理 CodeBuddy 工作项目的 CLI（纯标准库，零依赖）"
    )
    parser.add_argument(
        "-w",
        "--workspace",
        help="工作区目录；默认从当前目录向上查找 registry.json，或使用 HMP_WORKSPACE",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="创建工作区").add_argument("dir", help="工作区目录")
    add = sub.add_parser("add", help="登记项目并克隆仓库")
    add.add_argument("name", help="项目名称")
    add.add_argument("git_url", help="Git 仓库地址")
    sub.add_parser("list", help="列出已登记的项目")
    status = sub.add_parser("status", help="查看项目状态")
    status.add_argument("name", nargs="?", help="项目名称；省略时查看所有活动项目")
    log = sub.add_parser("log", help="查看最近提交")
    log.add_argument("name", help="项目名称")
    log.add_argument("-n", type=int, default=10, help="显示条数（默认 10）")
    sub.add_parser("open", help="打印项目绝对路径").add_argument("name", help="项目名称")
    sub.add_parser("archive", help="归档项目").add_argument("name", help="项目名称")
    remove = sub.add_parser("remove", help="注销项目")
    remove.add_argument("name", help="项目名称")
    remove.add_argument("--delete", action="store_true", help="同时删除项目文件（默认保留）")
    return parser


def _cmd_status(root: str, name: str | None) -> None:
    if name:
        s = core.project_status(root, name)
        print(f"项目: {s['name']}")
        print(f"路径: {s['path']}")
        print(f"分支: {s['branch']}")
        if s["ahead"] is None:
            print("上游: 无（未设置 tracking 分支）")
        else:
            print(f"上游: ahead {s['ahead']} / behind {s['behind']}")
        if s["dirty_files"]:
            print("未提交文件:")
            for line in s["dirty_files"]:
                print(f"  {line}")
        else:
            print("未提交文件: 无")
        return

    data = core.load_registry(root)
    names = [n for n, entry in data["projects"].items() if not entry.get("archived")]
    if not names:
        print("没有活动项目。")
        return
    for n in names:
        s = core.project_status(root, n)
        ahead = s["ahead"] if s["ahead"] is not None else "-"
        behind = s["behind"] if s["behind"] is not None else "-"
        print(
            f"{n} [{s['branch']}] ahead {ahead} / behind {behind} "
            f"未提交 {len(s['dirty_files'])} 个文件"
        )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            root, created = core.init_workspace(args.dir)
            print(f"工作区已{'创建' if created else '存在'}：{root}")
            return 0

        root = core.find_workspace(args.workspace)
        if args.command == "add":
            entry = core.add_project(root, args.name, args.git_url)
            print(f"已登记：{entry['name']} -> {entry['path']}")
        elif args.command == "list":
            rows = core.list_projects(root)
            if not rows:
                print("工作区中还没有已登记的项目。")
            else:
                print_table(
                    ["名称", "分支", "未提交改动", "最后提交", "路径"],
                    [
                        (r["name"], r["branch"], r["dirty"], r["last_commit"], r["path"])
                        for r in rows
                    ],
                )
        elif args.command == "status":
            _cmd_status(root, args.name)
        elif args.command == "log":
            info = core.project_log(root, args.name, args.n)
            print(f"== {info['name']} 最近提交 ==")
            if info["commits"]:
                for line in info["commits"]:
                    print(line)
            else:
                print("（无提交记录）")
        elif args.command == "open":
            print(core.open_project(root, args.name))
        elif args.command == "archive":
            entry = core.archive_project(root, args.name)
            print(f"已归档：{entry['name']} -> {entry['path']}")
        elif args.command == "remove":
            result = core.remove_project(root, args.name, args.delete)
            suffix = "（文件已删除）" if result["deleted"] else "（文件保留）"
            print(f"已注销：{result['name']}{suffix}")
        return 0
    except core.HmpError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
