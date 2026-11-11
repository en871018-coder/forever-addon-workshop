"""Security and transaction tests using temporary fake Forever branches only."""

import importlib.util
import json
import os
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "local-manager" / "core.py"
SPEC = importlib.util.spec_from_file_location("forever_manager_core", str(MODULE_PATH))
core_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(core_module)
ManagerCore, ManagerError = core_module.ManagerCore, core_module.ManagerError


class ManagerCoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="forever-manager-test-")
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        self.branch = self.root / "fake-wow" / "_forever_"
        self.branch.mkdir(parents=True)
        (self.branch / ".flavor.info").write_text("wowf\n")
        (self.branch.parent / ".build.info").write_text("Product!STRING:0|Version!STRING:0|Active!DEC:1\nwowf|1.60.1.70790|1\n")
        self.addons = self.branch / "Interface" / "AddOns"
        self.addons.mkdir(parents=True)
        self.state = self.root / "state"
        self.state.mkdir()
        (self.state / "config.json").write_text(json.dumps({"game_path": str(self.branch)}))
        self.core = ManagerCore(self.project, self.state)
        self.core._game_running = lambda: False
        self.wtf = self.branch / "WTF"
        self.wtf.mkdir()
        (self.wtf / "private.txt").write_text("never read or modify this file")

    def tearDown(self):
        self.assertEqual((self.wtf / "private.txt").read_text(), "never read or modify this file")
        self.temp.cleanup()

    def archive(self, entries=None, name="plugin.zip"):
        entries = entries or {"Example/Example.toc": "## Interface: 16001\n## Title: Example\n## Version: 2.0\nMain.lua\n", "Example/Main.lua": "-- new\n"}
        path = self.root / name
        with zipfile.ZipFile(str(path), "w", zipfile.ZIP_DEFLATED) as archive:
            for member, content in entries.items():
                archive.writestr(member, content)
        return path

    def plan(self, path):
        return self.core.plan_install({"type": "zip", "path": str(path)})

    def old_addon(self, name="Example"):
        folder = self.addons / name
        folder.mkdir()
        (folder / (name + ".toc")).write_text("## Interface: 16001\n## Title: Old\n## Version: 1.0\n")
        (folder / "old.lua").write_text("-- original\n")
        return folder

    def test_path_accepts_branch_and_addons_but_rejects_other_flavors(self):
        status = self.core.set_game_path(str(self.addons))
        self.assertEqual(status["game_path"], str(self.branch))
        self.assertEqual(status["client_build"], "1.60.1.70790")
        self.assertEqual(status["target"], "Forever")
        for name in ("_retail_", "_classic_", "not-a-game"):
            path = self.branch.parent / name
            path.mkdir()
            (path / ".flavor.info").write_text("wowf")
            with self.assertRaises(ManagerError):
                self.core.set_game_path(str(path))

    def test_path_requires_forever_metadata_and_rejects_links(self):
        other = self.root / "unverified" / "_forever_"
        other.mkdir(parents=True)
        with self.assertRaises(ManagerError):
            self.core.set_game_path(str(other))
        linked = self.root / "link" / "_forever_"
        linked.parent.mkdir()
        linked.symlink_to(self.branch, target_is_directory=True)
        with self.assertRaises(ManagerError):
            self.core.set_game_path(str(linked))

    def test_scan_chooses_forever_toc_without_executing_lua(self):
        folder = self.old_addon()
        (folder / "Example.toc").write_text("## Interface: 120105\n## Version: wrong\n")
        (folder / "Example_Vanilla.toc").write_text("## Interface: 16001\n## Title-zhCN: 测试插件\n## Version: 2.0\n")
        (folder / "payload.lua").write_text("THIS_IS_NOT_EXECUTABLE_PYTHON")
        self.assertEqual(self.core.installed()[0]["title"], "测试插件")
        self.assertTrue(self.core.installed()[0]["forever_declared"])
        self.assertFalse(self.core.installed()[0]["managed"])

    def test_plan_does_not_write_game_and_requires_true_confirmation(self):
        path = self.archive()
        plan = self.plan(path)
        self.assertTrue(plan["can_install"], plan)
        self.assertEqual(plan["addons"], ["Example"])
        self.assertEqual(plan["declared_interfaces"], [16001])
        self.assertFalse((self.addons / "Example").exists())
        for value in (False, None, 1, "true"):
            with self.assertRaises(ManagerError) as error:
                self.core.execute_install(plan["plan_id"], value)
            self.assertEqual(error.exception.code, "confirmation_required")

    def test_install_backs_up_replaces_and_marks_managed(self):
        old = self.old_addon()
        before = self.core._tree_digest(old)
        plan = self.plan(self.archive())
        result = self.core.execute_install(plan["plan_id"], True)
        self.assertTrue(result["ok"])
        self.assertEqual(self.core._tree_digest(Path(result["backup_path"]) / "Example"), before)
        self.assertEqual((old / "Main.lua").read_text(), "-- new\n")
        self.assertFalse((old / "old.lua").exists())
        self.assertTrue(self.core.installed()[0]["managed"])
        self.assertTrue(result["restart_required"])
        self.assertFalse(result["in_game_tested"])
        self.assertFalse(any(p.name.startswith(".forever-stage-") for p in self.addons.iterdir()))

    def test_rolls_back_all_folders_when_second_replacement_fails(self):
        first, second = self.old_addon("A"), self.old_addon("B")
        before = {p.name: self.core._tree_digest(p) for p in (first, second)}
        path = self.archive({"A/A.toc": "## Interface: 16001\n## Version: 2\n", "A/new.lua": "-- A", "B/B.toc": "## Interface: 16001\n## Version: 2\n", "B/new.lua": "-- B"})
        plan = self.plan(path)
        original_replace = self.core._replace
        failed = []

        def replace(source, target):
            if Path(source).parent.name == "content" and Path(source).name == "B" and not failed:
                failed.append(True)
                raise OSError("simulated disk failure")
            original_replace(source, target)

        with mock.patch.object(self.core, "_replace", side_effect=replace):
            with self.assertRaises(ManagerError) as error:
                self.core.execute_install(plan["plan_id"], True)
        self.assertEqual(error.exception.code, "install_failed")
        self.assertEqual({p.name: self.core._tree_digest(p) for p in (first, second)}, before)
        self.assertFalse(self.core.installed()[0]["managed"])

    def test_zip_slip_absolute_and_windows_traversal_rejected(self):
        for i, member in enumerate(("../outside.txt", "/tmp/outside.txt", "C:/outside.txt", "Example/../../outside.txt", "Example\\..\\outside.txt", "\\\\host\\share.txt")):
            with self.subTest(member=member):
                path = self.archive({"Example/Example.toc": "## Interface: 16001", member: "bad"}, "bad%d.zip" % i)
                self.assertFalse(self.plan(path)["can_install"])
        self.assertFalse((self.root / "outside.txt").exists())

    def test_symlinks_case_collisions_and_executables_rejected(self):
        path = self.root / "symlink.zip"
        with zipfile.ZipFile(str(path), "w") as archive:
            archive.writestr("Example/Example.toc", "## Interface: 16001")
            entry = zipfile.ZipInfo("Example/link")
            entry.create_system = 3
            entry.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(entry, "../../WTF")
        self.assertFalse(self.plan(path)["can_install"])
        cases = [
            {"Example/Example.toc": "## Interface: 16001", "example/other.lua": "bad"},
            {"Example/Example.toc": "## Interface: 16001", "Example/Images/a.tga": "a", "Example/images/b.tga": "b"},
            {"Example/Example.toc": "## Interface: 16001", "Example/a": "file", "Example/a/b.lua": "bad"},
            {"Example/Example.toc": "## Interface: 16001", "Example/program.EXE": "bad"},
            {"Example/Example.toc": "## Interface: 16001", "Example/disguised.lua": b"MZxxbad"},
            {"Example/Example.toc": "## Interface: 16001", "WTF/private.lua": "bad"},
        ]
        for i, entries in enumerate(cases):
            with self.subTest(case=i):
                self.assertFalse(self.plan(self.archive(entries, "case%d.zip" % i))["can_install"])

    def test_normal_windows_zip_and_one_wrapper_are_supported(self):
        entries = {"Release\\Example\\Example_Vanilla.toc": "## Interface: 16001\n## Version: 2", "Release\\Example\\Main.lua": "-- source"}
        plan = self.plan(self.archive(entries))
        self.assertTrue(plan["can_install"], plan)
        self.core.execute_install(plan["plan_id"], True)
        self.assertTrue((self.addons / "Example" / "Main.lua").is_file())

    def test_wrong_interface_and_unknown_addon_layout_rejected(self):
        for i, entries in enumerate((
            {"Example/Example.toc": "## Interface: 11509"},
            {"Example/Unrelated.toc": "## Interface: 16001"},
            {"Example.toc": "## Interface: 16001"},
        )):
            plan = self.plan(self.archive(entries, "wrong%d.zip" % i))
            self.assertFalse(plan["can_install"])
            self.assertEqual(plan["error_code"], "wrong_interface")

    def test_running_unknown_process_build_and_target_changes_prevent_write(self):
        plan = self.plan(self.archive())
        for running in (True, None):
            self.core._game_running = lambda: running
            with self.assertRaises(ManagerError):
                self.core.execute_install(plan["plan_id"], True)
        self.core._game_running = lambda: False
        (self.branch.parent / ".build.info").write_text("Product|Version\nwowf|1.60.1.99999\n")
        with self.assertRaises(ManagerError) as error:
            self.core.execute_install(plan["plan_id"], True)
        self.assertEqual(error.exception.code, "stale_plan")
        self.assertFalse((self.addons / "Example").exists())

    def test_cached_archive_tamper_and_existing_changes_prevent_write(self):
        old = self.old_addon()
        plan = self.plan(self.archive())
        cached = self.core._plans[plan["plan_id"]]["archive"]
        cached.write_bytes(b"tampered")
        with self.assertRaises(ManagerError) as error:
            self.core.execute_install(plan["plan_id"], True)
        self.assertEqual(error.exception.code, "digest_mismatch")
        plan = self.plan(self.archive(name="new.zip"))
        (old / "old.lua").write_text("-- changed after plan")
        with self.assertRaises(ManagerError) as error:
            self.core.execute_install(plan["plan_id"], True)
        self.assertEqual(error.exception.code, "stale_plan")

    def test_compression_bomb_and_count_limits(self):
        path = self.archive({"Example/Example.toc": "## Interface: 16001", "Example/big.lua": b"a" * (2 * 1024 * 1024)})
        self.assertEqual(self.plan(path)["error_code"], "archive_limit")
        with mock.patch.object(core_module, "MAX_FILES", 1):
            self.assertEqual(self.plan(self.archive(name="count.zip"))["error_code"], "archive_limit")

    def test_url_allowlist_and_redirect_target_validation(self):
        bad = ["http://github.com/a/b/releases/download/v1/a.zip", "https://localhost/a.zip", "https://github.com.evil.test/a.zip", "https://github.com/a/b/blob/main/a.zip", "https://github.com@127.0.0.1/a.zip", "https://bindonforever.com/admin", "https://mediafilez.forgecdn.net:444/files/1/2/a.zip"]
        for url in bad:
            with self.subTest(url=url), self.assertRaises(ManagerError):
                core_module._safe_url(url)
        core_module._safe_url("https://github.com/a/b/releases/download/v1/a.zip")
        handler = core_module._SafeRedirect()
        req = core_module.urllib.request.Request("https://github.com/a/b/releases/download/v1/a.zip")
        with self.assertRaises(ManagerError):
            handler.redirect_request(req, None, 302, "redirect", {}, "http://127.0.0.1/private")

    def test_existing_symlink_and_install_lock_refuse_overwrite(self):
        victim = self.root / "victim"
        victim.mkdir()
        (self.addons / "Example").symlink_to(victim, target_is_directory=True)
        self.assertEqual(self.plan(self.archive())["error_code"], "unsafe_target")
        (self.addons / "Example").unlink()
        plan = self.plan(self.archive(name="second.zip"))
        (self.state / "install.lock").write_text("busy")
        with self.assertRaises(ManagerError) as error:
            self.core.execute_install(plan["plan_id"], True)
        self.assertEqual(error.exception.code, "install_locked")
        self.assertFalse((self.addons / "Example").exists())

    def test_catalog_has_nine_attributed_items_and_local_patch(self):
        items = self.core.catalog()
        self.assertEqual(len(items), 9)
        self.assertTrue(all(item["source_url"] and item["version"] for item in items))
        folder = self.project / "addons" / "ForeverQuestTools"
        folder.mkdir(parents=True)
        (folder / "ForeverQuestTools.toc").write_text("## Interface: 16001\n## Version: 0.1.0\n")
        (folder / "Main.lua").write_text("-- local module\n")
        plan = self.core.plan_install({"type": "catalog", "id": "foreverquesttools"})
        self.assertTrue(plan["can_install"], plan)
        self.assertEqual(plan["addons"], ["ForeverQuestTools"])

    def test_missing_curseforge_cache_does_not_invent_download_url(self):
        with mock.patch.object(self.core, "_download", side_effect=AssertionError("must not download")):
            plan = self.core.plan_install({"type": "catalog", "id": "questie"})
        self.assertFalse(plan["can_install"])
        self.assertEqual(plan["error_code"], "missing_source")
        self.assertIsNone(next(i for i in self.core.catalog() if i["id"] == "questie")["download_url"])

    def test_wtf_cannot_be_a_state_or_zip_source(self):
        with self.assertRaises(ManagerError):
            ManagerCore(self.project, self.wtf / "manager-state")
        path = self.wtf / "private.zip"
        with zipfile.ZipFile(str(path), "w") as archive:
            archive.writestr("Example/Example.toc", "## Interface: 16001")
        plan = self.plan(path)
        self.assertFalse(plan["can_install"])
        self.assertEqual(plan["error_code"], "unsafe_source")


if __name__ == "__main__":
    unittest.main()
