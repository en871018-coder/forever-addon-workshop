# Third-party sources and boundaries

The public repository contains an independently implemented Python local manager
and browser UI. It does not bundle game-client code, addon archives, addon
translations, Wago import strings, fonts, screenshots, game artwork or third-party
client implementations. Python standard-library modules are referenced, not
vendored.

Names, historical version metadata, digests and publisher links in
`local-manager/core.py` are catalog references. They are not licenses to copy
the referenced works. The catalog's companion-module paths are retained for
compatibility with private local workspaces, but their files are not distributed
here.

| Referenced project | Publisher/source | Catalog license note |
| --- | --- | --- |
| ForeverAuras | https://github.com/neroxrw/foreverauras | GPLv2; refer to that release's license |
| GearMenu | https://www.curseforge.com/wow/addons/gearmenu-rg | MIT; refer to publisher |
| MageProcs | https://www.curseforge.com/wow/addons/mageprocs | All Rights Reserved |
| BoF Guide | https://bindonforever.com/ | All Rights Reserved; free edition subject to publisher terms |
| Questie | https://www.curseforge.com/wow/addons/questie | GPLv3; refer to publisher |
| QuestieDB | https://www.curseforge.com/wow/addons/questiedb | GPLv3; refer to publisher |
| Dungeon Quest Atlas | https://www.curseforge.com/wow/addons/dungeon-quest-atlas | All Rights Reserved |

These are historical catalog labels, not a statement that every future release
has the same license or compatibility. Obtain current source and permissions
from the publisher.

Service references:

- CurseForge REST API: https://docs.curseforge.com/rest-api/
- CurseForge third-party API application information:
  https://support.curseforge.com/support/solutions/articles/9000208346-about-the-curseforge-api-and-how-to-apply-for-a-key
- Wago: https://wago.io/ ; individual works have their own permissions.
- OpenAI API: https://platform.openai.com/docs/

World of Warcraft, CurseForge, Wago and other product names belong to their
respective owners. This independent prototype is not endorsed by those services.
Publishing this repository does not imply approval of an API application.
