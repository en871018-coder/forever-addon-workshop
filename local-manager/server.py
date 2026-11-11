#!/usr/bin/env python3
"""Loopback-only local addon workshop. Python 3.9+, standard library only."""
import argparse
import errno
import hmac
import json
import mimetypes
import secrets
import sys
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from core import ManagerCore, VERSION
from providers import Providers, now

APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parent


class Workshop:
    def __init__(self, root=PROJECT_ROOT, state_dir=None):
        self.state_dir = Path(state_dir or (Path(root) / '.manager-state'))
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.core = ManagerCore(Path(root), self.state_dir)
        self.providers = Providers(self.state_dir)
        self.token = secrets.token_urlsafe(32)
        self.activity = []
        self.mutation_lock = threading.Lock()

    def status(self):
        return dict(self.core.get_status(), **self.providers.flags())

    def log(self, action, message):
        self.activity.insert(0, {'time': now(), 'action': action, 'message': message})
        self.activity = self.activity[:50]

    def get(self, path, query):
        q = query.get('q', [''])[0][:200].strip()
        if path == '/api/status':
            return self.status()
        if path == '/api/installed':
            return self.core.installed()
        if path == '/api/catalog':
            return self.core.catalog(q)
        if path == '/api/search':
            if not q:
                raise ValueError('请先输入插件名称或关键词。')
            return self.providers.search_cf(q)
        if path == '/api/cf/files':
            return self.providers.cf_files(query.get('mod_id', [''])[0])
        if path == '/api/wa/search':
            if not q:
                raise ValueError('请先输入关键词，例如 mage 或法师技能名。')
            return self.providers.wa_search(q)
        if path == '/api/wa/library':
            return self.providers.wa_library()
        if path == '/api/translations':
            installed = {x['name'] for x in self.core.installed()}
            return [dict(x, addon_id=x['id'], installed=all(n in installed for n in x['addon_names']))
                    for x in self.core.catalog() if x['id'] in ('bofguide-zhcn', 'foreverquesttools')]
        if path == '/api/activity':
            return self.activity
        raise FileNotFoundError('没有这个接口。')

    def post(self, path, data):
        if path == '/api/settings':
            with self.mutation_lock:
                self.providers.validate_configuration(data)
                if 'game_path' in data:
                    self.core.set_game_path(data['game_path'])
                self.providers.configure(data)
                self.log('设置', '已更新本地设置；密钥仅存当前进程。')
                return self.status()
        if path == '/api/install/plan':
            with self.mutation_lock:
                if data.get('type') == 'curseforge':
                    data = self.providers.cf_download_source(data.get('mod_id'), data.get('file_id'))
                result = self.core.plan_install(data)
                self.log('检查安装包', '已检查压缩包；等待你确认后才会写入游戏目录。')
                return result
        if path == '/api/install/confirm':
            with self.mutation_lock:
                result = self.core.execute_install(data.get('plan_id', ''), data.get('confirm') is True)
                self.log('安装', '插件安装完成，原目录已备份；游戏内功能仍需验收。')
                return result
        if path == '/api/wa/save':
            result = self.providers.save_wa(data)
            self.log('保存字符串', '已保存到本地 WA 库，未导入游戏。')
            return result
        if path == '/api/wa/fetch':
            result = self.providers.fetch_wa(data)
            self.log('取得公开字符串', '已保存公开导入文本与来源；未执行或导入游戏。')
            return result
        if path == '/api/translation/request':
            result = self.providers.translation_request(data)
            self.log('汉化任务', '已导出可交给 Codex 的汉化任务。')
            return result
        if path == '/api/translate':
            # An explicit button click in the UI is required for this paid API call.
            result = self.providers.translate(data)
            self.log('AI 汉化', '翻译字典与格式检查完成，待接入插件语言接口。')
            return result
        raise FileNotFoundError('没有这个接口。')


def make_handler(workshop):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'ForeverWorkshop/' + VERSION

        def log_message(self, format, *args):
            pass  # No request bodies, paths or keys in console logs.

        def allowed_host(self):
            hosts = {'127.0.0.1:%s' % self.server.server_port,
                     'localhost:%s' % self.server.server_port}
            if self.headers.get('Host') not in hosts:
                self.respond(403, {'ok': False, 'error': '仅允许本机访问。'})
                return False
            origin = self.headers.get('Origin')
            if origin and origin not in {'http://' + h for h in hosts}:
                self.respond(403, {'ok': False, 'error': '拒绝跨站请求。'})
                return False
            if self.headers.get('Sec-Fetch-Site') == 'cross-site':
                self.respond(403, {'ok': False, 'error': '拒绝跨站请求。'})
                return False
            return True

        def authenticated(self):
            candidate = self.headers.get('X-Workshop-Token', '')
            if not hmac.compare_digest(candidate.encode('utf-8'), workshop.token.encode('utf-8')):
                self.respond(401, {'ok': False, 'error': '本地会话已失效，请通过启动窗口重新打开界面。'})
                return False
            return True

        def respond(self, code, value=None, content=None, mime='application/json; charset=utf-8', filename=None):
            raw = content if content is not None else json.dumps(value, ensure_ascii=False).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            if filename:
                self.send_header('Content-Disposition', "attachment; filename*=UTF-8''" + urllib.parse.quote(filename))
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            if not self.allowed_host():
                return
            parsed = urllib.parse.urlsplit(self.path)
            path = urllib.parse.unquote(parsed.path)
            try:
                if path.startswith('/api/'):
                    if not self.authenticated():
                        return
                    self.respond(200, {'ok': True, 'data': workshop.get(path, urllib.parse.parse_qs(parsed.query))})
                elif path.startswith('/downloads/'):
                    if not self.authenticated():
                        return
                    pieces = path.split('/')
                    if len(pieces) != 4 or pieces[2] not in ('translations', 'translation-requests', 'wa-library'):
                        raise FileNotFoundError()
                    category = workshop.state_dir / pieces[2]
                    base = category.resolve()
                    raw_candidate = category / pieces[3]
                    candidate = raw_candidate.resolve()
                    if (category.is_symlink() or raw_candidate.is_symlink()
                            or base.parent != workshop.state_dir.resolve() or candidate.parent != base
                            or not candidate.is_file() or candidate.suffix not in ('.json', '.txt')):
                        raise FileNotFoundError()
                    self.respond(200, content=candidate.read_bytes(), mime='application/octet-stream', filename=candidate.name)
                elif path in ('/', '/index.html', '/app.js', '/style.css'):
                    file = APP_DIR / 'web' / ('index.html' if path == '/' else path[1:])
                    self.respond(200, content=file.read_bytes(), mime=mimetypes.guess_type(file.name)[0] + '; charset=utf-8')
                elif path == '/favicon.ico':
                    self.respond(204, content=b'', mime='image/x-icon')
                else:
                    raise FileNotFoundError()
            except FileNotFoundError:
                self.respond(404, {'ok': False, 'error': '文件或接口不存在。'})
            except (ValueError, KeyError) as error:
                self.respond(400, {'ok': False, 'error': str(error)})
            except Exception:
                self.respond(500, {'ok': False, 'error': '本地操作失败，请检查目录权限或重试。未向页面输出私密配置。'})

        def do_POST(self):
            if not self.allowed_host() or not self.authenticated():
                return
            try:
                if self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
                    raise ValueError('仅接受 JSON 请求。')
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 3_000_000:
                    raise ValueError('请求为空或超过大小限制。')
                data = json.loads(self.rfile.read(length).decode('utf-8'))
                if not isinstance(data, dict):
                    raise ValueError('请求应为 JSON 对象。')
                path = urllib.parse.urlsplit(self.path).path
                self.respond(200, {'ok': True, 'data': workshop.post(path, data)})
            except FileNotFoundError:
                self.respond(404, {'ok': False, 'error': '接口不存在。'})
            except (ValueError, KeyError, UnicodeError) as error:
                self.respond(400, {'ok': False, 'error': str(error)})
            except Exception:
                self.respond(500, {'ok': False, 'error': '本地操作失败，请检查权限后重试。安装失败时会尝试恢复原目录。'})
    return Handler


def create_local_server(workshop, preferred_port=17860, port_attempts=20):
    """Bind a loopback port; leave existing applications and sessions untouched."""
    if (not isinstance(preferred_port, int) or isinstance(preferred_port, bool)
            or not 0 <= preferred_port <= 65535):
        raise ValueError('端口必须为 0–65535 之间的整数。')
    if not isinstance(port_attempts, int) or isinstance(port_attempts, bool) or port_attempts < 1:
        raise ValueError('端口尝试次数必须为正整数。')
    candidates = [0] if preferred_port == 0 else range(preferred_port, min(65536, preferred_port + port_attempts))
    last_error = None
    for port in candidates:
        try:
            return ThreadingHTTPServer(('127.0.0.1', port), make_handler(workshop))
        except OSError as error:
            if error.errno != errno.EADDRINUSE and getattr(error, 'winerror', None) != 10048:
                raise
            last_error = error
    raise OSError(errno.EADDRINUSE, '所尝试的本机端口均已占用。') from last_error


def main():
    parser = argparse.ArgumentParser(description='Forever 插件工坊，本地个人插件管理器')
    parser.add_argument('--port', type=int, default=17860)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    workshop = Workshop()
    try:
        server = create_local_server(workshop, args.port)
    except ValueError as error:
        print('启动失败：' + str(error), flush=True)
        return 1
    except OSError as error:
        if error.errno == errno.EADDRINUSE:
            print('启动失败：尝试的本机端口均被占用，请关闭旧工坊窗口后重试。', flush=True)
        else:
            print('启动失败：无法创建本机服务，请检查系统网络权限后重试。', flush=True)
        return 1
    server.daemon_threads = True
    url = 'http://127.0.0.1:%s/#%s' % (server.server_port, workshop.token)
    print('Forever 插件工坊 ' + VERSION + ' · 仅本机可访问', flush=True)
    if args.port and server.server_port != args.port:
        print('端口 %s 已占用，已自动使用可用端口 %s。' % (args.port, server.server_port), flush=True)
    print('打开界面：' + url, flush=True)
    print('关闭此窗口或按 Ctrl+C 停止。新增插件需完整重开游戏。', flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
