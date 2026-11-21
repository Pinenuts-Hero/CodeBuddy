# Hermes-Memory

CodeBuddy 工作项目的代码管理平台：一个 CLI（`hmp`）加一套目录规范，用来登记、跟踪、归档通过 CodeBuddy 推进的项目。

纯 Python 3 标准库实现，**零第三方依赖**；Git 相关操作通过调用系统 `git` 完成。

## 运行环境

- Python 3.8+
- 可用的 `git`（需在 `PATH` 中）

## 运行方式

两种方式完全等价，在项目根目录执行：

```bash
./hmp --help          # 可执行脚本方式
python3 -m hmp --help # 模块方式
```

## 目录规范

工作区由 `hmp init <dir>` 创建，`<dir>` 即工作区根目录。典型结构如下：

```
workspace/
├── registry.json          # 项目登记表（JSON）
├── projects/<name>/       # 克隆的仓库，name 为登记时指定的项目名
└── archive/<name>/        # 归档的项目，结构与 projects/ 相同
```

- `projects/`：活动项目，每个子目录都是一个完整的 Git 工作树。
- `archive/`：`hmp archive` 把项目从 `projects/` 移动到这里，并同步更新登记表。
- `registry.json`：登记表，记录项目名、来源地址、相对路径、是否归档等信息。示例：

```json
{
  "version": 1,
  "projects": {
    "demo": {
      "name": "demo",
      "url": "git@example.com:team/demo.git",
      "path": "projects/demo",
      "archived": false,
      "added_at": "2026-11-21T09:07:00+00:00"
    }
  }
}
```

项目名只允许字母、数字、点（`.`）、下划线（`_`）和连字符（`-`），且必须以字母或数字开头，不能包含 `..`，以防路径穿越。

## 定位工作区

除 `init` 外，所有命令都需要知道工作区在哪里。按以下优先级确定：

1. 命令行全局选项 `-w / --workspace <dir>`；
2. 环境变量 `HMP_WORKSPACE`；
3. 从当前目录逐级向上查找含 `registry.json` 的目录（类似 `git` 查找 `.git`）。

因此既可以 `cd` 进工作区直接操作，也可以在任意位置用 `-w` 指定：

```bash
export HMP_WORKSPACE=/path/to/workspace
hmp list
# 或
hmp -w /path/to/workspace list
```

## 命令一览

| 命令 | 说明 |
| --- | --- |
| `hmp init <dir>` | 创建工作区（目录、子目录与空的登记表），可重复执行 |
| `hmp add <name> <git-url>` | 登记项目并 `git clone` 到 `projects/<name>` |
| `hmp list` | 表格列出名称、分支、是否有未提交改动、最后提交、绝对路径 |
| `hmp status [name]` | 查看分支、ahead/behind、未提交文件；省略 name 时查看所有活动项目 |
| `hmp log <name> [-n 10]` | 查看最近提交，默认 10 条 |
| `hmp open <name>` | 打印项目的绝对路径 |
| `hmp archive <name>` | 将项目移到 `archive/<name>` 并更新登记表 |
| `hmp remove <name> [--delete]` | 注销项目，默认保留文件；加 `--delete` 同时删除文件 |

## 使用示例

```bash
# 1. 创建工作区
hmp init ~/hmp-workspace
cd ~/hmp-workspace

# 2. 登记并克隆一个项目（也可使用本地路径作为 git-url，便于离线使用）
hmp add demo git@example.com:team/demo.git

# 3. 查看所有项目
hmp list
# 名称   分支   未提交改动  最后提交            路径
# ----   ----   ----------  ------------------  ----------------
# demo   main   否          9aee0ff 修复登录问题  /home/me/hmp-workspace/projects/demo

# 4. 查看某个项目的详细状态
hmp status demo
# 项目: demo
# 路径: /home/me/hmp-workspace/projects/demo
# 分支: main
# 上游: ahead 1 / behind 0
# 未提交文件:
#   M src/app.py

# 5. 查看最近 5 条提交
hmp log demo -n 5

# 6. 取得路径，便于 cd 或其他脚本使用
cd "$(hmp open demo)"

# 7. 归档（项目移到 archive/，登记表同步更新）
hmp archive demo

# 8. 注销：默认保留文件
hmp remove demo
# 如需连文件一起删除：
# hmp remove demo --delete
```

## 测试

使用标准库 `unittest`，测试通过临时本地 Git 仓库完成，全程不联网：

```bash
make test
# 等价于：python3 -m unittest discover -s tests -v
```

## 设计说明

- 登记表采用「临时文件 + `os.replace`」原子写入，避免写坏文件。
- `remove` 默认只改登记表、不碰文件；显式 `--delete` 时才删除，且校验路径必须位于工作区内。
- 所有用户可预期错误统一以 `错误：...` 输出到 stderr，并以非零退出码结束。
