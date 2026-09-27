/* 配置草稿只保存在当前页面；敏感字段不回显。 */
const bridge = window.AstrBotPluginPage;
const $ = id => document.getElementById(id);
const node = (tag, text = '', cls = '') => {
  const item = document.createElement(tag);
  item.textContent = text;
  item.className = cls;
  return item;
};
const equal = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const sectionSwitches = new Set([
  'message.text_metadata.render_to_image',
  'message.text_metadata.paginate_images',
  'message.text_metadata.separate_text_sections',
  'message.opening.enable',
  'translation.enable',
  'permissions.whitelist.enable',
  'permissions.blacklist.enable',
  'bilibili_enhanced.use_cookie',
  'media_relay.enable',
  'bilibili_enhanced.admin_assist.enable',
]);
const views = {
  platforms: ['平台管理', []],
  message: ['消息与排版', ['message']],
  trigger: ['触发与限流', ['trigger', 'parse_rate_limit']],
  translation: ['翻译设置', ['translation']],
  download: ['下载与中转', ['download', 'media_relay']],
  proxy: ['代理设置', ['proxy']],
  permissions: ['权限管理', ['permissions']],
  admin: ['管理设置', ['admin']],
  all: ['全部设置', []],
};
let snapshot, draft, changes = {}, view = 'platforms', platform = 'bilibili';
let bindings = [], visibleFields = [], busy = false;
const invalid = new Set();
const status = (message, error = false) => {
  $('status').textContent = message;
  $('status').classList.toggle('error', error);
};
const platforms = () => snapshot.fields.filter(f => f.path.startsWith('parsers.'));
const label = path => snapshot.fields.find(f => f.path === path)?.title || path;
const value = path => draft[path];
function adopt(data) {
  snapshot = data;
  draft = structuredClone(data.values);
  changes = {};
  invalid.clear();
  $('preview-platform').replaceChildren();
  for (const field of platforms()) {
    const option = node('option', field.title);
    option.value = field.path.slice(8);
    $('preview-platform').append(option);
  }
  $('preview-platform').value = platform;
  render();
}
function edit(field, next, clearSecret = false) {
  draft[field.path] = next;
  if (field.secret ? (next === '' && !clearSecret) : equal(next, snapshot.values[field.path])) {
    delete changes[field.path];
  } else changes[field.path] = next;
  sync();
}
function selected(field) {
  const path = field.path;
  const query = $('search').value.trim().toLowerCase();
  if (query) return `${field.title} ${field.hint} ${path} ${snapshot.groups[path.split('.')[0]]}`.toLowerCase().includes(query);
  if (view === 'all') return true;
  if (view !== 'platforms') return views[view][1].includes(path.split('.')[0]);
  const supportsProxy = snapshot.fields.some(f => f.path === `proxy.${platform}` || f.path.startsWith(`proxy.${platform}.`) || (platform === 'xiaoheihe' && f.path === 'proxy.xiaoheihe_video'));
  return path === `parsers.${platform}` || path === 'message.hot_comments.count'
    || path === `message.hot_comments.${platform}` || (supportsProxy && path === 'proxy.address')
    || path === `proxy.${platform}` || path.startsWith(`proxy.${platform}.`)
    || (platform === 'xiaoheihe' && path === 'proxy.xiaoheihe_video')
    || path.startsWith(`${platform === 'bilibili' ? 'bilibili_enhanced' : platform}.`);
}
function availability(field) {
  for (const condition of field.conditions) {
    if (!equal(value(condition.path), condition.value)) return `需先设置「${label(condition.path)}」为${condition.value === true ? '开启' : condition.value === false ? '关闭' : condition.value}`;
  }
  if (field.path.startsWith('translation.') && field.path !== 'translation.enable' && !value('translation.enable')) return '请先启用翻译';
  if (field.path.startsWith('proxy.') && field.path !== 'proxy.address') {
    const hasProxy = Object.hasOwn(changes, 'proxy.address') ? !!value('proxy.address') : snapshot.configured['proxy.address'];
    if (!hasProxy) return '请先配置代理地址';
  }
  if (field.path.startsWith('message.hot_comments.') && field.path !== 'message.hot_comments.count' && !value('message.hot_comments.count')) return '热评数量大于 0 时生效';
  return '';
}
function control(field) {
  const wrap = node('div', '', `field${field.type === 'list' || field.secret ? ' wide' : ''}`);
  const title = node('label', field.title);
  const id = `field-${field.path}`;
  title.htmlFor = id;
  let input;
  const provider = field.path === 'translation.llm.astrbot_provider.provider_id';
  if (field.options.length || provider) {
    input = node('select');
    const options = provider ? [...new Set(['', ...snapshot.providers, value(field.path)])] : field.options;
    for (const choice of options) {
      const option = node('option', choice || '跟随当前会话');
      option.value = choice;
      input.append(option);
    }
    input.value = value(field.path);
  } else if (field.type === 'list' || field.type === 'text') {
    input = node('textarea');
    input.rows = 3;
    input.value = field.type === 'list' ? value(field.path).join('\n') : value(field.path);
  } else {
    input = node('input');
    input.type = field.type === 'bool' ? 'checkbox' : field.secret ? 'password' : ['int', 'float'].includes(field.type) ? 'number' : 'text';
    if (field.type === 'bool') { input.checked = value(field.path); title.className = 'toggle'; }
    else input.value = value(field.path);
    if (field.bounds) {
      input.min = field.bounds[0];
      if (field.bounds[1] !== null) input.max = field.bounds[1];
      input.step = field.type === 'int' ? '1' : 'any';
    }
    if (field.secret) { input.autocomplete = 'off'; input.placeholder = snapshot.configured[field.path] ? '已配置；留空保留原值' : '尚未配置'; }
  }
  input.id = id;
  input.addEventListener('input', () => {
    const number = ['int', 'float'].includes(field.type);
    if (number && (!input.value.trim() || !input.checkValidity())) {
      invalid.add(field.path); sync(); return;
    }
    invalid.delete(field.path);
    edit(field, field.type === 'bool' ? input.checked : number ? Number(input.value) : field.type === 'list' ? input.value.split('\n').map(s => s.trim()).filter(Boolean) : input.value);
  });
  if (field.type === 'bool') {
    wrap.classList.add('check-field');
    if (sectionSwitches.has(field.path)) {
      wrap.classList.add('section-switch');
      title.textContent = '';
      title.append(node('span', field.title), input, node('span', '', 'switch-track'));
    } else title.prepend(input);
    wrap.append(title);
  } else wrap.append(title, input);
  if (field.hint) wrap.append(node('p', field.hint, 'hint'));
  if (field.type === 'list') wrap.append(node('p', '每行一项', 'hint'));
  if (field.secret) {
    const clear = node('button', '清空已保存值');
    clear.type = 'button';
    clear.onclick = () => { input.value = ''; edit(field, '', true); };
    wrap.append(clear);
  }
  const reason = node('p', '', 'reason');
  wrap.append(reason);
  bindings.push({ field, wrap, input, reason });
  return wrap;
}
function render() {
  bindings = [];
  $('navigation').replaceChildren();
  for (const [key, [title]] of Object.entries(views)) {
    const button = node('button', title, view === key ? 'active' : '');
    button.onclick = () => { view = key; $('search').value = ''; render(); };
    $('navigation').append(button);
  }
  const searching = !!$('search').value.trim();
  $('section-title').textContent = searching ? '搜索结果' : views[view][0];
  $('section-kicker').textContent = searching ? '查找配置' : '配置工作区';
  $('editor').replaceChildren();
  if (view === 'platforms' && !searching) {
    const picker = node('div', '', 'platform-picker');
    for (const field of platforms()) {
      const key = field.path.slice(8);
      const button = node('button', `${field.title} · ${value(field.path) === '关闭' ? '关' : '开'}`, key === platform ? 'active' : '');
      button.onclick = () => { platform = key; $('preview-platform').value = key; render(); };
      picker.append(button);
    }
    $('editor').append(picker);
    $('editor').append(node('p', '输出模式按平台独立保存。热评数量和代理地址为全局设置；Cookie 仅用于对应平台。', 'note'));
  }
  visibleFields = snapshot.fields.filter(selected);
  const groups = new Map();
  for (const field of visibleFields) {
    const key = field.path.split('.').slice(0, -1).join('.');
    if (!groups.has(key)) {
      const panel = node('section', '', 'panel');
      panel.append(node('h3', snapshot.group_titles[key] || snapshot.groups[key.split('.')[0]], 'group-title'));
      const grid = node('div', '', key === 'message.hot_comments' ? 'field-grid check-list' : 'field-grid');
      panel.append(grid); groups.set(key, grid); $('editor').append(panel);
    }
    groups.get(key).append(control(field));
  }
  if (!visibleFields.length) $('editor').append(node('p', '没有匹配的设置', 'empty'));
  sync();
}
function sync() {
  const dirty = Object.keys(changes).length;
  for (const { field, wrap, input, reason } of bindings) {
    const why = availability(field);
    input.disabled = busy || !!why;
    wrap.classList.toggle('disabled', !!why);
    wrap.classList.toggle('changed', Object.hasOwn(changes, field.path));
    reason.textContent = invalid.has(field.path) ? '请输入允许范围内的有效数值' : why;
  }
  for (const id of ['search', 'reset', 'export', 'import', 'preview-platform', 'reload']) $(id).disabled = busy;
  for (const id of ['editor', 'navigation']) $(id).inert = busy;
  $('save').disabled = busy || !dirty || !!invalid.size;
  $('apply').disabled = busy || !!dirty || !!invalid.size || snapshot.revision === snapshot.active_revision;
  $('reload').textContent = dirty || invalid.size ? '放弃草稿并重新载入' : '重新载入';
  $('changes-title').textContent = `修改预览 · ${dirty} 项`;
  $('changes').replaceChildren();
  for (const [path, next] of Object.entries(changes)) {
    const field = snapshot.fields.find(f => f.path === path);
    const row = node('div', '', 'diff-row');
    row.append(node('strong', `${field.title} · ${path}`));
    row.append(node('div', field.secret ? (next ? '将替换凭据' : '将清空凭据') : `${JSON.stringify(snapshot.values[path])} → ${JSON.stringify(next)}`, 'diff-values'));
    $('changes').append(row);
  }
  const runtime = snapshot.runtime;
  $('runtime').replaceChildren(...[
    `缓存目录：${runtime.cache_available ? '可用' : '不可用'}`,
    `FFmpeg：${runtime.ffmpeg_available ? '可用' : '未检测到'}`,
    `运行中媒体任务：${runtime.active_flows}`,
    `配置：${snapshot.revision === snapshot.active_revision ? '已应用' : '等待应用'}`,
  ].map(text => node('div', text, 'runtime-row')));
  preview();
}
function preview() {
  const target = $('message-preview');
  target.style.zoom = $('preview-scale').value;
  target.replaceChildren();
  $('advice').replaceChildren();
  const mode = value(`parsers.${platform}`);
  const asImage = value('message.text_metadata.render_to_image');
  const paginate = value('message.text_metadata.paginate_images');
  const separate = value('message.text_metadata.separate_text_sections');
  const interleaved = platform === 'wechat' && value('wechat.article_layout') === '按原文图文穿插'
    && mode !== '仅富媒体' && value('message.text_metadata.show_description');
  if (mode === '关闭') { target.append(node('p', '此平台已关闭自动解析。', 'empty')); return; }
  if (value('message.opening.enable')) target.append(node('div', value('message.opening.content'), 'bubble'));
  if (mode !== '仅富媒体') {
    const card = node('div', '', asImage ? 'bubble image-preview' : 'bubble');
    const styles = { '科技感': 'tech', '专业严肃': 'formal', '温和卡片': 'warm' };
    if (asImage) {
      card.classList.add(styles[value('message.text_metadata.render_style')] || 'fresh');
      card.style.fontSize = `${value('message.text_metadata.render_font_size')}px`;
      card.style.setProperty('--render-line-spacing', value('message.text_metadata.render_line_spacing'));
      card.style.setProperty('--render-paragraph-spacing', `${value('message.text_metadata.render_paragraph_spacing')}em`);
    }
    for (const [key, text] of [['show_title', '周末散步 · 城市里的小小发现'], ['show_author', '作者：旅行记录员'], ['show_timestamp', '发布时间：2026-09-26'], ['show_description', '放慢脚步，记录沿途的光影与声音。'], ['show_original_link', '原文链接：https://example.com/media']]) {
      if (value(`message.text_metadata.${key}`)) card.append(node('p', text));
    }
    target.append(card);
    if (interleaved) {
      if (mode !== '仅文本') target.append(node('div', '▧ 公众号正文配图', 'bubble media'));
      const continuation = card.cloneNode(false);
      continuation.append(node('p', '配图之后的正文：走到山间，阳光穿过树叶。'));
      continuation.append(node('p', '另一个段落：停下来喝一杯茶，再继续往前走。'));
      target.append(continuation);
    }
    if (value('message.hot_comments.count') > 0 && value(`message.hot_comments.${platform}`)) {
      const comments = asImage && !separate && !interleaved ? card : card.cloneNode(false);
      comments.append(node('p', `热评（最多 ${value('message.hot_comments.count')} 条）`));
      comments.append(node('strong', '01  旅行爱好者'));
      comments.append(node('p', '这一刻好美！'));
      comments.append(node('small', '赞 128 · 刚刚'));
      if (comments !== card) target.append(comments);
    }
    if (value('translation.enable') && (value('message.text_metadata.show_title') || value('message.text_metadata.show_description'))) {
      const translation = asImage && !separate && !interleaved ? card : card.cloneNode(false);
      translation.append(node('p', `翻译（${value('translation.target_language')}）`));
      translation.append(node('p', '这里展示标题或正文的翻译内容，示例不调用模型。'));
      if (translation !== card) target.append(translation);
    }
  }
  if (mode !== '仅文本' && !interleaved) target.append(node('div', platform === 'wechat' ? '▧ 公众号配图（集中发送）' : value('message.media_display.video_cover_only') ? '▧ 视频封面示意' : '▶ 媒体内容示意', 'bubble media'));
  $('advice').replaceChildren(...[
    `消息聚合：${value('message.packing.mode')}`,
    value('translation.enable') ? `翻译已启用：${value('translation.target_language')}，${value('translation.content_scope')}。示例不调用模型。` : '翻译未启用',
    value('message.text_metadata.quote_user_message') ? '会引用用户消息' : '独立回复消息',
    ...(asImage ? [
      paginate ? '长图自动分页已启用' : '长图自动分页未启用',
      interleaved ? '文字按配图位置分段渲染' : separate ? '文字栏目分开渲染' : '文字栏目合并渲染',
    ] : ['纯文字分开发送']),
    ...(platform === 'wechat' ? [`公众号文章排版：${value('wechat.article_layout')}`] : []),
  ].map(text => node('p', text, 'note')));
}
async function operation(action) {
  busy = true; if (snapshot) sync();
  try { await action(); } catch (error) { status(error.message || '操作失败，请查看后台日志', true); }
  finally { busy = false; if (snapshot) sync(); }
}
async function applyResult(result, oldInstance) {
  if (result.apply !== 'pending') {
    adopt(await bridge.apiGet('settings'));
    status(result.apply === 'busy' ? '配置已保存。有媒体任务正在运行，完成后点击「应用已保存配置」。' : '配置已保存，请在 AstrBot 插件管理中手动重载插件。');
    return;
  }
  status('配置已保存，正在重载插件…');
  for (let attempt = 0; attempt < 15; attempt++) {
    await new Promise(resolve => setTimeout(resolve, 2000));
    let data;
    try { data = await bridge.apiGet('settings'); } catch { continue; }
    if (data.instance !== oldInstance) { adopt(data); status('配置已保存并应用。'); return; }
    if (data.runtime.reload_error) { adopt(data); status(data.runtime.reload_error, true); return; }
  }
  adopt(await bridge.apiGet('settings'));
  status('配置已保存，尚未确认重载完成，请查看插件状态或稍后重新载入。', true);
}
$('search').oninput = () => render();
$('preview-platform').onchange = () => { platform = $('preview-platform').value; render(); };
$('preview-scale').onchange = () => { if (snapshot) preview(); };
$('reload').onclick = () => operation(async () => { adopt(await bridge.apiGet('settings')); status('已重新载入保存的配置。'); });
$('save').onclick = () => operation(async () => {
  const result = await bridge.apiPost('settings/save', { revision: snapshot.revision, changes });
  changes = {}; invalid.clear();
  await applyResult(result, snapshot.instance);
});
$('apply').onclick = () => operation(async () => applyResult(await bridge.apiPost('settings/apply', {}), snapshot.instance));
$('reset').textContent = '恢复本页默认值（保留凭据）';
$('reset').onclick = () => {
  for (const field of visibleFields.filter(f => !f.secret)) { invalid.delete(field.path); edit(field, structuredClone(field.default)); }
  render(); status('默认值已放入草稿，请检查修改预览后保存。');
};
$('export').onclick = () => {
  const values = Object.fromEntries(snapshot.fields.filter(f => !f.secret).map(f => [f.path, draft[f.path]]));
  const url = URL.createObjectURL(new Blob([JSON.stringify({ format: 'media-parser-pages', version: 1, values }, null, 2)], { type: 'application/json' }));
  const link = node('a'); link.href = url; link.download = 'media-parser-settings.json'; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  status('已导出当前草稿，文件不包含 Cookie、密钥和代理地址。');
};
$('import').onchange = () => operation(async () => {
  const file = $('import').files[0]; $('import').value = '';
  if (!file) return;
  if (file.size > 1024 * 1024) throw new Error('配置文件不能超过 1 MB');
  const data = JSON.parse(await file.text());
  if (data.format !== 'media-parser-pages' || data.version !== 1 || !data.values || typeof data.values !== 'object' || Array.isArray(data.values)) throw new Error('请选择从本页面导出的配置文件');
  const pending = [];
  for (const field of snapshot.fields.filter(f => !f.secret && Object.hasOwn(data.values, f.path))) {
    const next = data.values[field.path];
    const valid = field.type === 'bool' ? typeof next === 'boolean' : field.type === 'list' ? Array.isArray(next) && next.every(x => typeof x === 'string') : ['int', 'float'].includes(field.type) ? typeof next === 'number' && Number.isFinite(next) && (field.type !== 'int' || Number.isInteger(next)) : typeof next === 'string';
    if (!valid || (field.options.length && !field.options.includes(next)) || (field.bounds && (next < field.bounds[0] || (field.bounds[1] !== null && next > field.bounds[1])))) throw new Error(`「${field.title}」的值不合法，未导入文件`);
    pending.push([field, next]);
  }
  for (const [field, next] of pending) { invalid.delete(field.path); edit(field, next); }
  render(); status(`已导入 ${pending.length} 个非敏感字段到草稿，请检查后保存。`);
});
await operation(async () => {
  if (!bridge?.apiGet) throw new Error('请从 AstrBot 插件 Pages 打开此页面');
  await bridge.ready();
  adopt(await bridge.apiGet('settings'));
  status('配置已载入。修改先保存在草稿中，点击保存后应用。');
});
