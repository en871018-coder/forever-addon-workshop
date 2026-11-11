"""Local, stdlib-only WoW Forever add-on inspection and confirmed installation.

No Lua, vendor scripts or archives are executed.  Only the selected Forever
branch metadata and Interface/AddOns are inspected; WTF is never accessed.
"""

import csv
import hashlib
import io
import json
import os
import platform
import plistlib
import re
import shutil
import stat
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


VERSION = "0.1.1"
TARGET_INTERFACE = 16001
MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024
MAX_UNPACKED_BYTES = 500 * 1024 * 1024
MAX_FILES = 10000
# QuestieDB's published Forever TOCs embed a ~15 MiB data store.
MAX_TOC_BYTES = 32 * 1024 * 1024
MAX_COMPRESSION_RATIO = 300
PLAN_LIFETIME = 3600
_NETWORK_HOSTS = {
    "github.com", "release-assets.githubusercontent.com",
    "objects.githubusercontent.com", "github-releases.githubusercontent.com",
    "edge.forgecdn.net", "media.forgecdn.net", "mediafilez.forgecdn.net",
    "bindonforever.com", "www.bindonforever.com",
}
_EXECUTABLE_SUFFIXES = {
    ".exe", ".com", ".dll", ".dylib", ".so", ".msi", ".pkg", ".dmg",
    ".app", ".bat", ".cmd", ".ps1", ".vbs", ".vbe", ".scr", ".sh",
    ".bash", ".zsh", ".py", ".pyc", ".jar", ".class",
}


class ManagerError(ValueError):
    def __init__(self, message, code="invalid_request"):
        super().__init__(message)
        self.code = code


def _sha256(path):
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def _read_json(path, default):
    if not path.is_file() or path.is_symlink():
        return default
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".write-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, str(path))
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _plain_title(value):
    value = re.sub(r"\|c[0-9a-fA-F]{8}|\|r", "", value)
    value = re.sub(r"\|[TA].*?\|[ta]", "", value)
    return value.strip()


def _toc(text):
    metadata = {}
    for line in text.splitlines():
        match = re.match(r"^\s*##\s*([^:]+):\s*(.*?)\s*$", line)
        if match:
            key = match.group(1).strip().lower()
            if key in ("title", "title-zhcn", "version", "dependencies", "requireddeps", "interface") or key.startswith("interface-"):
                metadata[key] = match.group(2)
    interfaces = []
    for key, value in metadata.items():
        if key == "interface" or key.startswith("interface-"):
            interfaces.extend(int(n) for n in re.findall(r"\d+", value))
    return {
        "title": _plain_title(metadata.get("title-zhcn") or metadata.get("title", "")),
        "version": metadata.get("version", "待确认"),
        "interface": ",".join(str(n) for n in sorted(set(interfaces))),
        "interfaces": sorted(set(interfaces)),
        "dependencies": [s for s in re.split(r"[,\s]+", metadata.get("dependencies", metadata.get("requireddeps", ""))) if s],
    }


def _safe_url(url, redirect=False):
    if not isinstance(url, str) or len(url) > 4096:
        raise ManagerError("下载地址无效。", "unsafe_url")
    parsed = urllib.parse.urlsplit(url)
    try:
        valid_port = parsed.port in (None, 443)
    except ValueError:
        valid_port = False
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not valid_port or parsed.username or parsed.password or host not in _NETWORK_HOSTS:
        raise ManagerError("仅接受已允许的官方 HTTPS 发行包地址。", "unsafe_url")
    if "\\" in url or any(ord(c) < 32 for c in url):
        raise ManagerError("下载地址含不安全字符。", "unsafe_url")
    if host == "github.com" and not re.match(r"^/[^/]+/[^/]+/releases/download/[^/]+/[^/]+$", parsed.path):
        raise ManagerError("GitHub 地址须直接指向 release 下载资产。", "unsafe_url")
    if host.endswith("forgecdn.net") and not re.match(r"^/files/\d+/\d+/[^/]+$", parsed.path):
        raise ManagerError("ForgeCDN 地址须直接指向固定文件。", "unsafe_url")
    if host in ("bindonforever.com", "www.bindonforever.com") and parsed.path != "/api/download/guide":
        raise ManagerError("仅接受 BoF 官方免费指南下载地址。", "unsafe_url")
    return url


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _safe_url(newurl, redirect=True)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class ManagerCore:
    def __init__(self, project_root: Path, state_dir: Path):
        self.project_root = Path(project_root).resolve()
        raw_state = Path(state_dir).expanduser().absolute()
        if any(part.casefold() == "wtf" for part in raw_state.parts):
            raise ManagerError("管理器状态目录不能放在 WTF 个人设置目录内。", "unsafe_state")
        if self._has_symlink(raw_state):
            raise ManagerError("管理器状态目录不能使用符号链接。", "unsafe_state")
        self.state_dir = raw_state.resolve()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._plans = {}
        self._config_path = self.state_dir / "config.json"
        self._managed_path = self.state_dir / "managed.json"
        self._catalog = self._load_catalog()
        config = _read_json(self._config_path, {})
        self.game_path = None
        candidate = config.get("game_path") if isinstance(config, dict) else None
        if not candidate and platform.system() == "Darwin":
            default = Path("/Applications/World of Warcraft/_forever_")
            if default.is_dir():
                candidate = str(default)
        if candidate:
            try:
                branch, _ = self._validate_game_path(candidate)
                self.game_path = branch
                _write_json(self._config_path, {"game_path": str(branch)})
            except ManagerError:
                pass

    def _load_catalog(self):
        """Pinned, attributed releases from this project's installation records."""
        specs = [
            ("foreverauras", "ForeverAuras", "1.1.0", "法术监控", False, "GPLv2", "ForeverAuras 及四个按需模块。", ["ForeverAuras", "ForeverAurasArchive", "ForeverAurasModelPaths", "ForeverAurasOptions", "ForeverAurasTemplates"], "https://github.com/neroxrw/foreverauras/releases/download/1.1.0/ForeverAuras-1.1.0.zip", "references/vendor/installation-2026-11-04/downloads/ForeverAuras-1.1.0.zip", "1ef62cfe71e046a6ba1b7cf076a886c0ac4ff7536adcffbc73437b5015f39cf4"),
            ("gearmenu", "GearMenu", "3.0.0", "装备", False, "MIT", "Forever 换装菜单；该版没有 QuickChange。", ["GearMenu"], "https://www.curseforge.com/wow/addons/gearmenu-rg/files/9268676", "references/vendor/installation-2026-11-04/downloads/gearmenu-forever-v3.0.0.zip", "f84fc09b1a8667ec3482f668b3b7f908ce802846bc0041db3b3fbd25f4be5e0f"),
            ("mageprocs", "MageProcs", "1.6.0", "法师提示", False, "All Rights Reserved；以官方平台许可为准", "人物周围法师触发动画。", ["MageProcs"], "https://www.curseforge.com/wow/addons/mageprocs/files/9276955", "references/vendor/installation-2026-11-04/downloads/MageProcs-1.6.0.zip", "8fe7f77e94fc13cec596bb5006b042c473a3ccf0dc645d157ef6203bb70cdcd5"),
            ("bofguide", "BoFGuide", "0.9.101", "练级路线", False, "All Rights Reserved；官方免费版个人游玩", "免费练级及采矿、工程路线。", ["BoFGuide"], "https://bindonforever.com/api/download/guide", "references/vendor/bof-installation-2026-11-05/BoFGuide-0.9.101.zip", "5f532368ac16cddab30a1163358a48998ec3b571dccf759e190c28c0182b7231"),
            ("questie", "Questie", "12.6.2", "任务地图", False, "GPLv3", "任务地图；需要 Forever 版 QuestieDB。", ["Questie"], "https://www.curseforge.com/wow/addons/questie/files/9467165", "references/vendor/quest-map-installation-2026-11-06/downloads/Questie-v12.6.2.zip", "767a12fde9ac814caeb93a14f19399e98401517227f600a971531eb16a12620e"),
            ("questiedb", "QuestieDB", "1.0.11 Forever", "任务数据库", False, "GPLv3", "Questie 的 Forever 任务和中文实体数据库。", ["QuestieDB"], "https://www.curseforge.com/wow/addons/questiedb/files/9455844", "references/vendor/quest-map-installation-2026-11-06/downloads/QuestieDB-1.0.11-Forever.zip", "c7cddd2dcee05eb00ef2a5935e09073e4641c06318029c87ca26231d9ffb2fd4"),
            ("dungeonquestatlas", "DungeonQuestAtlas", "2.16", "副本任务", False, "All Rights Reserved；仅原版个人安装", "含363项中文的副本任务图鉴。", ["DungeonQuestAtlas"], "https://www.curseforge.com/wow/addons/dungeon-quest-atlas/files/9458531", "references/vendor/quest-map-installation-2026-11-06/atlas-inspect/DungeonQuestAtlas_V2.16.zip", "206897786ccdcac9f91f876e595e6a1777e37b5fd462bb83d27a235bee56d074"),
            ("bofguide-zhcn", "BoFGuide_zhCN", "0.1.0", "中文补丁", True, "本项目独立模块；个人使用，见模块说明", "BoF 独立中文补丁，保留英文回退。", ["BoFGuide_zhCN"], "local:project/addons/BoFGuide_zhCN", "addons/BoFGuide_zhCN", None),
            ("foreverquesttools", "ForeverQuestTools", "0.1.0", "中文与预设", True, "GPL-3.0-only", "Questie 缺项补译与一次性地图/副本预设。", ["ForeverQuestTools"], "local:project/addons/ForeverQuestTools", "addons/ForeverQuestTools", None),
        ]
        result = []
        for item_id, name, version, category, translation, license_name, description, names, source, local_path, digest in specs:
            result.append({"id": item_id, "name": name, "title": name, "version": version,
                           "category": category, "translation": translation, "license": license_name,
                           "description": description, "addon_names": names, "source_url": source,
                           "download_url": source if item_id in ("foreverauras", "bofguide") else None,
                           "declared_interfaces": [TARGET_INTERFACE], "evidence": "项目固定发行包与离线检查记录；当前客户端实机效果待验收。",
                           "_local_path": local_path, "sha256": digest})
        return result

    @staticmethod
    def _has_symlink(path):
        # macOS exposes its ordinary temporary folders through system aliases.
        system_aliases = {"/var", "/tmp", "/etc"} if platform.system() == "Darwin" else set()
        return any(p.is_symlink() and str(p) not in system_aliases for p in (path,) + tuple(path.parents))

    @staticmethod
    def _build_record(branch):
        for path in (branch.parent / ".build.info", branch / ".build.info"):
            if not path.is_file() or path.is_symlink() or path.stat().st_size > 1024 * 1024:
                continue
            try:
                rows = list(csv.reader(io.StringIO(path.read_text(encoding="utf-8-sig")), delimiter="|"))
                if not rows:
                    continue
                headers = [s.split("!", 1)[0] for s in rows[0]]
                for row in rows[1:]:
                    record = dict(zip(headers, row))
                    if record.get("Product") == "wowf" and record.get("Active", "1") == "1":
                        return record.get("Version") or "待确认"
            except (OSError, UnicodeError, csv.Error):
                pass
        return None

    def _validate_game_path(self, value):
        if not isinstance(value, str) or not value.strip() or len(value) > 4096:
            raise ManagerError("请选择无限服 _forever_ 分支目录或其 Interface/AddOns。", "invalid_game_path")
        raw = Path(value).expanduser().absolute()
        if self._has_symlink(raw):
            raise ManagerError("游戏路径不能使用符号链接。", "invalid_game_path")
        branch = raw
        if raw.name.casefold() == "addons" and raw.parent.name.casefold() == "interface":
            branch = raw.parent.parent
        branch = branch.resolve()
        if branch.name.casefold() != "_forever_" or not branch.is_dir():
            raise ManagerError("仅支持已存在的无限服 _forever_ 分支，不能选择正式服或其他经典服。", "invalid_game_path")
        if self._has_symlink(branch / "Interface") or self._has_symlink(branch / "Interface" / "AddOns"):
            raise ManagerError("Interface/AddOns 不能指向符号链接。", "invalid_game_path")
        evidence = False
        flavor = branch / ".flavor.info"
        if flavor.is_file() and not flavor.is_symlink() and flavor.stat().st_size <= 8192:
            try:
                evidence = bool(re.search(r"(?<![A-Za-z0-9_])wowf(?![A-Za-z0-9_])", flavor.read_text(encoding="utf-8")))
            except (OSError, UnicodeError):
                pass
        build = self._build_record(branch)
        evidence = evidence or build is not None
        plist = branch / "World of Warcraft.app" / "Contents" / "Info.plist"
        if plist.is_file() and not self._has_symlink(plist) and plist.stat().st_size <= 1024 * 1024:
            try:
                info = plistlib.loads(plist.read_bytes())
                version = str(info.get("CFBundleVersion", ""))
                identifier = str(info.get("CFBundleIdentifier", "")).lower()
                if version.startswith("1.60.") and ("warcraft" in identifier or "wow" in identifier):
                    evidence = True
                    if build is None:
                        build = version
            except (OSError, ValueError, plistlib.InvalidFileException):
                pass
        if not evidence:
            raise ManagerError("该目录没有 wowf 产品标识，无法确认是无限服。", "unverified_game_path")
        return branch, build or "待确认"

    def set_game_path(self, path: str):
        with self._lock:
            branch, _ = self._validate_game_path(path)
            _write_json(self._config_path, {"game_path": str(branch)})
            self.game_path = branch
            return self.get_status()

    def _game_running(self):
        try:
            if os.name == "nt":
                data = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=8, check=True).stdout
                if not data.strip():
                    return None
                names = [row[0].casefold() for row in csv.reader(io.StringIO(data)) if row]
                return any(name in {"wow.exe", "wowclassic.exe", "wow64.exe", "world of warcraft.exe"} for name in names)
            data = subprocess.run(["ps", "-axo", "comm="], capture_output=True, text=True, timeout=8, check=True).stdout
            if not data.strip():
                return None
            wanted = {"World of Warcraft", "Wow", "WowClassic", "Wow.exe", "WowClassic.exe"}
            return any(Path(line.strip()).name in wanted for line in data.splitlines())
        except (OSError, subprocess.SubprocessError):
            return None

    def get_status(self):
        build, path_error = "待确认", None
        if self.game_path:
            try:
                _, build = self._validate_game_path(str(self.game_path))
            except ManagerError as exc:
                path_error = str(exc)
        return {"version": VERSION, "game_path": str(self.game_path) if self.game_path else None,
                "addon_path": str(self.game_path / "Interface" / "AddOns") if self.game_path else None,
                "client_build": build, "game_running": self._game_running(),
                "installed_count": len(self.installed()) if self.game_path and not path_error else 0,
                "target": "Forever", "platform": platform.system(), "path_error": path_error,
                "evidence": "分支元数据与 TOC 为只读证据；游戏运行兼容性待验收。"}

    def installed(self):
        if not self.game_path:
            return []
        branch, _ = self._validate_game_path(str(self.game_path))
        addons = branch / "Interface" / "AddOns"
        if not addons.is_dir():
            return []
        receipts = _read_json(self._managed_path, {})
        branch_receipts = receipts.get(str(branch), {}) if isinstance(receipts, dict) else {}
        by_name = {name.casefold(): item for item in self._catalog for name in item["addon_names"]}
        result = []
        for folder in sorted(addons.iterdir(), key=lambda p: p.name.casefold()):
            if folder.name.startswith(".") or folder.is_symlink() or not folder.is_dir():
                continue
            candidates = []
            for toc in folder.iterdir():
                if toc.suffix.lower() != ".toc" or toc.is_symlink() or not toc.is_file() or toc.stat().st_size > MAX_TOC_BYTES:
                    continue
                if not self._toc_matches(folder.name, toc.name):
                    continue
                try:
                    meta = _toc(toc.read_text(encoding="utf-8-sig", errors="replace"))
                except OSError:
                    continue
                rank = (TARGET_INTERFACE not in meta["interfaces"], not toc.stem.casefold().endswith("_camelot"), toc.stem.casefold() != folder.name.casefold(), toc.name.casefold())
                candidates.append((rank, meta))
            if not candidates:
                continue
            meta = min(candidates, key=lambda pair: pair[0])[1]
            known = by_name.get(folder.name.casefold())
            receipt = branch_receipts.get(folder.name, {})
            declared = TARGET_INTERFACE in meta["interfaces"]
            result.append({"name": folder.name, "title": meta["title"] or folder.name,
                           "version": meta["version"], "interface": meta["interface"],
                           "forever_declared": declared,
                           "evidence": "TOC 声明 Forever Interface 16001；当前 build 的实机兼容性待验收。" if declared else "TOC 未声明 Forever Interface 16001，兼容性待确认。",
                           "source_url": receipt.get("source_url") or (known["source_url"] if known else None),
                           "managed": bool(receipt), "catalog_id": known["id"] if known else None})
        return result

    def catalog(self, query=""):
        query = str(query or "").strip().casefold()
        result = []
        for item in self._catalog:
            if query and query not in " ".join(str(item.get(k, "")) for k in ("name", "title", "category", "description")).casefold():
                continue
            public = {k: v for k, v in item.items() if not k.startswith("_")}
            public["local_available"] = (self.project_root / item["_local_path"]).exists()
            result.append(public)
        return result

    def _state_subdir(self, name):
        path = self.state_dir / name
        if self._has_symlink(path):
            raise ManagerError("管理器工作目录含符号链接，已停止。", "unsafe_state")
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def _toc_matches(addon, filename):
        stem = filename[:-4]
        return stem.casefold() == addon.casefold() or stem.casefold().startswith(addon.casefold() + "_")

    @staticmethod
    def _safe_member(name):
        name = name.replace("\\", "/")
        if not name or name.startswith("/") or "\x00" in name or len(name) > 512:
            raise ManagerError("ZIP 含绝对路径或不安全文件名。", "unsafe_archive")
        pieces = name.rstrip("/").split("/")
        if len(pieces) > 32 or any(p in ("", ".", "..") for p in pieces):
            raise ManagerError("ZIP 含路径穿越或不安全层级。", "unsafe_archive")
        for piece in pieces:
            if re.search(r'[<>:"|?*\x00-\x1f]', piece) or piece.endswith((".", " ")):
                raise ManagerError("ZIP 文件名不符合跨平台安全要求。", "unsafe_archive")
            if piece.casefold() == "wtf":
                raise ManagerError("不接受包含 WTF 或个人设置的插件包。", "unsafe_archive")
            if re.match(r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)", piece, re.I):
                raise ManagerError("ZIP 含 Windows 保留文件名。", "unsafe_archive")
            if Path(piece).suffix.casefold() in _EXECUTABLE_SUFFIXES:
                raise ManagerError("插件包含可执行程序或外部脚本，已拒绝。", "executable_archive")
        return "/".join(pieces)

    def _inspect_archive(self, path):
        entries, canonical, all_files = {}, {}, set()
        total, compressed, count = 0, 0, 0
        try:
            with zipfile.ZipFile(str(path)) as archive:
                if len(archive.infolist()) > MAX_FILES * 2:
                    raise ManagerError("ZIP 条目数量超过限制。", "archive_limit")
                for entry in archive.infolist():
                    name = self._safe_member(entry.filename)
                    is_dir = entry.filename.endswith(("/", "\\"))
                    mode = (entry.external_attr >> 16) & 0o170000
                    if mode not in (0, stat.S_IFREG, stat.S_IFDIR) or entry.flag_bits & 1:
                        raise ManagerError("ZIP 含符号链接、特殊文件或加密文件。", "unsafe_archive")
                    parts = name.split("/")
                    for end in range(1, len(parts) + 1):
                        prefix = "/".join(parts[:end])
                        folded = prefix.casefold()
                        if folded in canonical and canonical[folded] != prefix:
                            raise ManagerError("ZIP 存在大小写碰撞，无法跨平台安全安装。", "case_collision")
                        canonical[folded] = prefix
                    if name in entries:
                        if is_dir and entries[name][1]:
                            continue
                        raise ManagerError("ZIP 含重复文件。", "unsafe_archive")
                    entries[name] = (entry, is_dir)
                    if is_dir:
                        continue
                    count += 1
                    total += entry.file_size
                    compressed += entry.compress_size
                    all_files.add(name)
                    if count > MAX_FILES or total > MAX_UNPACKED_BYTES or entry.file_size > MAX_UNPACKED_BYTES:
                        raise ManagerError("ZIP 展开大小或文件数量超过限制。", "archive_limit")
                if total > max(compressed, 1) * MAX_COMPRESSION_RATIO:
                    raise ManagerError("ZIP 压缩比过高，已拒绝解包。", "archive_limit")
                for name in all_files:
                    parts = name.split("/")
                    if any("/".join(parts[:i]) in all_files for i in range(1, len(parts))):
                        raise ManagerError("ZIP 存在文件与目录路径冲突。", "unsafe_archive")
                candidates = {}
                for name, (entry, is_dir) in entries.items():
                    parts = name.split("/")
                    if is_dir or len(parts) not in (2, 3) or not parts[-1].casefold().endswith(".toc"):
                        continue
                    addon = parts[-2]
                    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$", addon) or not self._toc_matches(addon, parts[-1]):
                        continue
                    if entry.file_size > MAX_TOC_BYTES:
                        raise ManagerError("TOC 文件过大。", "archive_limit")
                    meta = _toc(archive.read(entry).decode("utf-8-sig", errors="replace"))
                    if TARGET_INTERFACE in meta["interfaces"]:
                        prefix = "/".join(parts[:-1])
                        candidates.setdefault(prefix, []).append(meta)
                direct = {p: metas for p, metas in candidates.items() if "/" not in p}
                if direct:
                    candidates = direct
                elif candidates:
                    wrappers = {p.split("/")[0] for p in candidates}
                    if len(wrappers) != 1:
                        raise ManagerError("ZIP 目录结构不明确，请选择标准插件包。", "invalid_layout")
                if not candidates:
                    raise ManagerError("未找到与插件目录同名且声明 Interface 16001 的 TOC，不能安装到无限服。", "wrong_interface")
                names = [p.split("/")[-1] for p in candidates]
                if len({s.casefold() for s in names}) != len(names):
                    raise ManagerError("ZIP 含重名插件目录。", "case_collision")
                packages = []
                for prefix, metas in sorted(candidates.items()):
                    meta = metas[0]
                    packages.append(dict(meta, name=prefix.split("/")[-1], prefix=prefix))
                # CRC checking also verifies that the declared uncompressed sizes are real.
                for name, (entry, is_dir) in entries.items():
                    if not is_dir:
                        with archive.open(entry) as handle:
                            magic = handle.read(4)
                            if magic[:2] == b"MZ" or magic == b"\x7fELF" or magic in (b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe"):
                                raise ManagerError("ZIP 包含原生可执行程序，已拒绝。", "executable_archive")
                            while handle.read(1024 * 1024):
                                pass
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError) as exc:
            raise ManagerError("无法安全读取 ZIP：%s" % exc, "invalid_archive") from exc
        return {"entries": entries, "packages": packages, "files": count, "expanded_bytes": total}

    def _copy_zip(self, source, destination):
        if any(part.casefold() == "wtf" for part in source.parts):
            raise ManagerError("不读取 WTF 个人设置目录中的来源文件。", "unsafe_source")
        if source.is_symlink() or not source.is_file() or source.suffix.casefold() != ".zip":
            raise ManagerError("请选择本地 ZIP 文件。", "invalid_archive")
        if source.stat().st_size > MAX_DOWNLOAD_BYTES:
            raise ManagerError("ZIP 超过 200 MiB 下载限制。", "archive_limit")
        with source.open("rb") as inp, destination.open("xb") as out:
            copied = 0
            for block in iter(lambda: inp.read(1024 * 1024), b""):
                copied += len(block)
                if copied > MAX_DOWNLOAD_BYTES:
                    raise ManagerError("ZIP 超过大小限制。", "archive_limit")
                out.write(block)

    def _pack_local(self, folder, destination):
        if folder.is_symlink() or not folder.is_dir():
            raise ManagerError("项目插件源码目录不存在。", "missing_source")
        count, size = 0, 0
        with zipfile.ZipFile(str(destination), "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for root, dirs, files in os.walk(str(folder), followlinks=False):
                for name in sorted(dirs + files):
                    item = Path(root) / name
                    if item.is_symlink():
                        raise ManagerError("项目插件不能包含符号链接。", "unsafe_archive")
                for name in sorted(files):
                    item = Path(root) / name
                    count += 1
                    size += item.stat().st_size
                    if count > MAX_FILES or size > MAX_UNPACKED_BYTES:
                        raise ManagerError("项目插件大小超过限制。", "archive_limit")
                    archive.write(str(item), str(item.relative_to(folder.parent)).replace(os.sep, "/"))

    def _download(self, url, destination):
        _safe_url(url)
        opener = urllib.request.build_opener(_SafeRedirect())
        request = urllib.request.Request(url, headers={"User-Agent": "ForeverLocalManager/" + VERSION})
        started = time.monotonic()
        try:
            with opener.open(request, timeout=20) as response, destination.open("xb") as handle:
                _safe_url(response.url, redirect=True)
                length = response.headers.get("Content-Length")
                if length and int(length) > MAX_DOWNLOAD_BYTES:
                    raise ManagerError("下载超过 200 MiB 限制。", "archive_limit")
                total = 0
                while True:
                    if time.monotonic() - started > 120:
                        raise ManagerError("下载超时，请重试。", "download_timeout")
                    block = response.read(1024 * 1024)
                    if not block:
                        break
                    total += len(block)
                    if total > MAX_DOWNLOAD_BYTES:
                        raise ManagerError("下载超过 200 MiB 限制。", "archive_limit")
                    handle.write(block)
        except (OSError, urllib.error.URLError, ValueError) as exc:
            if isinstance(exc, ManagerError):
                raise
            raise ManagerError("官方发行包下载失败：%s" % exc, "download_failed") from exc

    def _tree_digest(self, folder):
        if not folder.exists() and not folder.is_symlink():
            return None
        if folder.is_symlink() or not folder.is_dir():
            raise ManagerError("目标插件目录是链接或文件，不能覆盖。", "unsafe_target")
        digest = hashlib.sha256()
        count, size = 0, 0
        for root, dirs, files in os.walk(str(folder), followlinks=False):
            dirs.sort()
            files.sort()
            for name in dirs + files:
                if (Path(root) / name).is_symlink():
                    raise ManagerError("目标插件内含符号链接，不能覆盖。", "unsafe_target")
            for name in files:
                path = Path(root) / name
                if not path.is_file():
                    raise ManagerError("目标插件含特殊文件。", "unsafe_target")
                count += 1
                size += path.stat().st_size
                if count > MAX_FILES or size > MAX_UNPACKED_BYTES:
                    raise ManagerError("目标插件备份大小超过限制。", "archive_limit")
                digest.update(str(path.relative_to(folder)).replace(os.sep, "/").encode("utf-8"))
                digest.update(b"\0")
                digest.update(bytes.fromhex(_sha256(path)))
        return digest.hexdigest()

    def plan_install(self, source: dict):
        with self._lock:
            plan_id = uuid.uuid4().hex
            public = {"plan_id": plan_id, "addons": [], "version": "待确认", "declared_interfaces": [],
                      "warnings": [], "can_install": False, "requires_confirmation": True, "sha256": None}
            archive_path = None
            try:
                if not self.game_path:
                    raise ManagerError("请先设置无限服游戏目录。", "missing_game_path")
                branch, build = self._validate_game_path(str(self.game_path))
                if not isinstance(source, dict):
                    raise ManagerError("安装来源格式无效。")
                downloads = self._state_subdir("downloads")
                archive_path = downloads / (plan_id + ".zip")
                catalog_item = None
                source_url = None
                if source.get("type") == "catalog":
                    catalog_item = next((item for item in self._catalog if item["id"] == source.get("id")), None)
                    if not catalog_item:
                        raise ManagerError("找不到所选目录插件。", "unknown_catalog")
                    source_url = catalog_item["source_url"]
                    path = self.project_root / catalog_item["_local_path"]
                    if path.is_dir():
                        self._pack_local(path, archive_path)
                    elif path.is_file():
                        self._copy_zip(path, archive_path)
                    elif catalog_item.get("download_url"):
                        self._download(catalog_item["download_url"], archive_path)
                    else:
                        raise ManagerError("本地固定发行包不存在。请前往官方来源手动下载，再导入 ZIP。", "missing_source")
                elif source.get("type") == "zip":
                    value = source.get("path")
                    if not isinstance(value, str) or not Path(value).is_absolute():
                        raise ManagerError("ZIP 路径必须是绝对路径。", "invalid_archive")
                    self._copy_zip(Path(value), archive_path)
                    source_url = "local:zip/" + Path(value).name
                elif source.get("type") == "url":
                    source_url = _safe_url(source.get("url"))
                    self._download(source_url, archive_path)
                else:
                    raise ManagerError("仅支持项目目录、本地 ZIP 或官方 HTTPS 发行包。", "invalid_source")
                digest = _sha256(archive_path)
                if catalog_item and catalog_item.get("sha256") and digest != catalog_item["sha256"]:
                    raise ManagerError("固定发行包 SHA-256 与项目记录不一致，已拒绝。", "digest_mismatch")
                details = self._inspect_archive(archive_path)
                packages = details["packages"]
                names = [p["name"] for p in packages]
                if catalog_item and set(names) != set(catalog_item["addon_names"]):
                    raise ManagerError("发行包内的插件目录与项目记录不一致。", "unexpected_addons")
                addons_dir = branch / "Interface" / "AddOns"
                # Reject case aliases already present on a case-sensitive development volume.
                existing = {p.name.casefold(): p.name for p in addons_dir.iterdir()} if addons_dir.is_dir() else {}
                for name in names:
                    if name.casefold() in existing and existing[name.casefold()] != name:
                        raise ManagerError("现有插件目录大小写不同，请先人工核对。", "case_collision")
                baseline = {name: self._tree_digest(addons_dir / name) for name in names}
                current = {item["name"] for item in self.installed()}
                required = {dep for item in packages for dep in item["dependencies"]}
                missing = sorted(required - current - set(names))
                warnings = ["TOC 声明 16001 仅是静态证据；当前 build 的运行与战斗兼容性待游戏内验收。", "安装前须完全退出游戏；首次新增插件目录后需完整重开。"]
                if missing:
                    warnings.append("尚缺少必需依赖：" + "、".join(missing) + "；启用前需另行安装。")
                if any(value is not None for value in baseline.values()):
                    warnings.append("将替换已有插件目录；确认执行前会完整备份所替换目录。")
                versions = sorted({p["version"] for p in packages})
                public.update({"addons": names, "version": catalog_item["version"] if catalog_item else ", ".join(versions),
                               "declared_interfaces": sorted({v for p in packages for v in p["interfaces"]}),
                               "warnings": warnings, "can_install": True, "sha256": digest,
                               "target": str(branch), "target_addon_path": str(addons_dir), "client_build": build,
                               "source_url": source_url, "missing_dependencies": missing,
                               "files": details["files"], "expanded_bytes": details["expanded_bytes"],
                               "packages": [{k: p[k] for k in ("name", "title", "version", "interface")} for p in packages],
                               "changes": [{"name": n, "action": "replace" if baseline[n] is not None else "install"} for n in names]})
                self._plans[plan_id] = {"public": public, "archive": archive_path, "digest": digest,
                                       "branch": branch, "build": build, "baseline": baseline,
                                       "created": time.monotonic(), "catalog_id": catalog_item["id"] if catalog_item else None}
                return dict(public)
            except (ManagerError, OSError, ValueError) as exc:
                if archive_path and archive_path.exists():
                    archive_path.unlink()
                public["warnings"] = [str(exc)]
                public["error"] = str(exc)
                public["error_code"] = getattr(exc, "code", "planning_failed")
                return public

    @staticmethod
    def _replace(source, target):
        os.replace(str(source), str(target))

    def execute_install(self, plan_id, confirm: bool):
        if confirm is not True:
            raise ManagerError("请先查看安装计划，再明确确认安装。", "confirmation_required")
        with self._lock:
            plan = self._plans.get(plan_id)
            if not plan or time.monotonic() - plan["created"] > PLAN_LIFETIME:
                raise ManagerError("安装计划不存在或已过期，请重新检查发行包。", "stale_plan")
            if not self.game_path:
                raise ManagerError("游戏目录已改变，请重新规划。", "stale_plan")
            branch, build = self._validate_game_path(str(self.game_path))
            if branch != plan["branch"] or build != plan["build"]:
                raise ManagerError("游戏路径或 build 已改变，请重新规划。", "stale_plan")
            running = self._game_running()
            if running is not False:
                raise ManagerError("游戏正在运行，请完全退出后再安装。" if running else "无法确认游戏已退出，暂不写入。", "game_running" if running else "process_unknown")
            if _sha256(plan["archive"]) != plan["digest"]:
                raise ManagerError("计划中的 ZIP 已改变，请重新规划。", "digest_mismatch")
            details = self._inspect_archive(plan["archive"])
            names = plan["public"]["addons"]
            if names != [p["name"] for p in details["packages"]]:
                raise ManagerError("发行包目录已改变，请重新规划。", "stale_plan")
            addons_dir = branch / "Interface" / "AddOns"
            for name in names:
                if self._tree_digest(addons_dir / name) != plan["baseline"][name]:
                    raise ManagerError("已安装插件在规划后发生变化，请重新检查。", "stale_plan")
            lock_path = self.state_dir / "install.lock"
            try:
                lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError as exc:
                raise ManagerError("已有安装操作或遗留锁，请先确认另一个管理器已退出。", "install_locked") from exc
            os.close(lock_fd)
            stage = None
            moved_old, moved_new = [], []
            try:
                backup = self._state_subdir("backups") / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + plan_id)
            except Exception:
                lock_path.unlink()
                raise
            try:
                addons_dir.mkdir(parents=True, exist_ok=True)
                stage = Path(tempfile.mkdtemp(prefix=".forever-stage-", dir=str(addons_dir)))
                content, displaced = stage / "content", stage / "displaced"
                content.mkdir()
                displaced.mkdir()
                with zipfile.ZipFile(str(plan["archive"])) as archive:
                    for package in details["packages"]:
                        prefix = package["prefix"] + "/"
                        for member, (entry, is_dir) in details["entries"].items():
                            if is_dir or not member.startswith(prefix):
                                continue
                            relative = member[len(prefix):]
                            out = content / package["name"] / relative
                            out.parent.mkdir(parents=True, exist_ok=True)
                            with archive.open(entry) as inp, out.open("xb") as handle:
                                shutil.copyfileobj(inp, handle, 1024 * 1024)
                backup.mkdir(parents=True, exist_ok=False)
                for name in names:
                    if plan["baseline"][name] is not None:
                        shutil.copytree(str(addons_dir / name), str(backup / name))
                        if self._tree_digest(backup / name) != plan["baseline"][name]:
                            raise ManagerError("备份内容核对失败，安装已取消。", "backup_failed")
                _write_json(backup / "manifest.json", {"plan": plan["public"], "previous_digests": plan["baseline"], "created_at": datetime.now(timezone.utc).isoformat(), "wtf_accessed": False})
                # Recheck immediately before committing any live folder change.
                if self._game_running() is not False:
                    raise ManagerError("游戏状态已改变，安装已取消。", "game_running")
                fresh, fresh_build = self._validate_game_path(str(self.game_path))
                if fresh != branch or fresh_build != build:
                    raise ManagerError("游戏元数据已变化，请重新规划。", "stale_plan")
                for name in names:
                    if self._tree_digest(addons_dir / name) != plan["baseline"][name]:
                        raise ManagerError("目标插件已改变，安装已取消。", "stale_plan")
                for name in names:
                    target = addons_dir / name
                    if target.exists():
                        self._replace(target, displaced / name)
                        moved_old.append(name)
                    self._replace(content / name, target)
                    moved_new.append(name)
                receipts = _read_json(self._managed_path, {})
                receipts.setdefault(str(branch), {})
                for item in details["packages"]:
                    receipts[str(branch)][item["name"]] = {"version": item["version"], "sha256": plan["digest"],
                        "catalog_id": plan["catalog_id"], "source_url": plan["public"]["source_url"],
                        "client_build": build, "installed_at": datetime.now(timezone.utc).isoformat()}
                _write_json(self._managed_path, receipts)
                self._plans.pop(plan_id, None)
                return {"ok": True, "addons": names, "backup_path": str(backup), "sha256": plan["digest"],
                        "client_build": build, "restart_required": True, "in_game_tested": False,
                        "message": "安装完成，已备份被替换目录。请完整重开游戏并验收；未读取或修改 WTF。"}
            except Exception as exc:
                rollback_errors = []
                for name in reversed(moved_new):
                    try:
                        shutil.rmtree(str(addons_dir / name))
                    except OSError as error:
                        rollback_errors.append(str(error))
                for name in reversed(moved_old):
                    try:
                        self._replace(stage / "displaced" / name, addons_dir / name)
                    except OSError as error:
                        rollback_errors.append(str(error))
                self._plans.pop(plan_id, None)
                if rollback_errors:
                    # Preserve stage and backups when automated rollback is incomplete.
                    stage = None
                    raise ManagerError("安装失败且回退未完成。请保留备份并人工恢复：%s；%s" % (backup, "; ".join(rollback_errors)), "rollback_failed") from exc
                if isinstance(exc, ManagerError):
                    raise
                raise ManagerError("安装失败，已恢复原插件目录：%s" % exc, "install_failed") from exc
            finally:
                if stage:
                    shutil.rmtree(str(stage), ignore_errors=True)
                try:
                    lock_path.unlink()
                except OSError:
                    pass
