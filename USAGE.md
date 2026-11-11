# Running Forever Addon Workshop 0.1.1

This public source snapshot contains the local manager, its web interface and
temporary-fixture tests. It does not contain any game addons or personal data.

## Start

1. Download the repository ZIP and extract it completely, or clone the repository.
2. Install Python 3.9 or newer from https://www.python.org/downloads/ . On Windows,
   enable the installer option to add Python to PATH.
3. Windows: double-click `启动插件工坊-Windows.cmd`. macOS: double-click
   `启动插件工坊-Mac.command`. Alternatively, from the extracted root run
   `python3 local-manager/server.py` (Windows: `py -3 local-manager/server.py`).
4. Open the full URL printed in the terminal, including its session fragment.
   Only loopback access is supported. Keep the terminal open while using the UI.
5. Select the real Forever installation in the connection settings. The branch
   must have valid `wowf` metadata; an empty folder named `_forever_` is insufficient.

The default port is 17860. If occupied, the server tries subsequent available
ports. Use `--port 17861` to select another starting port. Stop with Ctrl+C.

## Install an addon

Exit the game normally. Obtain the desired Forever addon ZIP from its publisher,
then use the local ZIP import action. The manager checks paths, dependencies,
declared Interface 16001 and size limits before producing a plan. Review the
target path, full build, affected folders and digest. Only the explicit install
confirmation writes into `Interface/AddOns`. Existing directories are backed up
under `.manager-state/backups/`. A failed group installation attempts rollback.

For manual recovery, keep a copy of the current directories, exit the game, and
restore the previous addon directories from the matching backup. Newly added
directories have no prior backup. The manager never restores or reads WTF.

The pinned catalog contains historical release metadata and links, not bundled
addons. Local companion modules from the private development workspace are also
not included; entries requiring those files will be unavailable. Source links
and license summaries do not grant redistribution rights. Use publisher sources.

## External services

- **CurseForge:** configure your own approved third-party API key for in-app
  search and file selection. Without a key, the UI offers official website links
  and ZIP import. No shared key is included. Author distribution restrictions and
  missing download URLs result in a publisher-page fallback. Approval, terms,
  quota and fees are determined by CurseForge.
- **Wago:** the manager can search Forever entries and save available public
  import text with its source URL. Requests may be denied or rate limited. The
  manager does not execute the string or import it into the game. A category
  match is not a guarantee that the installed ForeverAuras version supports it.
- **Translation:** export a JSON translation task, or explicitly request an
  OpenAI API translation using your own key and model. API charges may apply.
  A generated dictionary needs addon-specific locale integration before it can
  affect in-game text. This is not automatic translation inside WoW.

Keys remain in the running process and are not saved in the configuration.
Optional environment variables: `CURSEFORGE_API_KEY`, `OPENAI_API_KEY`,
`OPENAI_MODEL`. Stop the process to clear configured keys. Local backups, game
paths, saved WA text and translation jobs live in `.manager-state/`; do not
publish or share that directory.

## Verification and scope

Run `python3 -m unittest discover -s tests -p 'test_manager_*.py'` from the source
root. These tests use temporary fixture branches and mocked service responses;
they do not prove game compatibility or live API authorization.

The manager currently targets Forever with declared Interface 16001. A matching
TOC is necessary but does not prove runtime or combat compatibility. Windows
real-client operation, live CurseForge key access and paid AI calls have not
been validated. macOS local UI and startup behavior have been checked. Do not
run multiple updaters against the same addon directory.

## 中文简要说明

完整解压源码，安装 Python 3.9+，然后双击对应系统的启动器。浏览器界面由本机
Python 程序提供，仅监听 127.0.0.1。安装前先正常退出游戏，导入官方插件 ZIP，
核对计划后再确认安装。公开源码没有附带上游插件、中文路线词典或私人 WTF。

CurseForge 站内搜索需要自行获批的 API Key。WA 字符串只保存为文本；AI 返回的是
待适配字典，不能直接承诺在游戏内生效。Windows 实机及实际 API 权限仍待验收。
