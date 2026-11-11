# Forever Addon Workshop · Forever 插件工坊

A personal local addon manager for **World of Warcraft: Forever**, with a browser interface and a Python 3.9+ service. Version **0.1.1** is a prototype.

[Project page](https://en871018-coder.github.io/forever-addon-workshop/) · [Repository](https://github.com/en871018-coder/forever-addon-workshop) · [Download source ZIP](https://github.com/en871018-coder/forever-addon-workshop/archive/refs/heads/main.zip) · [Report an issue](https://github.com/en871018-coder/forever-addon-workshop/issues)

[Usage guide](USAGE.md) · [Original-code rights](COPYING.md) · [Third-party notices](THIRD_PARTY_NOTICES.md)

## What it does

- **Inspect installed addons:** read TOC titles, versions and Interface declarations from `Interface/AddOns`; validate the `wowf` product branch and check Interface `16001`.
- **Review and install ZIPs:** show the selected version, folders, target, required dependencies and SHA-256 before asking for explicit confirmation. The game must be stopped before installation. Replaced folders are backed up; installation failure attempts to restore the previous folders.
- **Find releases:** use the official CurseForge API when an approved key is configured, or open the provider's website and import a manually downloaded ZIP.
- **Organize Aura text:** search public Wago Forever listings, open works on the provider's site, and save public import strings with their sources. The manager does not decode or execute Lua or automatically import Auras into the game.
- **Prepare translations:** export translation jobs, or explicitly request a translation dictionary through your own optional AI API connection. API usage can incur separate charges. A dictionary still needs adaptation to the relevant addon's language interface and in-game testing.

The manager does not read or modify `WTF`. It does not run inside the game, change game task eligibility, automatically update addons in the background or certify addon compatibility from metadata alone.

## Run locally

Requirements: **Python 3.9 or newer** and a browser. The manager uses Python's standard library; pip, Node and cloud deployment are not required.

1. Download and fully extract the source ZIP, preserving its directory structure.
2. On Windows, run `启动插件工坊-Windows.cmd`. Install Python with **Add Python to PATH** enabled, or use the Python launcher.
3. On macOS, run `启动插件工坊-Mac.command`. If the file has lost its executable permission, open a terminal in the extracted directory and run:

   ```sh
   python3 local-manager/server.py
   ```

4. The launcher opens the local browser interface. If necessary, copy the complete URL printed in the terminal, including the session fragment after `#`.
5. Select the real `_forever_` branch or its `Interface/AddOns` directory. Existing `wowf` product metadata is required; creating an empty folder with that name is insufficient.
6. Review the plan and close the game before confirming an installation. After adding a new addon folder, fully restart the game to discover it.

The service listens only on `127.0.0.1`. Its starting port is `17860`; version 0.1.1 selects a subsequent available port when the address is occupied. The terminal prints the address actually selected. To specify a starting port:

```sh
python3 local-manager/server.py --port 17861
```

On Windows, `py -3` can replace `python3`. Closing the terminal or pressing Ctrl+C stops that instance.

Installation backups and locally generated files are stored in `.manager-state` beside the application. That directory is private runtime data and is excluded from this public repository. Keep one manager responsible for each addon directory.

## Intended CurseForge API usage

This project is preparing the public project information needed for third-party API access. **No approval or live-key validation is claimed.** It is an independent application, not the official CurseForge client.

The integration is designed to use the [official CurseForge REST API](https://docs.curseforge.com/rest-api/) with an approved third-party `x-api-key`. No bundled or shared key is provided. Requests are made on demand for a user's selected action:

1. Search World of Warcraft addons in the Forever category.
2. Read project, release and file metadata to let the user review a version.
3. Download a selected file only when a permitted download URL is supplied and the author's distribution settings allow third-party distribution.

When distribution is disabled or a download URL is absent, the app directs the user to the author's official page. It does not infer restricted CDN links or bypass paid content, account access or distribution restrictions. Search responses are not persistently cached. A website account or author upload token is not a replacement for an approved third-party API key.

Without a configured key, website search links, metadata in the local catalogue and ZIP import remain available. Catalogue entries identify historical versions and sources; they do not mean that an upstream addon is bundled, current or verified on the user's complete client build.

## External services and local data

- **CurseForge:** optional approved API key; unavailable or restricted access falls back to the official website.
- **Wago:** public listings and public text access where available. Rejected or unavailable requests fall back to the work's webpage. Individual works retain their own permissions and compatibility requirements.
- **AI translation:** optional credentials and a model the user can access. Each translation is explicitly confirmed before its input text is sent. API billing is separate; output is a dictionary, with no automatic in-game integration.

Optional credentials are held in the current process's memory and are not written to application settings or included in this repository. They may be entered through the interface or supplied with `CURSEFORGE_API_KEY`, `OPENAI_API_KEY` and `OPENAI_MODEL`. Do not commit credentials or runtime files. Local backups, saved Aura text and translation jobs remain on the user's computer unless the user separately chooses to share them.

## Validation and limitations

macOS discovery and browser workflows have been checked locally. The test suite covers temporary-directory installation and rollback, archive safety, path and Interface checks, local HTTP access, provider handling and translation format preservation.

**Windows startup and in-game operation have not been verified on an actual Windows system. The CurseForge integration has not been validated with a live approved key. Paid AI translation has not been exercised in validation.** A TOC declaration or provider category does not prove runtime availability, permission to use a combat API or compatibility with a particular game build. Newly installed addons and translations need in-game acceptance testing.

The current install rule targets Forever Interface `16001`. Other branches and future Interface changes require separate verification. Existing addon data can be incomplete; the manager does not correct an upstream addon's game database or server-side restrictions.

## Public repository scope and rights

The public tree contains the manager, browser assets, tests, root launchers, catalogue metadata and third-party notices. It does **not** include upstream addon packages, BoF Chinese translation data, `WTF`, credentials, runtime state, personal settings, vendor caches or the private project's historical records.

**All rights reserved for original code.** Public visibility and source downloads do not mean that an open-source licence has been selected or that permission for reuse or redistribution has been granted. Third-party components, references, trademarks and data retain their own licences and notices. No MIT licence is asserted for the project.

See [COPYING.md](COPYING.md) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for the public repository's notices. The [usage guide](USAGE.md) describes setup and current limits.

This is an independent personal project. It is not affiliated with or endorsed by Blizzard Entertainment, CurseForge, Overwolf or Wago.

## 简体中文说明

Forever 插件工坊是游戏外的本地管理器，使用 Python 3.9+ 和浏览器运行。它可以读取插件版本、检查无限服声明、审阅 ZIP 安装计划并备份被替换的目录，也可以保存公开 WA 字符串、导出汉化任务。它不读取或修改 WTF，不执行 WA 自定义代码，也不会自动把翻译字典接入任意插件。

CurseForge 站内搜索需要自己获批的第三方 API 密钥，目前尚未完成真实获批密钥验收；没有密钥时提供官网入口和本地 ZIP 导入。作者禁止分发或没有下载地址时会回官网，不猜测受限下载链接，不绕过付费或访问限制。AI 翻译为可选功能，使用自己的 API 连接，确认后才发送输入文字，费用另计。

公开仓库仅包含管理器、测试、启动器、目录元数据和第三方声明；没有附带任何上游插件、BoF 中文数据、WTF、API 密钥、运行记录或历史私人档案。原始代码暂保留全部权利，尚未选择开源许可，第三方内容按各自许可处理。Windows 首次实机与游戏内效果仍待验收。
