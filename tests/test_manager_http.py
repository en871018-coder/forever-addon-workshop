"""Exercise the real loopback handler against a disposable fake Forever branch."""

import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock


MANAGER_DIR = Path(__file__).resolve().parents[1] / "local-manager"
if str(MANAGER_DIR) not in sys.path:
    sys.path.insert(0, str(MANAGER_DIR))
import providers as providers_module
import server as manager_server


class ManagerHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="forever-http-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        self.branch = self.root / "fake-wow" / "_forever_"
        self.addons = self.branch / "Interface" / "AddOns"
        self.addons.mkdir(parents=True)
        (self.branch / ".flavor.info").write_text("wowf\n", encoding="utf-8")
        (self.branch.parent / ".build.info").write_text(
            "Product!STRING:0|Version!STRING:0|Active!DEC:1\nwowf|1.60.1.70790|1\n",
            encoding="utf-8")
        self.wtf = self.branch / "WTF" / "private.txt"
        self.wtf.parent.mkdir()
        self.wtf.write_bytes(b"private fixture; never serve or change")
        self.state = self.root / "state"
        self.state.mkdir()
        (self.state / "config.json").write_text(
            json.dumps({"game_path": str(self.branch)}), encoding="utf-8")
        self.app_dir = self.root / "app"
        web = self.app_dir / "web"
        web.mkdir(parents=True)
        for name, content in (("index.html", "<!doctype html><title>Fixture</title>"),
                              ("app.js", "'use strict';"), ("style.css", "body{}")):
            (web / name).write_text(content, encoding="utf-8")
        env = mock.patch.dict(os.environ, {
            "CURSEFORGE_API_KEY": "", "OPENAI_API_KEY": "", "OPENAI_MODEL": ""})
        env.start()
        self.addCleanup(env.stop)
        app_dir = mock.patch.object(manager_server, "APP_DIR", self.app_dir)
        app_dir.start()
        self.addCleanup(app_dir.stop)
        self.workshop = manager_server.Workshop(self.project, self.state)
        self.workshop.core._game_running = lambda: False
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0),
                                         manager_server.make_handler(self.workshop))
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        self.origin = "http://127.0.0.1:%d" % self.httpd.server_port

    def close_server(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=3)

    def tearDown(self):
        self.assertEqual(self.wtf.read_bytes(), b"private fixture; never serve or change")

    def request(self, method, path, data=None, body=None, headers=None, token=True):
        request_headers = {}
        if token:
            request_headers["X-Workshop-Token"] = self.workshop.token
        if data is not None:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        if method == "POST":
            body = body if body is not None else b""
            request_headers["Content-Type"] = "application/json"
        request_headers.update(headers or {})
        connection = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=5)
        try:
            connection.request(method, path, body=body, headers=request_headers)
            response = connection.getresponse()
            raw = response.read()
            try:
                value = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeError):
                value = None
            return response.status, dict(response.getheaders()), raw, value
        finally:
            connection.close()

    def test_bootstrap_is_public_but_api_and_downloads_require_header_token(self):
        code, headers, raw, _ = self.request("GET", "/", token=False)
        self.assertEqual(code, 200)
        self.assertIn(b"Fixture", raw)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        for path in ("/api/status", "/downloads/wa-library/example.txt",
                     "/api/status?token=" + self.workshop.token):
            with self.subTest(path=path):
                self.assertEqual(self.request("GET", path, token=False)[0], 401)
        self.assertEqual(self.request("GET", "/api/status",
                                      headers={"X-Workshop-Token": "wrong"})[0], 401)

    def test_authenticated_status_uses_only_fixture_branch(self):
        code, _, _, value = self.request("GET", "/api/status")
        self.assertEqual(code, 200)
        self.assertTrue(value["ok"])
        self.assertEqual(value["data"]["game_path"], str(self.branch))
        self.assertEqual(value["data"]["client_build"], "1.60.1.70790")
        self.assertEqual(value["data"]["installed_count"], 0)
        self.assertEqual(value["data"]["target"], "Forever")

    def test_host_origin_and_fetch_site_block_before_api_dispatch(self):
        bad_headers = [
            {"Host": "evil.example"},
            {"Host": "127.0.0.1:%d.evil.example" % self.httpd.server_port},
            {"Origin": "https://evil.example"},
            {"Origin": "null"},
            {"Origin": self.origin, "Sec-Fetch-Site": "cross-site"},
        ]
        with mock.patch.object(self.workshop, "get", wraps=self.workshop.get) as get:
            for headers in bad_headers:
                with self.subTest(headers=headers):
                    self.assertEqual(self.request("GET", "/api/status", headers=headers)[0], 403)
            get.assert_not_called()
        self.assertEqual(self.request("GET", "/api/status", headers={"Origin": self.origin})[0], 200)
        self.assertEqual(self.request("POST", "/api/settings", data={},
                                      headers={"Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.request("POST", "/api/settings", data={}, token=False)[0], 401)

    def test_post_requires_bounded_json_object_and_does_not_mutate_on_error(self):
        cases = [
            (b"{}", {"Content-Type": "text/plain"}),
            (b"[]", {}), (b"null", {}), (b"{", {}), (b"\xff", {}),
            (b"", {}), (b"{}", {"Content-Length": "3000001"}),
            (b"", {"Content-Length": "-1"}),
            (b"", {"Content-Length": "not-a-number"}),
        ]
        with mock.patch.object(self.workshop, "post", wraps=self.workshop.post) as post:
            for body, headers in cases:
                with self.subTest(body=body, headers=headers):
                    code, _, _, value = self.request("POST", "/api/settings", body=body, headers=headers)
                    self.assertEqual(code, 400)
                    self.assertFalse(value["ok"])
            post.assert_not_called()

    def test_keys_are_process_only_and_not_returned_in_status_activity_or_search(self):
        cf_key, ai_key = "cf-private-sentinel-392", "ai-private-sentinel-857"
        code, _, raw, value = self.request("POST", "/api/settings", data={
            "game_path": str(self.branch), "curseforge_api_key": cf_key,
            "openai_api_key": ai_key, "ai_model": "fixture-model"})
        self.assertEqual(code, 200)
        self.assertTrue(value["data"]["curseforge_configured"])
        self.assertTrue(value["data"]["ai_configured"])
        responses = [raw]
        for path in ("/api/status", "/api/activity"):
            responses.append(self.request("GET", path)[2])
        with mock.patch.object(providers_module, "request_json", return_value={"data": []}) as network:
            responses.append(self.request("GET", "/api/search?q=fixture")[2])
            self.assertEqual(network.call_args.args[1]["x-api-key"], cf_key)
        for secret in (cf_key, ai_key):
            self.assertTrue(all(secret.encode() not in response for response in responses))
            for path in self.state.rglob("*"):
                if path.is_file():
                    self.assertNotIn(secret.encode(), path.read_bytes(), str(path))

    def test_invalid_settings_do_not_partially_switch_game_or_replace_credentials(self):
        code, _, _, _ = self.request("POST", "/api/settings", data={
            "curseforge_api_key": "old-cf-sentinel", "openai_api_key": "old-ai-sentinel",
            "ai_model": "old-fixture-model"})
        self.assertEqual(code, 200)
        original_path = self.workshop.core.game_path
        original_config = (self.state / "config.json").read_bytes()
        other = self.root / "other-valid-wow" / "_forever_"
        (other / "Interface" / "AddOns").mkdir(parents=True)
        (other / ".flavor.info").write_text("wowf\n", encoding="utf-8")
        (other.parent / ".build.info").write_text(
            "Product!STRING:0|Version!STRING:0|Active!DEC:1\nwowf|1.60.1.70791|1\n",
            encoding="utf-8")
        validated, _ = self.workshop.core._validate_game_path(str(other))
        self.assertEqual(validated, other)
        invalid_fields = [{"ai_model": "invalid model id"},
                          {"openai_api_key": "new-ai-sentinel\ninvalid"},
                          {"curseforge_api_key": "new-cf-sentinel\r\ninvalid"}]
        with mock.patch.object(self.workshop.core, "set_game_path",
                               wraps=self.workshop.core.set_game_path) as set_path:
            for invalid in invalid_fields:
                data = {"game_path": str(other), "curseforge_api_key": "new-cf-sentinel",
                        "openai_api_key": "new-ai-sentinel", "ai_model": "new-fixture-model"}
                data.update(invalid)
                with self.subTest(invalid_field=next(iter(invalid))):
                    code, _, raw, result = self.request("POST", "/api/settings", data=data)
                    self.assertEqual(code, 400)
                    self.assertFalse(result["ok"])
                    self.assertEqual(self.workshop.core.game_path, original_path)
                    self.assertEqual((self.state / "config.json").read_bytes(), original_config)
                    self.assertEqual(self.workshop.providers.cf_key, "old-cf-sentinel")
                    self.assertEqual(self.workshop.providers.ai_key, "old-ai-sentinel")
                    self.assertEqual(self.workshop.providers.ai_model, "old-fixture-model")
                    for secret in (b"old-cf-sentinel", b"old-ai-sentinel",
                                   b"new-cf-sentinel", b"new-ai-sentinel"):
                        self.assertNotIn(secret, raw)
            set_path.assert_not_called()

    def test_download_serves_only_authenticated_allowed_artifacts(self):
        folder = self.state / "translations"
        folder.mkdir()
        (folder / "fixture.json").write_bytes(b'{"translated":true}')
        code, headers, raw, _ = self.request("GET", "/downloads/translations/fixture.json")
        self.assertEqual(code, 200)
        self.assertEqual(raw, b'{"translated":true}')
        self.assertEqual(headers["Content-Type"], "application/octet-stream")
        self.assertIn("attachment;", headers["Content-Disposition"])
        (folder / "executable.py").write_text("raise RuntimeError('never execute')")
        self.assertEqual(self.request("GET", "/downloads/translations/executable.py")[0], 404)

    def test_path_traversal_symlinks_and_arbitrary_file_routes_do_not_leak(self):
        marker = b"outside-state-secret-sentinel"
        secret = self.root / "secret.txt"
        secret.write_bytes(marker)
        folder = self.state / "translations"
        folder.mkdir()
        (folder / "leak.txt").symlink_to(secret)
        paths = [
            "/downloads/translations/%2e%2e/%2e%2e/secret.txt",
            "/downloads/translations/%2e%2e%2f%2e%2e%2fsecret.txt",
            "/downloads/translations/..%5c..%5csecret.txt",
            "/downloads/translations/leak.txt",
            "/downloads/config.json",
            "/downloads/translations/../config.json",
            "/../../secret.txt", "/%2e%2e/secret.txt",
            "/api/file?path=" + str(secret), "/api/read?path=" + str(self.wtf),
        ]
        for path in paths:
            with self.subTest(path=path):
                code, _, raw, _ = self.request("GET", path)
                self.assertEqual(code, 404)
                self.assertNotIn(marker, raw)
        self.assertEqual(secret.read_bytes(), marker)

    def test_download_category_symlink_cannot_expose_an_outside_directory(self):
        outside = self.root / "private-outside-state"
        outside.mkdir()
        marker = b"must-not-serve-outside-category"
        (outside / "private.json").write_bytes(marker)
        (self.state / "translations").symlink_to(outside, target_is_directory=True)
        code, _, raw, _ = self.request("GET", "/downloads/translations/private.json")
        self.assertEqual(code, 404)
        self.assertNotIn(marker, raw)

    def test_zip_traversal_plan_and_unsafe_download_urls_never_write_game(self):
        archive = self.root / "traversal.zip"
        with zipfile.ZipFile(str(archive), "w") as handle:
            handle.writestr("Fixture/Fixture.toc", "## Interface: 16001\n")
            handle.writestr("../../escaped.lua", "-- must never be extracted")
        code, _, _, value = self.request("POST", "/api/install/plan",
                                        data={"type": "zip", "path": str(archive)})
        self.assertEqual(code, 200)
        self.assertFalse(value["data"]["can_install"])
        self.assertFalse((self.root / "escaped.lua").exists())
        self.assertEqual(list(self.addons.iterdir()), [])
        with mock.patch.object(self.workshop.core, "_download") as download:
            for url in ("file://" + str(self.wtf), "http://127.0.0.1/private",
                        "https://github.com.evil.example/a/b/releases/download/v1/a.zip"):
                with self.subTest(url=url):
                    code, _, _, value = self.request("POST", "/api/install/plan",
                                                    data={"type": "url", "url": url})
                    self.assertEqual(code, 200)
                    self.assertFalse(value["data"]["can_install"])
            download.assert_not_called()

    def test_wa_save_is_opaque_text_and_plain_lua_is_rejected(self):
        marker = self.root / "executed.txt"
        opaque = "!WA:2!__import__('pathlib').Path(%r).write_text('bad')" % str(marker)
        code, _, _, value = self.request("POST", "/api/wa/save", data={
            "name": "fixture", "string": opaque, "source_url": "https://wago.io/fixture"})
        self.assertEqual(code, 200)
        self.assertFalse(marker.exists())
        self.assertEqual(Path(value["data"]["path"]).read_text(encoding="utf-8"), opaque)
        code, _, _, value = self.request("POST", "/api/wa/save", data={
            "name": "bad", "string": "print('do not run Lua')"})
        self.assertEqual(code, 400)
        self.assertFalse(value["ok"])
        self.assertEqual(list(self.addons.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
