"""Mock external providers; only redirect isolation uses disposable loopback HTTP."""

import io
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock


MANAGER_DIR = Path(__file__).resolve().parents[1] / "local-manager"
if str(MANAGER_DIR) not in sys.path:
    sys.path.insert(0, str(MANAGER_DIR))
import providers as provider_module


def completed(translations):
    return {"status": "completed", "output": [{"type": "message", "content": [
        {"type": "output_text", "text": json.dumps(translations, ensure_ascii=False)}]}]}


class ManagerProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="forever-provider-test-")
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name) / "state"
        self.state.mkdir()
        env = mock.patch.dict(os.environ, {
            "CURSEFORGE_API_KEY": "", "OPENAI_API_KEY": "", "OPENAI_MODEL": ""})
        env.start()
        self.addCleanup(env.stop)
        self.providers = provider_module.Providers(self.state)

    def assert_secrets_absent(self, values, secrets):
        serialized = json.dumps(values, ensure_ascii=False)
        for secret in secrets:
            self.assertNotIn(secret, serialized)
            for path in self.state.rglob("*"):
                if path.is_file():
                    self.assertNotIn(secret.encode(), path.read_bytes(), str(path))

    def configure_ai(self):
        self.providers.configure({"openai_api_key": "ai-private-sentinel",
                                  "ai_model": "fixture-model"})

    def test_without_cf_key_returns_honest_website_fallback_without_network(self):
        with mock.patch.object(provider_module, "request_json") as network:
            result = self.providers.search_cf("Mage & Map")
        network.assert_not_called()
        self.assertEqual(result["mode"], "website")
        self.assertEqual(result["items"], [])
        self.assertEqual(urllib.parse.parse_qs(urllib.parse.urlsplit(result["website_url"]).query)["search"],
                         ["Mage & Map"])

    def test_configured_keys_are_not_exposed_or_written_and_invalid_headers_rejected(self):
        cf_key, ai_key = "cf-private-sentinel", "ai-private-sentinel"
        self.providers.configure({"curseforge_api_key": cf_key,
                                  "openai_api_key": ai_key, "ai_model": "fixture-model"})
        self.assertTrue(self.providers.flags()["curseforge_configured"])
        self.assertTrue(self.providers.flags()["ai_configured"])
        self.assert_secrets_absent(self.providers.flags(), (cf_key, ai_key))
        self.assertEqual(list(self.state.iterdir()), [])
        for field in ("curseforge_api_key", "openai_api_key"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.providers.configure({field: "header-sentinel\r\nInjected: bad"})

    def test_invalid_environment_credentials_are_disabled_without_echo_or_storage(self):
        sentinel = "private-env-sentinel\ninvalid"
        with mock.patch.dict(os.environ, {"CURSEFORGE_API_KEY": sentinel,
                                           "OPENAI_API_KEY": sentinel,
                                           "OPENAI_MODEL": "fixture-model"}):
            provider = provider_module.Providers(self.state)
        flags = provider.flags()
        self.assertFalse(flags["curseforge_configured"])
        self.assertFalse(flags["ai_configured"])
        self.assert_secrets_absent(flags, (sentinel, "private-env-sentinel"))
        self.assertEqual(list(self.state.iterdir()), [])

    def test_cf_search_sends_private_key_in_header_and_filters_forever(self):
        self.providers.configure({"curseforge_api_key": "cf-private-sentinel"})
        response = {"data": [{"id": 12, "name": "Fixture", "summary": "map",
                               "links": {"websiteUrl": "https://www.curseforge.com/wow/addons/fixture"},
                               "allowModDistribution": False,
                               "latestFiles": [{"gameVersions": ["1.60.1"]}]}]}
        with mock.patch.object(provider_module, "request_json", return_value=response) as network:
            result = self.providers.search_cf("法师 & map")
        url, headers = network.call_args.args[:2]
        parsed = urllib.parse.urlsplit(url)
        query = urllib.parse.parse_qs(parsed.query)
        self.assertEqual(parsed.netloc, "api.curseforge.com")
        self.assertEqual(parsed.path, "/v1/mods/search")
        self.assertEqual(query["gameId"], ["1"])
        self.assertEqual(query["gameVersionTypeId"], ["88568"])
        self.assertEqual(query["searchFilter"], ["法师 & map"])
        self.assertEqual(headers["x-api-key"], "cf-private-sentinel")
        self.assertNotIn("cf-private-sentinel", url)
        self.assertFalse(result["items"][0]["distribution_allowed"])
        self.assert_secrets_absent(result, ("cf-private-sentinel",))

    def test_cf_files_filters_forever_and_respects_per_file_download_availability(self):
        replies = [{"data": {"allowModDistribution": True}}, {"data": [
            {"id": 21, "displayName": "v1", "gameVersions": ["1.60.1"], "downloadUrl": "https://edge.forgecdn.net/files/1/2/a.zip"},
            {"id": 22, "displayName": "v2", "gameVersions": ["1.60.1"], "downloadUrl": None}]}]
        with mock.patch.object(self.providers, "cf_get", side_effect=replies) as get:
            files = self.providers.cf_files("12")
        self.assertEqual(get.call_args_list[1].args[1]["gameVersionTypeId"], 88568)
        self.assertEqual([item["download_available"] for item in files], [True, False])

    def test_cf_distribution_false_stops_before_file_or_download_lookup(self):
        with mock.patch.object(self.providers, "cf_get", return_value={"data": {"allowModDistribution": False}}) as get:
            with self.assertRaises(ValueError):
                self.providers.cf_download_source(12, 21)
        self.assertEqual(get.call_count, 1)

    def test_cf_null_url_and_project_mismatch_stop_before_download_lookup(self):
        cases = [{"modId": 12, "downloadUrl": None},
                 {"modId": 99, "downloadUrl": "https://edge.forgecdn.net/files/1/2/a.zip"}]
        for file_record in cases:
            with self.subTest(file=file_record):
                with mock.patch.object(self.providers, "cf_get", side_effect=[
                        {"data": {"allowModDistribution": True}}, {"data": file_record}]) as get:
                    with self.assertRaises(ValueError):
                        self.providers.cf_download_source(12, 21)
                self.assertEqual(get.call_count, 2)

    def test_cf_download_uses_fixed_api_url_and_rejects_non_https(self):
        url = "https://edge.forgecdn.net/files/1/2/a.zip"
        replies = [{"data": {"allowModDistribution": True}},
                   {"data": {"modId": 12, "downloadUrl": url}}, {"data": url}]
        with mock.patch.object(self.providers, "cf_get", side_effect=replies) as get:
            result = self.providers.cf_download_source("12", "21")
        self.assertEqual(result, {"type": "url", "url": url})
        self.assertEqual(get.call_args.args[0], "mods/12/files/21/download-url")
        replies[-1] = {"data": "http://127.0.0.1/private"}
        with mock.patch.object(self.providers, "cf_get", side_effect=replies):
            with self.assertRaises(ValueError):
                self.providers.cf_download_source(12, 21)

    def test_cf_ids_cannot_inject_provider_paths(self):
        with mock.patch.object(self.providers, "cf_get") as get:
            for value in (None, 0, -1, "../12", "12/files/secret"):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    self.providers.cf_files(value)
            get.assert_not_called()

    def test_wago_403_falls_back_without_claiming_string_or_compatibility_success(self):
        with mock.patch.object(provider_module, "request_json", side_effect=ValueError("外部服务返回 HTTP 403")) as network:
            result = self.providers.wa_search("mage & fire")
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(network.call_args.args[0]).query)
        self.assertEqual(query["expansion"], ["forever"])
        self.assertEqual(query["type"], ["weakaura"])
        self.assertEqual(query["q"], ["mage & fire"])
        self.assertEqual(result["mode"], "website")
        self.assertEqual(result["items"], [])
        self.assertIn("403", result["message"])
        self.assertEqual(urllib.parse.urlsplit(result["website_url"]).netloc, "wago.io")

    def test_wago_does_not_mislabel_classic_results_as_forever(self):
        hits = [
            {"id": "classic-id", "name": "Classic", "type": "WEAKAURA", "expansion": 0},
            {"id": "forever-id", "name": "Forever", "type": "FOREVERAURAS", "expansion": 200},
            {"id": "../../unsafe", "name": "Unsafe", "type": "FOREVERAURAS", "expansion": 200},
        ]
        with mock.patch.object(provider_module, "request_json", return_value={"hits": hits}):
            result = self.providers.wa_search("mage")
        self.assertEqual(result["mode"], "api")
        self.assertEqual([item["id"] for item in result["items"]], ["forever-id"])
        self.assertIn("待验收", result["items"][0]["evidence"])

    def test_wago_preserves_production_foreverauras_with_negative_expansion(self):
        hits = [{"id": "2e-DnshTA", "name": "Mage HUD", "type": "FOREVERAURAS", "expansion": -1},
                {"id": "N13U-73D9", "name": "Mage Books - Forever", "type": "FOREVERAURAS", "expansion": -1},
                {"id": "MqRD-0AuZ", "name": "Powered's fixture", "type": "FOREVERAURAS", "expansion": -1}]
        with mock.patch.object(provider_module, "request_json", return_value={"hits": hits, "total": 22}):
            result = self.providers.wa_search("mage")
        self.assertEqual(result["mode"], "api")
        self.assertEqual([item["id"] for item in result["items"]], [hit["id"] for hit in hits])
        self.assertTrue(all("待验收" in item["evidence"] for item in result["items"]))

    def test_wago_public_raw_fetch_saves_opaque_text_with_canonical_source(self):
        opaque = "!WA:2!opaqueFixtureEncodedString123456"
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = opaque.encode("utf-8")
        opener = mock.Mock()
        opener.open.return_value = response
        with mock.patch.object(provider_module.urllib.request, "build_opener", return_value=opener) as build:
            result = self.providers.fetch_wa({"id": "2e-DnshTA", "name": "Fixture HUD"})
            self.assertIsInstance(build.call_args.args[0], provider_module.PublicWagoRedirect)
        request = opener.open.call_args.args[0]
        parsed = urllib.parse.urlsplit(request.full_url)
        self.assertEqual((parsed.scheme, parsed.netloc, parsed.path),
                         ("https", "data.wago.io", "/api/raw/encoded"))
        self.assertEqual(urllib.parse.parse_qs(parsed.query), {"id": ["2e-DnshTA"]})
        headers = {k.lower(): v for k, v in request.header_items()}
        self.assertEqual(headers["accept"], "text/plain")
        self.assertNotIn("authorization", headers)
        self.assertNotIn("api-key", headers)
        self.assertEqual(Path(result["path"]).read_text(encoding="utf-8"), opaque)
        records = list((self.state / "wa-library").glob("*.json"))
        self.assertEqual(len(records), 1)
        record = json.loads(records[0].read_text(encoding="utf-8"))
        self.assertEqual(record["source_url"], "https://wago.io/2e-DnshTA")
        self.assertEqual(record["compatibility"], "待确认")

    def test_wago_raw_403_and_html_are_rejected_without_saving_artifacts(self):
        for failure in ("403", "html"):
            with self.subTest(failure=failure):
                opener = mock.Mock()
                if failure == "403":
                    opener.open.side_effect = urllib.error.HTTPError(
                        "https://data.wago.io/api/raw/encoded?id=fixture", 403,
                        "Forbidden", {}, io.BytesIO(b"provider-private-response"))
                else:
                    response = mock.MagicMock()
                    response.__enter__.return_value = response
                    response.read.return_value = b"<html>browser verification</html>"
                    opener.open.return_value = response
                with mock.patch.object(provider_module.urllib.request, "build_opener", return_value=opener):
                    with self.assertRaises(ValueError) as rejected:
                        self.providers.fetch_wa({"id": "2e-DnshTA", "name": "Fixture HUD"})
                self.assertIn("作品页", str(rejected.exception))
                self.assertNotIn("provider-private-response", str(rejected.exception))
                self.assertEqual(list(self.state.iterdir()), [])

    def test_wago_raw_invalid_id_is_rejected_before_any_network_request(self):
        with mock.patch.object(provider_module.urllib.request, "build_opener") as network:
            for ident in ("", "../../secret", "fixture?key=private", "https://wago.io/fixture"):
                with self.subTest(ident=ident), self.assertRaises(ValueError):
                    self.providers.fetch_wa({"id": ident, "name": "Fixture"})
            network.assert_not_called()
        self.assertEqual(list(self.state.iterdir()), [])

    def test_wago_wrong_response_shape_uses_website_fallback(self):
        with mock.patch.object(provider_module, "request_json", return_value={"hits": {"unexpected": True}}):
            result = self.providers.wa_search("mage")
        self.assertEqual(result["mode"], "website")
        self.assertEqual(result["items"], [])

    def test_ai_valid_result_has_strict_schema_no_storage_and_no_keys_in_artifact(self):
        self.configure_ai()
        source = {"hello": "Hello %s", "count": "Count %d"}
        translated = {"hello": "你好 %s", "count": "数量 %d"}
        with mock.patch.object(provider_module, "request_json", return_value=completed(translated)) as network:
            result = self.providers.translate({"addon_name": "Fixture", "strings": source})
        url, headers, body = network.call_args.args[:3]
        self.assertEqual(url, "https://api.openai.com/v1/responses")
        self.assertEqual(headers["Authorization"], "Bearer ai-private-sentinel")
        self.assertIs(body["store"], False)
        schema = body["text"]["format"]
        self.assertTrue(schema["strict"])
        self.assertEqual(set(schema["schema"]["required"]), set(source))
        self.assertFalse(schema["schema"]["additionalProperties"])
        self.assertEqual(result["translations"], translated)
        artifacts = list((self.state / "translations").glob("*.json"))
        self.assertEqual(len(artifacts), 1)
        artifact = json.loads(artifacts[0].read_text(encoding="utf-8"))
        self.assertEqual(artifact["source"], source)
        self.assertEqual(artifact["translations"], translated)
        self.assert_secrets_absent(result, ("ai-private-sentinel",))

    def test_ai_missing_extra_or_non_string_keys_are_rejected_without_artifact(self):
        self.configure_ai()
        source = {"a": "Hello %s", "b": "Count %d"}
        invalid = [{"a": "你好 %s"}, {"a": "你好 %s", "b": "数量 %d", "extra": "额外"},
                   {"a": 2, "b": "数量 %d"}, ["not", "a", "dictionary"]]
        for translated in invalid:
            with self.subTest(translated=translated):
                with mock.patch.object(provider_module, "request_json", return_value=completed(translated)):
                    with self.assertRaises(ValueError):
                        self.providers.translate({"addon_name": "Fixture", "strings": source})
                self.assertFalse((self.state / "translations").exists())

    def test_ai_dropped_added_or_reordered_format_tokens_are_rejected(self):
        self.configure_ai()
        cases = [
            ("Hello %s", "你好"), ("Count %d", "数量 %d %s"),
            ("%s has %d items", "%d件物品属于%s"),
            ("|cffffffffHello|r", "|r你好|cffffffff"),
            ("Line\\nValue %0.2f %%", "值 %0.2f %%"),
            ("First\nSecond", "第一第二"), ("Key\tValue", "键值"),
        ]
        for source, target in cases:
            with self.subTest(source=source, target=target):
                with mock.patch.object(provider_module, "request_json", return_value=completed({"a": target})):
                    with self.assertRaises(ValueError):
                        self.providers.translate({"addon_name": "Fixture", "strings": {"a": source}})
                self.assertFalse((self.state / "translations").exists())

    def test_ai_invalid_input_or_missing_configuration_never_calls_network(self):
        with mock.patch.object(provider_module, "request_json") as network:
            with self.assertRaises(ValueError):
                self.providers.translate({"addon_name": "Fixture", "strings": {"a": "Hello"}})
            self.configure_ai()
            for strings in ({}, {"a": 12}, ["not a dictionary"], {"a": "x" * 3001}):
                with self.subTest(strings=type(strings).__name__), self.assertRaises(ValueError):
                    self.providers.translate({"addon_name": "Fixture", "strings": strings})
            network.assert_not_called()
        self.assertFalse((self.state / "translations").exists())

    def test_ai_incomplete_or_non_json_response_never_saves_output(self):
        self.configure_ai()
        replies = [{"status": "incomplete", "output": []},
                   {"status": "completed", "output": [{"type": "message", "content": [
                       {"type": "output_text", "text": "not JSON"}]}]}]
        for response in replies:
            with self.subTest(response=response):
                with mock.patch.object(provider_module, "request_json", return_value=response):
                    with self.assertRaises(ValueError):
                        self.providers.translate({"addon_name": "Fixture", "strings": {"a": "Hello"}})
                self.assertFalse((self.state / "translations").exists())

    def test_translation_job_is_offline_and_wa_is_stored_without_execution(self):
        with mock.patch.object(provider_module, "request_json") as network:
            job = self.providers.translation_request({"addon_name": "Fixture", "strings": {"x": "Hello %s"}})
        network.assert_not_called()
        self.assertTrue(Path(job["path"]).is_file())
        marker = self.state / "must-not-exist"
        opaque = "!WA:2!__import__('pathlib').Path(%r).touch()" % str(marker)
        with mock.patch("builtins.eval", side_effect=AssertionError("must not evaluate input")):
            record = self.providers.save_wa({"name": "../Fixture", "string": opaque,
                                             "source_url": "https://wago.io/fixture"})
        self.assertFalse(marker.exists())
        self.assertEqual(Path(record["path"]).read_text(encoding="utf-8"), opaque)
        self.assertEqual(Path(record["path"]).parent, self.state / "wa-library")
        library = self.providers.wa_library()
        self.assertEqual(library[0]["sha256"], record["sha256"])
        self.assertIn("/downloads/wa-library/", library[0]["download_url"])


class ProviderTransportTests(unittest.TestCase):
    def test_json_transport_sets_headers_and_body_without_external_network(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b'{"result":"ok"}'
        opener = mock.Mock()
        opener.open.return_value = response
        with mock.patch.object(provider_module.urllib.request, "build_opener", return_value=opener):
            result = provider_module.request_json("https://api.openai.com/v1/responses",
                                                  {"Authorization": "Bearer transport-sentinel"},
                                                  {"input": "中文", "store": False}, timeout=9)
        self.assertEqual(result, {"result": "ok"})
        request = opener.open.call_args.args[0]
        headers = {k.lower(): v for k, v in request.header_items()}
        self.assertEqual(headers["authorization"], "Bearer transport-sentinel")
        self.assertEqual(headers["content-type"], "application/json")
        self.assertEqual(headers["accept"], "application/json")
        self.assertIn("ForeverAddonWorkshop", headers["user-agent"])
        self.assertEqual(json.loads(request.data.decode("utf-8")), {"input": "中文", "store": False})
        self.assertNotIn("transport-sentinel", request.full_url)
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 9)

    def test_provider_http_and_header_errors_never_echo_credentials_or_response_body(self):
        sentinel = "transport-private-sentinel"
        errors = [urllib.error.HTTPError("https://api.curseforge.com/v1/mods/search", 403,
                                        "server said " + sentinel, {}, io.BytesIO(sentinel.encode())),
                  ValueError("Invalid header value b'%s\\ninvalid'" % sentinel),
                  urllib.error.URLError("network echoed " + sentinel)]
        for error in errors:
            with self.subTest(error=type(error).__name__):
                opener = mock.Mock()
                opener.open.side_effect = error
                with mock.patch.object(provider_module.urllib.request, "build_opener", return_value=opener):
                    with self.assertRaises(ValueError) as rejected:
                        provider_module.request_json("https://api.curseforge.com/v1/mods/search",
                                                     {"x-api-key": sentinel})
                self.assertNotIn(sentinel, str(rejected.exception))
                if isinstance(error, urllib.error.HTTPError):
                    self.assertIn("403", str(rejected.exception))

    def test_oversized_invalid_utf8_and_non_json_provider_responses_are_rejected(self):
        for raw in (b"x" * 33, b"\xff", b"<html>browser verification</html>"):
            with self.subTest(raw=raw):
                response = mock.MagicMock()
                response.__enter__.return_value = response
                response.read.return_value = raw
                opener = mock.Mock()
                opener.open.return_value = response
                with mock.patch.object(provider_module, "MAX_RESPONSE", 32), \
                        mock.patch.object(provider_module.urllib.request, "build_opener", return_value=opener):
                    with self.assertRaises(ValueError):
                        provider_module.request_json("https://data.wago.io/search/es?q=fixture")
                response.read.assert_called_once_with(33)

    def test_sensitive_headers_are_not_forwarded_across_origin_redirects(self):
        captured = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                if self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", "http://localhost:%d/destination" % self.server.server_port)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                else:
                    captured.append(dict(self.headers))
                    raw = b'{"ok":true}'
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            try:
                provider_module.request_json("http://127.0.0.1:%d/redirect" % server.server_port,
                                             {"x-api-key": "cf-redirect-sentinel",
                                              "Authorization": "Bearer ai-redirect-sentinel"}, timeout=3)
            except ValueError as error:
                self.assertNotIn("redirect-sentinel", str(error))
            for headers in captured:
                serialized = json.dumps(headers)
                self.assertNotIn("cf-redirect-sentinel", serialized)
                self.assertNotIn("ai-redirect-sentinel", serialized)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


class PublicWagoRedirectTests(unittest.TestCase):
    def test_same_origin_version_redirect_is_allowed_without_credentials(self):
        request = provider_module.urllib.request.Request(
            'https://data.wago.io/api/raw/encoded?id=fixture',
            headers={'Authorization': 'sentinel', 'x-api-key': 'sentinel',
                     'api-key': 'sentinel', 'Cookie': 'sentinel', 'Accept': 'text/plain'})
        redirect = provider_module.PublicWagoRedirect().redirect_request(
            request, None, 302, 'Found', {},
            'https://data.wago.io/api/raw/encoded?id=fixture&version=1')
        self.assertEqual(redirect.full_url, 'https://data.wago.io/api/raw/encoded?id=fixture&version=1')
        self.assertNotIn('sentinel', json.dumps(dict(redirect.header_items())))
        self.assertEqual(redirect.get_header('Accept'), 'text/plain')

    def test_other_hosts_protocols_ports_paths_and_userinfo_are_rejected(self):
        request = provider_module.urllib.request.Request('https://data.wago.io/api/raw/encoded?id=fixture')
        targets = ('https://evil.example/api/raw/encoded', 'http://data.wago.io/api/raw/encoded',
                   'https://data.wago.io:444/api/raw/encoded', 'https://user@data.wago.io/api/raw/encoded',
                   'https://data.wago.io/lookup/wago', 'file:///etc/passwd')
        for url in targets:
            with self.subTest(url=url), self.assertRaises(ValueError):
                provider_module.PublicWagoRedirect().redirect_request(request, None, 302, 'Found', {}, url)


if __name__ == "__main__":
    unittest.main()
