"""hmp 单元测试。

全部使用临时本地 Git 仓库与临时工作区，不访问网络。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from _hmp import core  # noqa: E402


class HmpTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = tmp.name
        self.workspace = os.path.join(self.base, "ws")

    # ------------------------------------------------------------------ #
    # 辅助
    # ------------------------------------------------------------------ #
    @staticmethod
    def _run(args, cwd=None, check=True):
        proc = subprocess.run(
            args, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        if check and proc.returncode != 0:
            raise AssertionError(f"命令失败 {args}:\n{proc.stdout}\n{proc.stderr}")
        return proc

    def _source_repo(self, name):
        """创建一个含一次提交的本地 Git 仓库，返回其路径（作为 clone 源）。"""
        repo = os.path.join(self.base, "src", name)
        os.makedirs(repo, exist_ok=True)
        self._run(["git", "init", "-q"], cwd=repo)
        self._run(["git", "config", "user.email", "test@example.com"], cwd=repo)
        self._run(["git", "config", "user.name", "Tester"], cwd=repo)
        with open(os.path.join(repo, "README.md"), "w", encoding="utf-8") as fh:
            fh.write(f"# {name}\n")
        self._run(["git", "add", "."], cwd=repo)
        self._run(["git", "commit", "-qm", f"init {name}"], cwd=repo)
        return repo

    def _init_and_add(self, name="demo"):
        core.init_workspace(self.workspace)
        src = self._source_repo(name)
        core.add_project(self.workspace, name, src)
        return src

    # ------------------------------------------------------------------ #
    # init
    # ------------------------------------------------------------------ #
    def test_init_creates_structure_and_registry(self):
        root, created = core.init_workspace(self.workspace)
        self.assertTrue(created)
        self.assertEqual(root, os.path.abspath(self.workspace))
        self.assertTrue(os.path.isdir(os.path.join(root, "projects")))
        self.assertTrue(os.path.isdir(os.path.join(root, "archive")))

        registry = os.path.join(root, "registry.json")
        self.assertTrue(os.path.isfile(registry))
        with open(registry, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(data["version"], 1)
        self.assertEqual(data["projects"], {})

    def test_init_is_idempotent_and_keeps_registry(self):
        core.init_workspace(self.workspace)
        data = core.load_registry(self.workspace)
        data["projects"]["keep"] = {"name": "keep"}
        with open(os.path.join(self.workspace, "registry.json"), "w", encoding="utf-8") as fh:
            json.dump(data, fh)

        _, created = core.init_workspace(self.workspace)
        self.assertFalse(created)
        self.assertIn("keep", core.load_registry(self.workspace)["projects"])

    # ------------------------------------------------------------------ #
    # add
    # ------------------------------------------------------------------ #
    def test_add_clones_repo_and_registers_project(self):
        core.init_workspace(self.workspace)
        src = self._source_repo("demo")

        entry = core.add_project(self.workspace, "demo", src)

        target = os.path.join(self.workspace, "projects", "demo")
        self.assertTrue(os.path.isdir(os.path.join(target, ".git")))
        self.assertTrue(os.path.isfile(os.path.join(target, "README.md")))
        self.assertFalse(entry["archived"])
        self.assertEqual(entry["path"], os.path.join("projects", "demo"))

        data = core.load_registry(self.workspace)
        self.assertIn("demo", data["projects"])
        self.assertEqual(data["projects"]["demo"]["url"], src)

    def test_add_rejects_duplicate_name(self):
        src = self._init_and_add()
        with self.assertRaises(core.HmpError):
            core.add_project(self.workspace, "demo", src)

    def test_add_rejects_unsafe_name(self):
        core.init_workspace(self.workspace)
        src = self._source_repo("demo")
        for bad in ("../escape", "a/b", "..", ""):
            with self.subTest(name=bad), self.assertRaises(core.HmpError):
                core.add_project(self.workspace, bad, src)

    def test_add_failure_does_not_register(self):
        core.init_workspace(self.workspace)
        missing = os.path.join(self.base, "does-not-exist")
        with self.assertRaises(core.HmpError):
            core.add_project(self.workspace, "ghost", missing)
        self.assertNotIn("ghost", core.load_registry(self.workspace)["projects"])
        self.assertFalse(os.path.exists(os.path.join(self.workspace, "projects", "ghost")))

    # ------------------------------------------------------------------ #
    # list
    # ------------------------------------------------------------------ #
    def test_list_reports_branch_dirty_commit_and_abs_path(self):
        self._init_and_add()

        rows = core.list_projects(self.workspace)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["name"], "demo")
        self.assertNotEqual(row["branch"], "-")
        self.assertEqual(row["dirty"], "否")
        self.assertIn("init demo", row["last_commit"])
        self.assertTrue(os.path.isabs(row["path"]))
        self.assertFalse(row["archived"])

        readme = os.path.join(self.workspace, "projects", "demo", "README.md")
        with open(readme, "a", encoding="utf-8") as fh:
            fh.write("local change\n")
        self.assertEqual(core.list_projects(self.workspace)[0]["dirty"], "是")

    def test_list_empty_workspace(self):
        core.init_workspace(self.workspace)
        self.assertEqual(core.list_projects(self.workspace), [])

    # ------------------------------------------------------------------ #
    # archive
    # ------------------------------------------------------------------ #
    def test_archive_moves_dir_and_updates_registry(self):
        self._init_and_add()

        core.archive_project(self.workspace, "demo")

        data = core.load_registry(self.workspace)
        entry = data["projects"]["demo"]
        self.assertTrue(entry["archived"])
        self.assertEqual(entry["path"], os.path.join("archive", "demo"))
        self.assertIn("archived_at", entry)

        self.assertFalse(os.path.exists(os.path.join(self.workspace, "projects", "demo")))
        archived = os.path.join(self.workspace, "archive", "demo")
        self.assertTrue(os.path.isdir(os.path.join(archived, ".git")))
        self.assertEqual(core.list_projects(self.workspace)[0]["path"], archived)

    def test_archive_unknown_project_raises(self):
        core.init_workspace(self.workspace)
        with self.assertRaises(core.HmpError):
            core.archive_project(self.workspace, "missing")

    # ------------------------------------------------------------------ #
    # status / log / open / remove（附加覆盖）
    # ------------------------------------------------------------------ #
    def test_status_log_and_open(self):
        self._init_and_add()

        status = core.project_status(self.workspace, "demo")
        self.assertEqual(status["name"], "demo")
        self.assertIsNotNone(status["branch"])
        self.assertEqual(status["dirty_files"], [])

        log = core.project_log(self.workspace, "demo", n=1)
        self.assertEqual(len(log["commits"]), 1)
        self.assertIn("init demo", log["commits"][0])

        self.assertEqual(
            core.open_project(self.workspace, "demo"),
            os.path.join(self.workspace, "projects", "demo"),
        )

    def test_remove_keeps_files_by_default_and_deletes_on_request(self):
        self._init_and_add()
        target = os.path.join(self.workspace, "projects", "demo")

        result = core.remove_project(self.workspace, "demo")
        self.assertFalse(result["deleted"])
        self.assertNotIn("demo", core.load_registry(self.workspace)["projects"])
        self.assertTrue(os.path.isdir(target))

        # 重新登记后再用 --delete 语义删除
        src = self._source_repo("demo2")
        core.add_project(self.workspace, "demo2", src)
        target2 = os.path.join(self.workspace, "projects", "demo2")
        result2 = core.remove_project(self.workspace, "demo2", delete=True)
        self.assertTrue(result2["deleted"])
        self.assertFalse(os.path.exists(target2))

    # ------------------------------------------------------------------ #
    # 入口
    # ------------------------------------------------------------------ #
    def test_entrypoints_work(self):
        for command in (
            [sys.executable, "-m", "hmp", "--help"],
            [os.path.join(REPO_ROOT, "hmp"), "--help"],
        ):
            with self.subTest(command=command):
                proc = self._run(command, cwd=REPO_ROOT, check=False)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertIn("init", proc.stdout)


if __name__ == "__main__":
    unittest.main()
