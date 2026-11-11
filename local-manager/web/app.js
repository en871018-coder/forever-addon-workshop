'use strict';
const $ = (selector, parent = document) => parent.querySelector(selector);
const tokenFromLink = location.hash.slice(1);
if (tokenFromLink) {
  sessionStorage.setItem('forever-workshop-token', tokenFromLink);
  history.replaceState(null, '', location.pathname);
}
const token = sessionStorage.getItem('forever-workshop-token') || '';
const state = {page: 'installed', status: {}, installed: [], catalog: [], filter: '全部', query: ''};
const titles = {installed: '我的插件', discover: '发现插件', wa: 'WA 灵感库', translate: '汉化工坊'};
const main = $('#main');
const dialog = $('#action-dialog');
let viewId = 0;

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key.startsWith('on')) node.addEventListener(key.slice(2).toLowerCase(), value);
    else if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (value !== false && value != null) node.setAttribute(key, value === true ? '' : String(value));
  }
  for (const child of children.flat(Infinity)) {
    if (child != null && child !== false) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}
const button = (text, fn, kind = 'secondary', extra = {}) => el('button', {class: 'button ' + kind, type: 'button', onClick: fn, ...extra}, text);
const tag = (text, kind = '') => el('span', {class: 'tag ' + kind}, text);
function external(url, text, className = '') {
  try { if (new URL(url).protocol !== 'https:') return el('span', {class: 'muted'}, text); }
  catch { return el('span', {class: 'muted'}, text); }
  return el('a', {href: url, target: '_blank', rel: 'noopener noreferrer', class: className}, text);
}
async function api(path, body) {
  const control = new AbortController();
  const timeout = setTimeout(() => control.abort(), 180000);
  try {
    const response = await fetch(path, {method: body === undefined ? 'GET' : 'POST',
      headers: {'X-Workshop-Token': token, ...(body === undefined ? {} : {'Content-Type': 'application/json'})},
      body: body === undefined ? undefined : JSON.stringify(body), signal: control.signal});
    const result = await response.json();
    if (!response.ok || !result.ok) throw new Error(result.error || '操作未完成，请重试。');
    return result.data;
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('请求超时。请检查网络后重试；不要重复确认安装。');
    throw error;
  } finally { clearTimeout(timeout); }
}
function toast(message, error = false) {
  const item = el('div', {class: 'toast' + (error ? ' error' : '')}, message);
  $('#toasts').append(item); setTimeout(() => item.remove(), error ? 10000 : 6500);
}
function notice(message, error = false) { return el('div', {class: 'notice' + (error ? ' error' : '')}, message); }
function cleanPath(value) {
  const text = value.trim();
  return text.length > 1 && ((text.startsWith('"') && text.endsWith('"')) || (text.startsWith("'") && text.endsWith("'"))) ? text.slice(1, -1) : text;
}
function empty(title, description, ...actions) {
  return el('div', {class: 'empty'}, el('span', {class: 'empty-symbol'}, '✧'), el('h3', {}, title), el('p', {}, description), ...actions);
}
function avatar(name) {
  const colors = ['blue', 'green', 'purple', ''];
  const num = [...name].reduce((n, c) => n + c.charCodeAt(0), 0);
  return el('span', {class: 'addon-avatar ' + colors[num % 4]}, name.replace(/[^A-Za-z\u4e00-\u9fff]/g, '').slice(0, 2) || 'F');
}
function heading(title, description, ...actions) {
  return el('div', {class: 'page-heading'}, el('div', {}, el('div', {class: 'eyebrow'}, 'YOUR FOREVER, YOUR WAY'), el('h1', {}, title), el('p', {}, description)), el('div', {class: 'button-row'}, ...actions));
}
function hero(eyebrow, title, text) {
  return el('div', {class: 'hero'}, el('div', {}, el('div', {class: 'eyebrow'}, eyebrow), el('h2', {}, title), el('p', {}, text)), el('span', {class: 'hero-sigil', 'aria-hidden': 'true'}, 'F✧'));
}
function searchForm(placeholder, callback, initial = '') {
  const input = el('input', {placeholder, 'aria-label': placeholder, value: initial, maxlength: 200});
  const form = el('form', {class: 'search-row'}, el('div', {class: 'search-box'}, el('span', {class: 'search-icon'}, '⌕'), input), el('button', {class: 'button primary', type: 'submit'}, '搜索'));
  form.addEventListener('submit', event => {event.preventDefault(); callback(input.value.trim());});
  return {form, input};
}
async function loadLocal() {
  [state.status, state.installed, state.catalog] = await Promise.all([api('/api/status'), api('/api/installed'), api('/api/catalog')]);
  $('#nav-count').textContent = state.installed.length;
  $('#build-label').textContent = state.status.client_build || '客户端 build 待确认';
  $('#connection-dot').classList.toggle('connected', !!state.status.game_path);
}
async function refresh() {
  const refreshButton = $('#refresh'); refreshButton.disabled = true;
  try { await loadLocal(); render(); }
  catch (error) { main.replaceChildren(empty('暂时无法打开工坊', error.message, button('重试', refresh, 'primary'))); }
  finally { refreshButton.disabled = false; }
}
function navigate(page) {state.page = page; state.query = ''; state.filter = '全部'; render(); window.scrollTo(0, 0);}
function render() {
  viewId += 1;
  document.querySelectorAll('[data-page]').forEach(item => item.classList.toggle('active', item.dataset.page === state.page));
  $('#breadcrumb').textContent = titles[state.page];
  if (state.page === 'installed') renderInstalled();
  if (state.page === 'discover') renderDiscover();
  if (state.page === 'wa') renderWa();
  if (state.page === 'translate') renderTranslate();
}
function renderInstalled() {
  const s = state.status;
  const runningText = s.game_running === true ? '游戏运行中' : s.game_running === false ? '已就绪' : '进程状态待确认';
  const stats = el('div', {class: 'stats'},
    stat('本地插件目录', String(state.installed.length).padStart(2, '0'), '从 Interface/AddOns 读取', '▦'),
    stat('中文扩展', state.installed.filter(x => ['BoFGuide_zhCN', 'ForeverQuestTools'].includes(x.name)).length, '独立补丁，保留上游文件', '文'),
    stat('安装状态', runningText, s.game_running ? '退出游戏后再安装' : '每次安装前会再次检查', '◇'));
  const tableContainer = el('div');
  const search = searchForm('搜索已安装的插件…', q => updateTable(q));
  const updateTable = (q = '') => {
    const items = state.installed.filter(x => (x.name + ' ' + x.title).toLowerCase().includes(q.toLowerCase()));
    const table = el('div', {class: 'addon-table'}, el('div', {class: 'table-row table-head'}, el('span', {}, '插件'), el('span', {}, '本地版本'), el('span', {}, '兼容性证据'), el('span', {class: 'table-action'}, '来源')));
    for (const addon of items) {
      table.append(el('div', {class: 'table-row'}, el('div', {class: 'table-name'}, avatar(addon.name), el('div', {}, el('strong', {}, addon.title || addon.name), el('small', {}, addon.name))), el('div', {class: 'table-version'}, addon.version || '待确认'), el('div', {class: 'table-evidence'}, tag(addon.forever_declared ? '声明 Forever' : '待确认', addon.forever_declared ? 'green' : ''), el('div', {}, addon.managed ? '由工坊管理 · 实机待验收' : '本地扫描 · 实机待验收')), el('div', {class: 'table-action'}, external(addon.source_url, '查看 ↗'))));
    }
    tableContainer.replaceChildren(items.length ? table : empty('还没有找到插件', q ? '换个关键词试试。' : '先选择 _forever_ 游戏目录，或去发现插件安装。', button('选择游戏目录', openSettings, 'primary')));
  };
  updateTable();
  main.replaceChildren(heading('我的插件', '熟悉的插件，清楚的版本。每次安装都有记录。', button('＋ 导入 ZIP', openZip), button('发现插件 ↗', () => navigate('discover'), 'primary')),
    hero('A LITTLE LESS SETUP. A LITTLE MORE ADVENTURE.', '把时间留给下一场冒险。', '专为无限服整理。查看本地版本、使用中文补丁，安装前检查目录与依赖，覆盖前先备份。'), stats,
    ...(!s.game_path ? [notice('还没有选择无限服目录。在“连接与设置”中填写 _forever_ 路径即可开始。')] : []),
    el('div', {class: 'section-heading'}, el('h2', {}, '已安装'), el('span', {class: 'path-text'}, s.game_path || '未选择游戏目录')), search.form, tableContainer,
    notice('“声明 Forever”来自 TOC，不能替代当前完整 build 的实机验收。新增插件目录后，请完整退出并重开游戏。'));
  appendActivity(viewId);
}
function stat(label, value, subtitle, symbol) {return el('div', {class: 'stat'}, el('div', {}, el('span', {class: 'stat-label'}, label), el('strong', {}, value), el('small', {}, subtitle)), el('span', {class: 'stat-icon'}, symbol));}
function catalogCard(item) {
  const installed = item.addon_names.every(name => state.installed.some(x => x.name === name));
  return el('article', {class: 'addon-card'}, el('div', {class: 'card-heading'}, avatar(item.name), el('div', {class: 'card-name'}, el('h3', {}, item.title), el('small', {}, item.category || '插件'))),
    el('p', {class: 'card-summary'}, item.description), el('div', {class: 'tag-row'}, tag('Forever 发行包', 'gold'), ...(item.translation ? [tag('中文扩展', 'green')] : []), ...(installed ? [tag('已安装', 'green')] : [])),
    el('div', {class: 'card-foot'}, el('div', {}, el('div', {class: 'version'}, 'v' + item.version), external(item.source_url, '来源 ↗')), button(installed ? '检查重装' : '检查并安装', () => planInstall({type: 'catalog', id: item.id}), 'small')));
}
function renderDiscover() {
  let searchId = 0;
  const result = el('div');
  const grid = el('div', {class: 'card-grid'});
  const categories = ['全部', '任务', '法术', '装备', '中文'];
  const filters = el('div', {class: 'filters'});
  function update() {
    grid.replaceChildren(...state.catalog.filter(x => (x.name + x.description + x.category).toLowerCase().includes(state.query.toLowerCase()) &&
      (state.filter === '全部' || (x.category || '').includes(state.filter) || (state.filter === '中文' && x.translation))).map(catalogCard));
    if (!grid.children.length) grid.append(empty('本地推荐库没有匹配项', '可以搜索 CurseForge，或下载 ZIP 后导入。'));
    filters.replaceChildren(...categories.map(category => button(category, () => {state.filter = category; update();}, '', {class: 'filter' + (state.filter === category ? ' active' : '')})));
  }
  async function search(q) {
    const currentSearch = ++searchId;
    state.query = q; update();
    if (!q) {result.replaceChildren(); return;}
    result.replaceChildren(el('div', {class: 'loading'}, '正在搜索 CurseForge…'));
    const requestView = viewId;
    try {
      const data = await api('/api/search?q=' + encodeURIComponent(q));
      if (requestView !== viewId || currentSearch !== searchId) return;
      result.replaceChildren(el('div', {class: 'section-heading'}, el('h2', {}, 'CurseForge 搜索'), external(data.website_url, '去官网查看 ↗')), notice(data.message));
      if (data.items.length) result.append(el('div', {class: 'card-grid'}, ...data.items.map(item => el('article', {class: 'addon-card'}, el('div', {class: 'card-heading'}, avatar(item.name), el('div', {class: 'card-name'}, el('h3', {}, item.title), el('small', {}, 'CurseForge'))), el('p', {class: 'card-summary'}, item.summary), el('div', {class: 'tag-row'}, tag('完整 build 待验收')), el('div', {class: 'card-foot'}, external(item.source_url, '来源 ↗'), button(item.distribution_allowed ? '选择版本' : '仅官网提供', () => selectCfVersion(item), 'small', {disabled: !item.distribution_allowed}))))));
      else if (data.mode === 'api') result.append(empty('没有匹配的在线结果', '试试英文插件名，或在官网调整筛选条件。'));
    } catch (error) {if (requestView === viewId && currentSearch === searchId) result.replaceChildren(notice(error.message, true));}
  }
  const form = searchForm('搜索插件，例如 Questie、背包、法师…', search, state.query);
  main.replaceChildren(heading('发现插件', '从已整理的无限服版本开始，也可以搜索 CurseForge。', button('＋ 导入 ZIP', openZip)),
    hero('CURATED FOR FOREVER', '适合你的世界，才值得装进来。', '这里保留了项目已核对的固定版本与来源。中文补丁单独维护，避免更新覆盖；其他经典服版本会先检查，再安装。'), form.form, result,
    el('div', {class: 'section-heading'}, el('h2', {}, '项目推荐库'), el('span', {}, '9 个组件 · 历史固定版本')), filters, grid);
  update();
}
async function selectCfVersion(item) {
  modal(el('div', {class: 'loading'}, '正在读取 Forever 文件列表…'));
  try {
    const files = await api('/api/cf/files?mod_id=' + item.id);
    const list = el('div', {class: 'file-option-list'});
    for (const file of files) list.append(el('div', {class: 'file-option'}, el('div', {}, el('strong', {}, file.name), el('small', {}, file.game_versions.join(' · ')), el('small', {}, file.evidence)), button('检查', () => planInstall({type: 'curseforge', mod_id: item.id, file_id: file.id}), 'small', {disabled: !file.download_available})));
    modal(modalHeading('选择发行版本', item.name), notice('只列出当前页最多 50 个 Forever 分类文件。空下载地址或作者禁止第三方分发的文件，请到官网取得。'), files.length ? list : empty('没有可列出的文件', '请打开官网核对作者提供的无限服版本。', external(item.source_url, '打开官网 ↗', 'button')), modalActions(button('关闭', () => dialog.close())));
  } catch (error) {modal(modalHeading('暂时无法读取', item.name), notice(error.message, true), modalActions(button('关闭', () => dialog.close())));}
}
function modal(...content) {$('#action-content').replaceChildren(...content); if (!dialog.open) dialog.showModal();}
function modalHeading(title, label = 'FOREVER WORKSHOP') {return el('div', {class: 'dialog-heading'}, el('div', {}, el('div', {class: 'eyebrow'}, label), el('h2', {}, title)), button('×', () => dialog.close(), '', {class: 'icon-button', 'aria-label': '关闭'}));}
const modalActions = (...buttons) => el('div', {class: 'dialog-actions'}, ...buttons);
function openZip() {
  const path = el('input', {placeholder: '粘贴下载好的 .zip 文件完整路径', 'aria-label': 'ZIP 文件路径', autocomplete: 'off'});
  const form = el('form', {}, modalHeading('导入本地插件包'), el('p', {class: 'muted'}, '支持插件 ZIP。将先检查目录、Forever 声明和依赖，确认后再备份安装。'), el('label', {}, 'ZIP 文件完整路径', path), el('p', {class: 'field-note'}, '例如 Windows：D:\Downloads\Questie.zip；Mac：/Users/你的用户名/Downloads/Questie.zip。'), modalActions(button('取消', () => dialog.close()), el('button', {class: 'button primary', type: 'submit'}, '检查安装包')));
  form.addEventListener('submit', event => {event.preventDefault(); planInstall({type: 'zip', path: cleanPath(path.value)});});
  modal(form); path.focus();
}
async function planInstall(source) {
  modal(el('div', {class: 'loading'}, '正在检查安装包与目标目录…'));
  try {
    const plan = await api('/api/install/plan', source);
    const confirm = button('备份并安装', async () => {
      confirm.disabled = true; confirm.textContent = '正在备份并安装…';
      try {
        const result = await api('/api/install/confirm', {plan_id: plan.plan_id, confirm: true});
        modal(modalHeading('安装完成'), notice(result.message || '原目录已备份。请完整重开游戏，检查插件列表与实际功能。'), result.backup_path ? el('p', {class: 'path-text'}, '备份：' + result.backup_path) : null, modalActions(button('完成', () => dialog.close(), 'primary')));
        await loadLocal(); render(); toast('插件已安装，原目录已备份。');
      } catch (error) {toast(error.message, true); confirm.disabled = false; confirm.textContent = '重试安装';}
    }, 'primary', {disabled: !plan.can_install});
    const packageVersions = new Map((plan.packages || []).map(item => [item.name, item.version]));
    const changes = new Map((plan.changes || []).map(item => [item.name, item.action]));
    modal(modalHeading(plan.can_install ? '确认这次安装' : '安装检查未通过'), el('p', {class: 'muted'}, '确认后将备份并替换以下插件目录。不会读取或修改 WTF。'),
      el('p', {class: 'hash'}, '版本：' + plan.version + ' · build：' + (plan.client_build || '待确认')),
      el('p', {class: 'path-text'}, '安装至：' + (plan.target_addon_path || state.status.game_path || '未选择')),
      el('div', {class: 'plan-list'}, ...(plan.addons || []).map(name => el('div', {}, '▸ ' + name + ' · ' + (packageVersions.get(name) || plan.version) + ' · ' + (changes.get(name) === 'replace' ? '替换并备份' : '新增')))),
      notice((plan.warnings || ['安装后需要完整重开游戏，功能仍待实机验收。']).join('\n'), !plan.can_install),
      el('div', {class: 'hash'}, 'SHA256 · ' + (plan.sha256 || '未生成')), modalActions(button('取消', () => dialog.close()), confirm));
  } catch (error) {modal(modalHeading('安装检查未通过'), notice(error.message, true), modalActions(button('关闭', () => dialog.close())));}
}
function renderWa() {
  let searchId = 0;
  const results = el('div');
  const library = el('div');
  const search = searchForm('搜索 ForeverAuras，例如 mage、hot streak…', runSearch);
  async function runSearch(query) {
    const currentSearch = ++searchId;
    if (!query) return;
    search.input.value = query;
    results.replaceChildren(el('div', {class: 'loading'}, '正在搜索 Wago Forever 分类…'));
    const requestView = viewId;
    try {
      const data = await api('/api/wa/search?q=' + encodeURIComponent(query));
      if (requestView !== viewId || currentSearch !== searchId) return;
      results.replaceChildren(notice(data.message), el('div', {class: 'section-heading'}, el('h2', {}, '搜索结果'), external(data.website_url, '在 Wago 官网搜索 ↗')));
      if (data.items.length) results.append(el('div', {class: 'card-grid'}, ...data.items.map(item => el('article', {class: 'addon-card'}, el('div', {class: 'card-heading'}, avatar(item.title), el('div', {class: 'card-name'}, el('h3', {}, item.title), el('small', {}, item.author || 'Wago'))), el('p', {class: 'card-summary'}, item.summary), el('div', {class: 'tag-row'}, tag('Forever 分类'), tag('实机待验收')), el('div', {class: 'card-foot'}, el('span', {class: 'version'}, item.version), el('div', {class: 'button-row'}, external(item.source_url, '查看 ↗'), button('取字符串', () => fetchWa(item), 'small')))))));
      else results.append(empty(data.mode === 'api' ? '没有找到匹配项' : '可在官网继续查看', '可尝试英文技能名。在作品页复制导入字符串后，粘贴到下方保存。', external(data.website_url, '打开 Wago ↗', 'button')));
    } catch (error) {if (requestView === viewId && currentSearch === searchId) results.replaceChildren(notice(error.message, true));}
  }
  const name = el('input', {placeholder: '例如：法师火法连击提示', required: true, maxlength: 100});
  const source = el('input', {placeholder: 'https://wago.io/…', type: 'url'});
  const value = el('textarea', {placeholder: '粘贴 !WA:… 或 ForeverAuras 导入字符串', required: true, maxlength: 2000000});
  const save = el('button', {class: 'button primary', type: 'submit'}, '保存到本地库');
  const form = el('form', {class: 'panel'}, el('h3', {}, '已经有字符串？'), el('p', {}, '把作品来源和导入字符串放在一起，换电脑也方便找到。'), el('label', {}, '名称', name), el('label', {}, '来源链接（可选）', source), el('label', {}, '导入字符串', value), el('div', {class: 'button-row'}, save));
  form.addEventListener('submit', async event => {
    event.preventDefault(); save.disabled = true;
    try {const record = await api('/api/wa/save', {name: name.value, source_url: source.value.trim(), string: value.value}); toast(record.warnings.join(' ')); form.reset(); await loadLibrary();}
    catch (error) {toast(error.message, true);} finally {save.disabled = false;}
  });
  const steps = el('div', {class: 'panel'}, el('h3', {}, '从作品，到你的角色'), el('div', {class: 'tip-list'},
    tip('1', '先看 Forever 分类与作品说明', '旧 WA 能否使用，取决于触发逻辑与当前客户端接口。'),
    tip('2', '保存字符串与来源', '只保存文本，不解码执行，也不会改写游戏配置。'),
    tip('3', '在游戏中打开 /fa 手动导入', '查看自定义代码与预览效果，实战后再判断是否适用。')), notice('你已经用 MageProcs 显示法师触发。导入同类 Aura 前先停用重复提示，以免两个模块同时显示。'), el('div', {class: 'section-heading'}, el('h2', {}, '你的本地库')), library);
  async function loadLibrary() {
    try {const entries = await api('/api/wa/library'); library.replaceChildren(...(entries.length ? entries.map(item => el('div', {class: 'library-item'}, el('div', {}, el('strong', {}, item.name), el('small', {}, '兼容待确认 · 仅保存文本')), button('下载字符串', () => download(item.download_url), 'small'))) : [el('p', {class: 'muted'}, '还没有保存的字符串。')]));}
    catch (error) {library.replaceChildren(notice(error.message, true));}
  }
  main.replaceChildren(heading('WA 灵感库', '搜索、收藏，再把合适的效果带进游戏。'), hero('AURAS, WITH A LITTLE INSPIRATION.', '好效果，也要适合无限服。', '尝试搜索 Wago 的 Forever 分类。受网络或网站限制时，可直接去官网查看和复制；作品的兼容性会保留待验收状态。'), search.form,
    el('div', {class: 'chips'}, ...['mage', 'hot streak', 'missile barrage', 'engineering'].map(q => button(q, () => runSearch(q)))), results, el('div', {class: 'section-heading'}, el('h2', {}, '整理你的字符串')), el('div', {class: 'two-column'}, form, steps));
  loadLibrary();
}
async function fetchWa(item) {
  modal(el('div', {class: 'loading'}, '正在取得公开字符串…'));
  try {
    const record = await api('/api/wa/fetch', {id: item.id, name: item.title});
    modal(modalHeading('已保存到本地库', item.title), notice(record.warnings.join(' ')), el('p', {class: 'path-text'}, record.path), modalActions(button('完成', () => {dialog.close(); render();}, 'primary')));
  } catch (error) {
    modal(modalHeading('请在作品页继续', item.title), notice(error.message), modalActions(button('关闭', () => dialog.close()), external(item.source_url, '打开 Wago 作品页 ↗', 'button primary')));
  }
}
function tip(number, title, text) {return el('div', {class: 'tip'}, el('span', {class: 'step'}, number), el('div', {}, el('strong', {}, title), el('p', {}, text)));}
function renderTranslate() {
  const patches = state.catalog.filter(x => x.translation);
  const name = el('input', {placeholder: '插件目录名，例如 MyAddon', required: true, maxlength: 100});
  const strings = el('textarea', {class: 'large', placeholder: '{\n  "Next step": "Next step",\n  "Buy %d waters": "Buy %d waters"\n}', required: true});
  const result = el('div', {class: 'result-box'});
  const exportButton = button('导出汉化任务', () => submit(false));
  const aiButton = button('用 AI 翻译', () => submit(true), 'primary', {disabled: !state.status.ai_configured});
  function input() {let parsed; try {parsed = JSON.parse(strings.value);} catch {throw new Error('原文需要有效 JSON，请参考输入框中的格式。');} return {addon_name: name.value.trim(), strings: parsed};}
  async function submit(ai) {
    try {
      const data = input();
      if (!data.addon_name) throw new Error('请先填写插件目录名。');
      if (ai) {
        const proceed = button('发送文字并翻译', () => {dialog.close(); execute(true, data);}, 'primary');
        modal(modalHeading('确认使用 AI 翻译'), el('p', {class: 'muted'}, '将把下面这批原文发送到 OpenAI API。使用你的 API 密钥与模型，可能产生费用。不会发送 WTF、账号资料或其他文件。'), el('p', {class: 'hash'}, '模型：' + state.status.ai_model + ' · 字符串：' + Object.keys(data.strings || {}).length + ' 项'), modalActions(button('取消', () => dialog.close()), proceed));
      } else execute(false, data);
    } catch (error) {toast(error.message, true);}
  }
  async function execute(ai, data) {
    exportButton.disabled = true; aiButton.disabled = true;
    result.replaceChildren(el('div', {class: 'loading'}, ai ? '正在翻译并检查格式符…' : '正在导出任务…'));
    try {
      const output = await api(ai ? '/api/translate' : '/api/translation/request', data);
      result.replaceChildren(notice(output.message || output.warnings.join(' ')), ...(output.translations ? [el('pre', {}, JSON.stringify(output.translations, null, 2))] : [el('p', {class: 'path-text'}, output.path)]), button(ai ? '下载翻译字典' : '下载任务文件', () => download(output.download_url), 'primary'));
      toast(ai ? '翻译字典已保存，格式检查通过。' : '汉化任务已保存，可交给当前 Codex 继续。');
    } catch (error) {result.replaceChildren(notice(error.message, true));}
    finally {exportButton.disabled = false; aiButton.disabled = !state.status.ai_configured;}
  }
  main.replaceChildren(heading('汉化工坊', '把看得懂的说明，带进下一步任务。', button('配置 AI 连接', openSettings)),
    hero('LESS GUESSWORK. MORE 中文.', '熟悉的文字，熟悉的节奏。', '已经完成的补丁可以直接检查安装。新插件先翻译显示文本，再接入各自的语言接口；保留英文回退，避免改坏上游逻辑。'),
    el('div', {class: 'section-heading'}, el('h2', {}, '现有中文扩展'), el('span', {}, '独立维护 · 实机效果待验收')), el('div', {class: 'card-grid'}, ...patches.map(catalogCard)),
    el('div', {class: 'section-heading'}, el('h2', {}, '开始一份新的汉化')), el('div', {class: 'two-column'},
      el('div', {class: 'panel'}, el('h3', {}, '提交需要翻译的文字'), el('p', {}, '输入“键：原文”的 JSON 字典，支持每批最多 200 项。不要粘贴账号资料或私密内容。'), el('label', {}, '插件目录名', name), el('label', {}, '原文字符串', strings), el('div', {class: 'button-row'}, exportButton, aiButton), result),
      el('div', {class: 'panel'}, el('h3', {}, '从翻译，到真正生效'), el('div', {class: 'tip-list'}, tip('1', '拿到文本', '导出任务可交给当前 Codex；配置 API 后也可在此生成中文。'), tip('2', '检查格式与接入方式', '保留格式符、颜色标记与文本键；按插件语言接口制作补丁。'), tip('3', '在目标客户端验收', '核对 build、中文显示和实际任务推进，升级后重新检查。')), notice(state.status.ai_configured ? 'AI API 已连接配置。只有你点击并确认翻译时才会发送文字。' : '未配置 AI API。你仍可导出任务，在当前 Codex 对话继续处理；应用不会自动读取或登录聊天账号。'), el('p', {}, '第一版生成翻译字典，不会把任意 Lua 文件机械替换成中文。上游许可、语言接口和实机排版需要分别检查。'))));
}
async function download(path) {
  try {
    const response = await fetch(path, {headers: {'X-Workshop-Token': token}});
    if (!response.ok) {const error = await response.json(); throw new Error(error.error || '下载失败。');}
    const blob = await response.blob(); const url = URL.createObjectURL(blob);
    const anchor = el('a', {href: url, download: decodeURIComponent(path.split('/').pop())});
    document.body.append(anchor); anchor.click(); anchor.remove(); setTimeout(() => URL.revokeObjectURL(url), 2000);
  } catch (error) {toast(error.message, true);}
}
async function appendActivity(id) {
  try {
    const entries = await api('/api/activity');
    if (id !== viewId || !entries.length) return;
    main.append(el('div', {class: 'activity'}, el('div', {class: 'section-heading'}, el('h2', {}, '本次操作')), ...entries.slice(0, 4).map(item => el('div', {class: 'activity-row'}, el('time', {}, new Date(item.time).toLocaleTimeString('zh-CN', {hour: '2-digit', minute: '2-digit'})), el('span', {}, item.message)))));
  } catch { /* Activity is supplementary; the main action already reports errors. */ }
}
function openSettings() {
  $('#game-path').value = state.status.game_path || '';
  $('#cf-key').value = ''; $('#ai-key').value = ''; $('#ai-model').value = state.status.ai_model || '';
  $('#settings-status').textContent = 'CurseForge：' + (state.status.curseforge_configured ? '已配置' : '未配置') + ' · AI：' + (state.status.ai_configured ? '已配置' : '未配置');
  $('#settings-dialog').showModal();
}
$('#settings-form').addEventListener('submit', async event => {
  event.preventDefault(); const submit = $('#settings-form button[type=submit]'); submit.disabled = true;
  const data = {ai_model: $('#ai-model').value.trim()};
  const gamePath = cleanPath($('#game-path').value); if (gamePath) data.game_path = gamePath;
  if ($('#cf-key').value) data.curseforge_api_key = $('#cf-key').value;
  if ($('#ai-key').value) data.openai_api_key = $('#ai-key').value;
  try {await api('/api/settings', data); $('#cf-key').value = ''; $('#ai-key').value = ''; $('#settings-dialog').close(); await refresh(); toast('设置已保存。密钥仅用于当前运行，不写入文件。');}
  catch (error) {toast(error.message, true);} finally {submit.disabled = false;}
});
document.querySelectorAll('.close-dialog').forEach(item => item.addEventListener('click', () => $('#settings-dialog').close()));
document.querySelectorAll('[data-page]').forEach(item => item.addEventListener('click', () => navigate(item.dataset.page)));
$('#open-settings').addEventListener('click', openSettings);
$('#refresh').addEventListener('click', refresh);
$('#brand').addEventListener('click', event => {event.preventDefault(); navigate('installed');});
refresh();
