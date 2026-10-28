/* gx3gen 本地软件前端逻辑（原生 JS，无构建、无外部依赖） */
'use strict';

// ---------------------------------------------------------------------------
// 基础状态与工具
// ---------------------------------------------------------------------------
const $ = (selector, root) => (root || document).querySelector(selector);
const $$ = (selector, root) => Array.from((root || document).querySelectorAll(selector));

const STEP_ORDER = ['style', 'requirement', 'plan', 'generate', 'review', 'settings'];

const KIND_LABELS = {
  init: '初始化段',
  station: '设备段（STL）',
  common: '公共段',
  rungs: '普通网络',
  statement: '语句段',
};

const ROLE_LABELS = { input: '输入', output: '输出', internal: '内部', inout: '输入输出' };

const STAT_LABELS = {
  rows: 'CSV 行',
  instructions: '指令',
  rungs: '网络',
  segments: '段',
  steps: '步数',
  comments: '注释',
  devices: '软元件',
};

const ARTIFACT_DEFS = [
  ['program_csv', '程序本体 CSV'],
  ['comment_csv', '软元件注释 CSV'],
  ['network_table', '网络表'],
  ['report', '生成报告'],
];

// 下载时使用的契约文件名（服务端同时兼容生成器的 program.csv 等文件名）
const ARTIFACT_URL_NAMES = {
  program_csv: '程序本体.csv',
  comment_csv: '软元件注释.csv',
  network_table: '网络表.md',
  report: '生成报告.md',
};

const state = {
  config: {},
  llm: {},
  projects: [],
  styles: [],
  project: null,        // 当前项目完整 JSON
  plan: null,           // 最近一次规划结果 {spec, checklist, prompt_hint, assumptions}
  generated: null,      // 最近一次生成结果
  stylePreview: null,   // 风格库中选中的画像
  selectedStyleId: '',
  feedback: [],
  lessons: [],
  step: 'style',
};

function esc(value) {
  return String(value == null ? '' : value).replace(/[&<>"']/g, ch => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[ch]));
}

function kindLabel(kind) {
  return KIND_LABELS[kind] || kind || '未知';
}

function roleLabel(role) {
  return ROLE_LABELS[role] || role || '';
}

function csvCell(value) {
  const text = String(value == null ? '' : value);
  return /[",\n\r]/.test(text) ? '"' + text.replace(/"/g, '""') + '"' : text;
}

function fmtTime(text) {
  if (!text) return '';
  return String(text).replace('T', ' ');
}

function showBanner(message, type, timeout) {
  const banner = $('#banner');
  if (!banner) return;
  banner.textContent = String(message || '');
  banner.className = 'banner ' + (type || 'error');
  banner.classList.remove('hidden');
  clearTimeout(showBanner._timer);
  const delay = timeout || (type === 'error' ? 10000 : 4000);
  showBanner._timer = setTimeout(() => banner.classList.add('hidden'), delay);
}

function hideBanner() {
  const banner = $('#banner');
  if (banner) banner.classList.add('hidden');
}

function setBusy(button, busy, busyText) {
  if (!button) return;
  if (busy) {
    if (!button.dataset.originalLabel) button.dataset.originalLabel = button.textContent;
    button.textContent = busyText || '处理中…';
    button.disabled = true;
    button.classList.add('busy');
  } else {
    if (button.dataset.originalLabel) {
      button.textContent = button.dataset.originalLabel;
      delete button.dataset.originalLabel;
    }
    button.disabled = false;
    button.classList.remove('busy');
  }
}

async function runAction(button, action, busyText) {
  setBusy(button, true, busyText);
  try {
    await action();
  } catch (error) {
    showBanner(error && error.message ? error.message : String(error), 'error', 12000);
  } finally {
    setBusy(button, false);
    updateActionAvailability();
  }
}

async function runQuiet(action) {
  try {
    await action();
  } catch (error) {
    showBanner(error && error.message ? error.message : String(error), 'error', 12000);
  }
}

// ---------------------------------------------------------------------------
// HTTP
// ---------------------------------------------------------------------------
async function request(method, path, body) {
  const options = { method, headers: {} };
  if (body !== undefined) {
    options.headers['Content-Type'] = 'application/json; charset=utf-8';
    options.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(path, options);
  } catch (error) {
    throw new Error('无法连接本地服务：' + (error && error.message ? error.message : error));
  }
  const text = await response.text();
  let data = {};
  if (text) {
    try {
      data = JSON.parse(text);
    } catch (error) {
      data = { error: text.slice(0, 500) };
    }
  }
  if (!response.ok) {
    throw new Error((data && data.error) || ('HTTP ' + response.status));
  }
  return data;
}

const api = {
  state: () => request('GET', '/api/state'),
  createProject: name => request('POST', '/api/projects', { name }),
  getProject: id => request('GET', '/api/projects/' + encodeURIComponent(id)),
  updateProject: (id, body) => request('PUT', '/api/projects/' + encodeURIComponent(id), body),
  plan: (id, useLlm) => request('POST', '/api/plan', { project_id: id, use_llm: !!useLlm }),
  generate: id => request('POST', '/api/generate', { project_id: id }),
  feedbackAdd: body => request('POST', '/api/feedback', body),
  feedbackList: id => request('GET', '/api/feedback?project_id=' + encodeURIComponent(id)),
  optimize: id => request('POST', '/api/optimize', { project_id: id }),
  importStyle: body => request('POST', '/api/style/import', body),
  getStyle: id => request('GET', '/api/styles/' + encodeURIComponent(id)),
  saveConfig: body => request('POST', '/api/config', body),
  ioParse: body => request('POST', '/api/io/parse', body),
  specValidate: spec => request('POST', '/api/spec/validate', { spec }),
};

// ---------------------------------------------------------------------------
// 文件 -> base64
// ---------------------------------------------------------------------------
function readFileAsBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      try {
        const bytes = new Uint8Array(reader.result);
        const chunk = 0x8000;
        let binary = '';
        for (let i = 0; i < bytes.length; i += chunk) {
          binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
        }
        resolve({ filename: file.name, base64: btoa(binary) });
      } catch (error) {
        reject(new Error('读取文件失败：' + file.name + '（' + (error.message || error) + '）'));
      }
    };
    reader.onerror = () => reject(new Error('读取文件失败：' + file.name));
    reader.readAsArrayBuffer(file);
  });
}

async function filesToPayload(fileList) {
  const output = [];
  for (const file of Array.from(fileList || [])) {
    output.push(await readFileAsBase64(file));
  }
  return output;
}

// ---------------------------------------------------------------------------
// 渲染：项目 / 步骤
// ---------------------------------------------------------------------------
function renderProjectList() {
  const list = $('#project-list');
  if (!list) return;
  if (!state.projects.length) {
    list.innerHTML = '<li class="empty-li">还没有项目，先新建一个。</li>';
    return;
  }
  list.innerHTML = state.projects.map(project => {
    const active = state.project && state.project.id === project.id;
    const pills = [];
    if (project.has_spec) pills.push('<span class="pill">架构已确认</span>');
    if (project.has_outputs) pills.push('<span class="pill ok">已生成</span>');
    if (project.feedback_count) pills.push('<span class="pill">反馈 ' + project.feedback_count + '</span>');
    return '<li><button type="button" class="project-item' + (active ? ' active' : '') +
      '" data-project-id="' + esc(project.id) + '">' +
      '<span class="p-name">' + esc(project.name || '未命名项目') + '</span>' +
      '<span class="p-meta">' + esc(fmtTime(project.updated_at || project.created_at)) +
      (pills.length ? ' ' + pills.join(' ') : '') + '</span>' +
      '</button></li>';
  }).join('');
}

function renderCurrentProject() {
  const box = $('#current-project');
  if (!box) return;
  if (!state.project) {
    box.classList.add('hidden');
    box.innerHTML = '';
    return;
  }
  box.classList.remove('hidden');
  const parts = [];
  parts.push('<span>当前项目：<b>' + esc(state.project.name || '未命名项目') + '</b></span>');
  parts.push('<span class="meta">' + esc(state.project.id) + '</span>');
  if (state.project.spec) parts.push('<span class="pill">架构已确认</span>');
  if (state.project.outputs && Object.keys(state.project.outputs).length) {
    parts.push('<span class="pill ok">已生成 ' + Object.keys(state.project.outputs).length + ' 个文件</span>');
  }
  box.innerHTML = parts.join('');
}

function switchStep(step) {
  if (STEP_ORDER.indexOf(step) < 0) return;
  state.step = step;
  $$('.step-btn').forEach(button => {
    button.classList.toggle('active', button.dataset.step === step);
  });
  $$('.section').forEach(section => {
    section.classList.toggle('hidden', section.dataset.section !== step);
  });
  updateStepStates();
  if (step === 'review') {
    renderFeedbackSegments();
    runQuiet(loadFeedback);
  }
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function updateStepStates() {
  const project = state.project;
  const done = {
    style: !!(project && project.style_profile),
    requirement: !!(project && (project.requirement || (project.io && project.io.length))),
    plan: !!(project && project.spec),
    generate: !!(project && project.outputs && Object.keys(project.outputs).length),
    review: !!(state.feedback && state.feedback.length),
    settings: false,
  };
  $$('.step-btn').forEach(button => {
    const step = button.dataset.step;
    button.classList.toggle('done', !!done[step] && step !== state.step);
  });
}

function updateActionAvailability() {
  const hasProject = !!state.project;
  const hasSpec = !!(state.project && state.project.spec);
  const mapping = {
    '#btn-style-apply': hasProject && !!state.selectedStyleId,
    '#btn-save-requirement': hasProject,
    '#btn-plan-rule': hasProject,
    '#btn-plan-ai': hasProject,
    '#btn-spec-validate': true,
    '#btn-spec-format': true,
    '#btn-spec-save': hasProject,
    '#btn-spec-generate': hasProject,
    '#btn-generate': hasProject && hasSpec,
    '#btn-feedback-submit': hasProject,
    '#btn-optimize': hasProject && hasSpec,
    '#btn-optimize-regenerate': hasProject && hasSpec,
  };
  Object.keys(mapping).forEach(selector => {
    const button = $(selector);
    if (button && !button.classList.contains('busy')) button.disabled = !mapping[selector];
  });
}

// ---------------------------------------------------------------------------
// 渲染：风格
// ---------------------------------------------------------------------------
function renderStyleLibrary() {
  const box = $('#style-library');
  if (!box) return;
  if (!state.styles.length) {
    box.innerHTML = '<div class="empty">还没有学习过风格。</div>';
    return;
  }
  box.innerHTML = state.styles.map(style => {
    const selected = style.id === state.selectedStyleId;
    return '<button type="button" class="style-item' + (selected ? ' selected' : '') +
      '" data-style-id="' + esc(style.id) + '">' +
      '<span><b>' + esc(style.name || '未命名风格') + '</b><br>' +
      '<span class="s-meta">' + esc(fmtTime(style.created_at)) + ' · ' +
      (style.segment_count || 0) + ' 段 · ' + ((style.source_files || []).length) + ' 个源文件</span></span>' +
      '<span class="pill">' + esc(style.id) + '</span></button>';
  }).join('');
}

function renderStyleSelects() {
  const select = $('#requirement-style-select');
  if (!select) return;
  const options = ['<option value="">不使用历史风格</option>'];
  state.styles.forEach(style => {
    options.push('<option value="' + esc(style.id) + '">' + esc(style.name || '未命名风格') + '</option>');
  });
  select.innerHTML = options.join('');
  const current = (state.project && state.project.style_profile && state.project.style_profile.id) ||
    state.selectedStyleId || '';
  select.value = current;
}

function renderCurrentStyle() {
  const label = $('#current-style-name');
  if (!label) return;
  const profile = state.project && state.project.style_profile;
  if (profile && profile.name) {
    label.textContent = profile.name + '（' + (profile.id || '无 id') + '）';
  } else if (profile && profile.id) {
    label.textContent = profile.id;
  } else {
    label.textContent = '未设置';
  }
}

function describeValue(value) {
  if (value == null) return '';
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return String(value);
  if (Array.isArray(value)) return value.map(describeValue).join('、');
  if (typeof value === 'object') {
    if (value.device) return value.device + (value.comment ? '（' + value.comment + '）' : '');
    try { return JSON.stringify(value); } catch (error) { return String(value); }
  }
  return String(value);
}

function renderStyleResult(profile) {
  const card = $('#style-result-card');
  const box = $('#style-result');
  if (!card || !box) return;
  if (!profile) {
    card.classList.add('hidden');
    box.innerHTML = '';
    return;
  }
  card.classList.remove('hidden');
  $('#style-result-name').textContent = profile.name || '';
  try {
    const parts = [];
    const segments = Array.isArray(profile.segments) ? profile.segments : [];
    const stepRanges = Array.isArray(profile.step_ranges) ? profile.step_ranges : [];
    const chips = [
      '<span class="chip">段结构 ' + segments.length + '</span>',
      '<span class="chip">步号段 ' + stepRanges.length + '</span>',
      '<span class="chip">源文件 ' + ((profile.source_files || []).length) + '</span>',
    ];
    if (profile.instruction_mix && typeof profile.instruction_mix === 'object') {
      chips.push('<span class="chip">指令种类 ' + Object.keys(profile.instruction_mix).length + '</span>');
    }
    parts.push('<div class="style-chips">' + chips.join('') + '</div>');

    if (segments.length) {
      parts.push('<div><div class="style-block-title">段结构</div><div class="table-wrap"><table>' +
        '<thead><tr><th>顺序</th><th>段标题</th><th>网络数</th><th>STL</th></tr></thead><tbody>' +
        segments.map(segment =>
          '<tr><td>' + esc(segment.order == null ? '' : segment.order) + '</td>' +
          '<td>' + esc(segment.title || '') + '</td>' +
          '<td>' + esc(segment.network_count == null ? '' : segment.network_count) + '</td>' +
          '<td>' + (segment.has_stl ? '是' : '') + '</td></tr>').join('') +
        '</tbody></table></div></div>');
    }

    const modeEntries = Object.keys(profile.modes || {});
    if (modeEntries.length) {
      parts.push('<div><div class="style-block-title">模式位</div><div class="style-chips">' +
        modeEntries.map(key => '<span class="chip gray">' + esc(key) + ' → ' +
          esc(describeValue(profile.modes[key])) + '</span>').join('') + '</div></div>');
    }

    const alarm = profile.alarm || {};
    if (alarm && Object.keys(alarm).length) {
      parts.push('<div><div class="style-block-title">报警布局</div><div class="style-chips">' +
        Object.keys(alarm).map(key => '<span class="chip gray">' + esc(key) + '：' +
          esc(describeValue(alarm[key])) + '</span>').join('') + '</div></div>');
    }

    const naming = profile.naming || {};
    if (naming && Object.keys(naming).length) {
      parts.push('<div><div class="style-block-title">命名习惯</div><div class="style-chips">' +
        Object.keys(naming).map(key => '<span class="chip gray">' + esc(key) + '：' +
          esc(describeValue(naming[key])) + '</span>').join('') + '</div></div>');
    }

    const mix = profile.instruction_mix || {};
    const mixEntries = Object.keys(mix)
      .map(key => [key, Number(mix[key]) || 0])
      .sort((a, b) => b[1] - a[1])
      .slice(0, 10);
    if (mixEntries.length) {
      const max = Math.max.apply(null, mixEntries.map(item => item[1])) || 1;
      parts.push('<div><div class="style-block-title">指令构成（前 10）</div><div class="instruction-mix">' +
        mixEntries.map(item =>
          '<div class="mix-row"><span>' + esc(item[0]) + '</span>' +
          '<span class="mix-bar"><i style="width:' + Math.round(item[1] / max * 100) + '%"></i></span>' +
          '<span>' + esc(item[1]) + '</span></div>').join('') + '</div></div>');
    }

    parts.push('<details><summary class="muted">查看完整画像 JSON</summary><pre class="pre">' +
      esc(JSON.stringify(profile, null, 2)) + '</pre></details>');
    box.innerHTML = parts.join('');
  } catch (error) {
    box.innerHTML = '<div class="empty">画像展示失败：' + esc(error.message || error) + '</div>';
  }
}

async function selectStylePreview(styleId) {
  state.selectedStyleId = styleId;
  renderStyleLibrary();
  updateActionAvailability();
  try {
    const data = await api.getStyle(styleId);
    state.stylePreview = data.style || null;
    renderStyleResult(state.stylePreview);
  } catch (error) {
    showBanner('读取风格失败：' + error.message, 'error');
  }
}

// ---------------------------------------------------------------------------
// 渲染：需求 / IO
// ---------------------------------------------------------------------------
function ioToCsv(io) {
  const lines = ['序号,软元件名,注释,类型'];
  (io || []).forEach((item, index) => {
    lines.push([
      csvCell(index + 1),
      csvCell(item.device || ''),
      csvCell(item.comment || ''),
      csvCell(roleLabel(item.role) || item.type || ''),
    ].join(','));
  });
  return lines.join('\n');
}

function fillRequirementForm() {
  const project = state.project;
  $('#requirement').value = (project && project.requirement) || '';
  $('#io-text').value = project && project.io && project.io.length ? ioToCsv(project.io) : '';
  $('#io-preview').classList.add('hidden');
  $('#io-preview').innerHTML = '';
  renderStyleSelects();
  renderCurrentStyle();
}

function renderIoPreview(io) {
  const box = $('#io-preview');
  if (!box) return;
  const rows = io || [];
  if (!rows.length) {
    box.classList.add('hidden');
    box.innerHTML = '';
    return;
  }
  box.classList.remove('hidden');
  box.innerHTML = '<table><thead><tr><th>#</th><th>软元件</th><th>注释</th><th>方向</th></tr></thead><tbody>' +
    rows.map((item, index) =>
      '<tr><td>' + (index + 1) + '</td><td>' + esc(item.device || '') + '</td>' +
      '<td class="wrap">' + esc(item.comment || '') + '</td>' +
      '<td>' + esc(roleLabel(item.role) || '') + '</td></tr>').join('') +
    '</tbody></table>';
}

// ---------------------------------------------------------------------------
// 渲染：架构 / 生成
// ---------------------------------------------------------------------------
function fillList(selector, items, emptyText) {
  const box = $(selector);
  if (!box) return;
  const list = (items || []).filter(item => item != null && String(item).trim() !== '');
  box.innerHTML = list.length
    ? list.map(item => '<li>' + esc(item) + '</li>').join('')
    : '<li class="empty-li">' + esc(emptyText || '暂无') + '</li>';
}

function qualityLines(quality) {
  const data = quality || {};
  const stats = data.stats || {};
  const lines = [];
  const bits = [];
  if (stats.segments !== undefined) bits.push('段 ' + stats.segments);
  if (stats.station_steps !== undefined) bits.push('自动流程步 ' + stats.station_steps);
  if (stats.instructions !== undefined) bits.push('指令 ' + stats.instructions);
  if (stats.has_cycle !== undefined) bits.push('循环 ' + (stats.has_cycle ? '有' : '无'));
  if (stats.has_timer !== undefined) bits.push('定时 ' + (stats.has_timer ? '有' : '无'));
  if (stats.has_start_stop !== undefined) bits.push('启停 ' + (stats.has_start_stop ? '有' : '无'));
  if (bits.length) lines.push('质量统计：' + bits.join(' / '));
  (data.errors || []).forEach(item => lines.push('[质量错误] ' + item));
  (data.warnings || []).forEach(item => lines.push('[质量警告] ' + item));
  (data.suggestions || []).forEach(item => lines.push('[质量建议] ' + item));
  if (!(data.errors || []).length && !(data.warnings || []).length && bits.length) {
    lines.unshift('质量检查：通过');
  }
  return lines;
}

function auditLines(audit) {
  const data = audit || {};
  if (!Object.keys(data).length) return [];
  if (data.ok) return ['GX 导入自检：通过（BOM/行尾/操作数/IL/STL/注释）'];
  return ['GX 导入自检：未通过'].concat(
    (data.problems || []).map(item => '[GX 自检] ' + item));
}

function renderSpecTextarea(spec) {
  $('#spec-json').value = spec ? JSON.stringify(spec, null, 2) : '';
}

function renderPlanInfo(plan) {
  const data = plan || {};
  const quality = qualityLines(data.quality);
  fillList('#plan-assumptions', quality.concat(data.assumptions || []), '暂无待确认事项');
  fillList('#plan-checklist', data.checklist || [], '暂无历史经验');
  const plannerLabel = data.planner === 'llm' ? '本地 LLM 规划（可选）' : (data.planner ? '规则规划' : '');
  $('#plan-prompt-hint').textContent =
    (plannerLabel ? '规划器：' + plannerLabel + '\n' : '') + (data.prompt_hint || '暂无');
  fillList('#optimize-checklist', data.checklist || [], '暂无');
  $('#optimize-prompt-hint').textContent = data.prompt_hint || '暂无';
}

function parseSpecTextarea() {
  const text = $('#spec-json').value.trim();
  if (!text) throw new Error('ProgramSpec 还是空的，请先在「② 需求 / IO」生成架构方案');
  try {
    return JSON.parse(text);
  } catch (error) {
    throw new Error('ProgramSpec JSON 解析失败：' + error.message);
  }
}

function segmentsFromSpec(spec) {
  if (!spec || !Array.isArray(spec.segments)) return [];
  return spec.segments.map((segment, index) => {
    const kind = segment.kind || '';
    const item = { index, title: segment.title || ('段 ' + (index + 1)), kind };
    if (kind === 'station') item.steps = (segment.steps || []).length;
    if (kind === 'rungs') item.networks = (segment.rungs || []).length;
    if (kind === 'statement') item.statements = (segment.statements || []).length;
    if (Array.isArray(segment.step_range) && segment.step_range.length === 2) {
      item.step_range = segment.step_range;
    }
    return item;
  });
}

function segmentsTableHtml(segments) {
  const rows = segments || [];
  if (!rows.length) return '<div class="empty">暂无段结构</div>';
  return '<table><thead><tr><th>#</th><th>段标题</th><th>类型</th><th>步号范围</th><th>规模</th></tr></thead><tbody>' +
    rows.map(segment => {
      let size = '—';
      if (segment.steps != null) size = segment.steps + ' 步';
      else if (segment.networks != null) size = segment.networks + ' 网络';
      else if (segment.statements != null) size = segment.statements + ' 语句';
      const range = Array.isArray(segment.step_range)
        ? segment.step_range[0] + ' – ' + segment.step_range[1] : '—';
      return '<tr><td>' + esc(segment.index) + '</td><td class="wrap">' + esc(segment.title) + '</td>' +
        '<td>' + esc(kindLabel(segment.kind)) + '</td><td>' + esc(range) + '</td><td>' + esc(size) + '</td></tr>';
    }).join('') + '</tbody></table>';
}

function artifactsFromOutputs(projectId, outputs) {
  if (!projectId || !outputs) return [];
  return ARTIFACT_DEFS
    .filter(item => outputs[item[0]])
    .map(item => {
      const downloadName = ARTIFACT_URL_NAMES[item[0]] || outputs[item[0]];
      return {
        key: item[0],
        label: item[1],
        name: outputs[item[0]],
        downloadName,
        url: '/api/projects/' + encodeURIComponent(projectId) + '/artifact/' +
          encodeURIComponent(downloadName),
      };
    });
}

function renderArtifacts(selector, artifacts) {
  const box = $(selector);
  if (!box) return;
  const items = artifacts || [];
  if (!items.length) {
    box.innerHTML = '<div class="empty">暂无产物</div>';
    return;
  }
  box.innerHTML = items.map(item => {
    const downloadName = item.downloadName || item.name;
    return '<a class="artifact" href="' + esc(item.url) + '" download="' + esc(downloadName) + '">' +
      esc(item.label) + '<small>' + esc(downloadName) + '</small></a>';
  }).join('');
}

function renderStats(selector, stats) {
  const box = $(selector);
  if (!box) return;
  const entries = Object.keys(stats || {});
  if (!entries.length) {
    box.innerHTML = '';
    return;
  }
  box.innerHTML = entries.map(key => {
    const label = STAT_LABELS[key] || key;
    let value = stats[key];
    if (value && typeof value === 'object') value = JSON.stringify(value);
    return '<div class="stat"><b>' + esc(value) + '</b><span>' + esc(label) + '</span></div>';
  }).join('');
}

function renderGenerated() {
  const generated = state.generated;
  const has = !!generated;
  $('#generate-empty').classList.toggle('hidden', has);
  $('#generate-result').classList.toggle('hidden', !has);

  if (has) {
    renderStats('#generate-stats', generated.stats || {});
    $('#generate-summary').textContent = generated.summary || '（生成器没有返回摘要）';
    $('#generate-segments').innerHTML = segmentsTableHtml(generated.segments || []);
    fillList('#generate-assumptions', generated.assumptions || [], '暂无待确认假设');
    fillList('#generate-lint', qualityLines(generated.quality)
      .concat(auditLines(generated.audit), generated.lint || []),
      '没有额外检查提示');
    renderArtifacts('#generate-artifacts', generated.artifacts || []);
  } else {
    $('#generate-stats').innerHTML = '';
    $('#generate-summary').textContent = '';
    $('#generate-segments').innerHTML = '';
    $('#generate-assumptions').innerHTML = '';
    $('#generate-lint').innerHTML = '';
    $('#generate-artifacts').innerHTML = '';
  }

  const reviewSegments = has ? (generated.segments || []) : [];
  $('#review-segments').innerHTML = reviewSegments.length
    ? segmentsTableHtml(reviewSegments)
    : '<div class="empty">还没有生成结果。</div>';
  renderArtifacts('#review-artifacts', has ? (generated.artifacts || []) : []);
  renderFeedbackSegments();
}

// ---------------------------------------------------------------------------
// 渲染：反馈 / 配置
// ---------------------------------------------------------------------------
function renderFeedbackSegments() {
  const select = $('#feedback-segment');
  if (!select) return;
  const previous = select.value;
  const segments = (state.generated && state.generated.segments) ||
    segmentsFromSpec(state.project && state.project.spec) || [];
  if (!segments.length) {
    select.innerHTML = '<option value="">（还没有段，请先生成架构）</option>';
    return;
  }
  select.innerHTML = segments.map(segment =>
    '<option value="' + esc(segment.title) + '">' + esc(segment.title) +
    '（' + esc(kindLabel(segment.kind)) + '）</option>').join('');
  if (previous) {
    const exists = segments.some(segment => segment.title === previous);
    if (exists) select.value = previous;
  }
}

function targetLabel(target) {
  if (!target || typeof target !== 'object') return '整个程序';
  const kind = target.kind || 'program';
  if (kind === 'segment') return '段：' + (target.segment || '?');
  if (kind === 'network') return '网络：' + (target.segment || '?') + ' #' + (target.index == null ? 0 : target.index);
  return '整个程序';
}

function renderFeedback() {
  const box = $('#feedback-list');
  if (!box) return;
  const records = state.feedback || [];
  const lessons = state.lessons || [];
  const parts = [];
  records.forEach(record => {
    const item = record || {};
    const severity = item.severity || 'suggestion';
    parts.push('<div class="feedback-item">' +
      '<div class="f-head">' +
      '<span class="badge ' + esc(severity) + '">' + esc(severity) + '</span>' +
      '<span class="badge">' + esc(item.category || '其他') + '</span>' +
      '<span class="muted">' + esc(targetLabel(item.target)) + '</span>' +
      '<span class="muted">' + esc(fmtTime(item.created_at || '')) + '</span>' +
      '</div>' +
      '<div class="f-message">' + esc(item.message || '') + '</div>' +
      '</div>');
  });
  if (lessons.length) {
    parts.push('<div class="f-lessons-title">派生经验（会注入下一轮规划 / 检查清单）</div>');
    lessons.forEach(lesson => {
      if (lesson && typeof lesson === 'object') {
        parts.push('<div class="feedback-item"><div class="f-head">' +
          '<span class="badge">' + esc(lesson.category || '经验') + '</span>' +
          '<span class="muted">出现 ' + esc(lesson.count == null ? '?' : lesson.count) + ' 次</span></div>' +
          '<div class="f-message">' + esc(lesson.last_message || '') + '</div></div>');
      } else {
        parts.push('<div class="feedback-item"><div class="f-message">' + esc(lesson) + '</div></div>');
      }
    });
  }
  if (!parts.length) {
    box.innerHTML = '<div class="empty">暂无反馈。</div>';
    return;
  }
  box.innerHTML = parts.join('');
}

function renderConfig() {
  const llm = state.llm || (state.config && state.config.llm) || {};
  $('#cfg-base-url').value = llm.base_url || '';
  $('#cfg-model').value = llm.model || '';
  $('#cfg-api-key').value = '';
  $('#cfg-timeout').value = llm.timeout == null ? 120 : llm.timeout;
  $('#cfg-temperature').value = llm.temperature == null ? 0.2 : llm.temperature;
}

// ---------------------------------------------------------------------------
// 数据加载
// ---------------------------------------------------------------------------
async function refreshState() {
  const data = await api.state();
  state.config = data.config || {};
  state.llm = data.llm || (state.config && state.config.llm) || {};
  state.projects = data.projects || [];
  state.styles = data.styles || [];
  renderProjectList();
  renderStyleLibrary();
  renderStyleSelects();
  renderConfig();
  updateStepStates();
  updateActionAvailability();
  const versionBox = $('#app-version');
  if (versionBox) versionBox.textContent = data.version ? 'v' + data.version : '';
}

function restoreGenerated() {
  const project = state.project;
  if (!project) {
    state.generated = null;
    renderGenerated();
    return;
  }
  const outputs = project.outputs || {};
  if (Object.keys(outputs).length) {
    const segments = segmentsFromSpec(project.spec);
    state.generated = {
      outputs,
      stats: project.stats || {},
      summary: project.summary || '',
      lint: project.lint || [],
      quality: project.quality || {},
      artifacts: artifactsFromOutputs(project.id, outputs),
      segments,
      assumptions: (project.spec && project.spec.assumptions) || [],
    };
  } else {
    state.generated = null;
  }
  renderGenerated();
}

async function selectProject(projectId) {
  const data = await api.getProject(projectId);
  state.project = data.project;
  state.selectedStyleId = (state.project.style_profile && state.project.style_profile.id) || '';
  localStorage.setItem('gx3gen.project', projectId);
  fillRequirementForm();
  renderSpecTextarea(state.project.spec);
  renderPlanInfo({
    assumptions: (state.project.spec && state.project.spec.assumptions) || [],
    checklist: state.project.checklist || [],
    prompt_hint: state.project.prompt_hint || '',
    quality: state.project.quality || {},
    planner: state.project.planner || '',
  });
  restoreGenerated();
  if (state.project.style_profile && state.project.style_profile.profile_version) {
    state.stylePreview = state.project.style_profile;
  }
  renderStyleLibrary();
  renderCurrentProject();
  renderProjectList();
  renderStyleResult(state.stylePreview);
  updateStepStates();
  updateActionAvailability();
  await runQuiet(loadFeedback);
}

async function loadFeedback() {
  if (!state.project) {
    state.feedback = [];
    state.lessons = [];
    renderFeedback();
    return;
  }
  try {
    const data = await api.feedbackList(state.project.id);
    state.feedback = data.feedback || [];
    state.lessons = data.lessons || [];
  } catch (error) {
    state.feedback = [];
    state.lessons = [];
  }
  renderFeedback();
  updateStepStates();
}

// ---------------------------------------------------------------------------
// 动作
// ---------------------------------------------------------------------------
async function doCreateProject() {
  const input = $('#new-project-name');
  const name = input.value.trim();
  if (!name) throw new Error('请先输入项目名称');
  const data = await api.createProject(name);
  input.value = '';
  await refreshState();
  if (data.project && data.project.id) {
    await selectProject(data.project.id);
    switchStep('requirement');
    showBanner('项目已创建：' + name, 'ok');
  }
}

async function doImportStyle() {
  const fileInput = $('#style-files');
  const files = fileInput.files;
  if (!files || !files.length) throw new Error('请选择至少一个程序本体 CSV 文件');
  const name = $('#style-name').value.trim() ||
    ('风格-' + new Date().toLocaleString('zh-CN', { hour12: false }).replace(/[\/\\:]/g, '-'));
  const payloadFiles = await filesToPayload(files);
  const profile = await api.importStyle({ name, files: payloadFiles });
  state.stylePreview = profile;
  state.selectedStyleId = profile.id || '';
  renderStyleResult(profile);
  await refreshState();
  renderStyleLibrary();
  updateActionAvailability();
  showBanner('风格学习完成：' + (profile.name || name) +
    '（' + ((profile.segments || []).length) + ' 段）', 'ok');
}

async function doApplyStyle() {
  if (!state.project) throw new Error('请先在左侧选择或新建项目');
  if (!state.selectedStyleId) throw new Error('请先在风格库中选择一个风格');
  const data = await api.updateProject(state.project.id, { style_profile: state.selectedStyleId });
  state.project = data.project;
  renderProjectList();
  renderCurrentProject();
  renderCurrentStyle();
  renderStyleSelects();
  renderStyleLibrary();
  updateStepStates();
  showBanner('已把该风格设为当前项目风格', 'ok');
}

async function doParseIo() {
  const fileInput = $('#io-files');
  const files = fileInput.files;
  let body;
  if (files && files.length) {
    body = { files: await filesToPayload(files) };
  } else {
    const text = $('#io-text').value;
    if (!text.trim()) throw new Error('请先粘贴 IO 表或选择 CSV 文件');
    body = { text };
  }
  const data = await api.ioParse(body);
  if (files && files.length) {
    $('#io-text').value = ioToCsv(data.io || []);
  }
  renderIoPreview(data.io || []);
  if (!(data.io || []).length) {
    showBanner('没有解析到 IO 行。请确认表头包含「软元件 / 注释」或列顺序为 序号,软元件名,注释,类型。', 'warn', 12000);
  } else {
    showBanner('已解析 ' + data.io.length + ' 个 IO 点，记得点「保存需求与 IO」。', 'ok');
  }
}

async function saveRequirementSilently() {
  if (!state.project) return;
  const body = {
    requirement: $('#requirement').value,
    io_csv: $('#io-text').value,
    style_profile: $('#requirement-style-select').value || null,
  };
  const data = await api.updateProject(state.project.id, body);
  state.project = data.project;
  state.selectedStyleId = (state.project.style_profile && state.project.style_profile.id) || '';
  renderCurrentStyle();
  updateStepStates();
}

async function doSaveRequirement() {
  if (!state.project) throw new Error('请先在左侧选择或新建项目');
  await saveRequirementSilently();
  renderProjectList();
  renderCurrentProject();
  updateStepStates();
  updateActionAvailability();
  showBanner('需求与 IO 已保存（' + ((state.project.io || []).length) + ' 个 IO 点）', 'ok');
}

async function doPlan(useLlm) {
  if (!state.project) throw new Error('请先在左侧选择或新建项目');
  await saveRequirementSilently();
  const data = await api.plan(state.project.id, useLlm);
  state.plan = data;
  state.project = data.project || state.project;
  renderSpecTextarea(data.spec);
  renderPlanInfo({
    assumptions: data.assumptions || [],
    checklist: data.checklist || [],
    prompt_hint: data.prompt_hint || '',
    quality: data.quality || {},
    planner: data.planner || '',
  });
  renderProjectList();
  renderCurrentProject();
  updateStepStates();
  updateActionAvailability();
  switchStep('plan');
  const quality = data.quality || {};
  const qualityErrors = quality.errors || [];
  const planner = data.planner || '';
  if (qualityErrors.length) {
    showBanner('规划完成但质量检查未通过：\n- ' + qualityErrors.join('\n- ') +
      '\n建议在 AI agent 里修正 ProgramSpec，或补充需求/IO 后重试。', 'error', 20000);
  } else if (useLlm && planner !== 'llm') {
    showBanner('本地 LLM 未配置或不可用，已自动使用规则解析：共 ' +
      (((data.spec || {}).segments || []).length) + ' 个段。' +
      '（用 AI agent 打开项目时，不需要本地 LLM。）', 'ok', 12000);
  } else {
    const mode = planner === 'llm' ? '本地 LLM 解析' : '规则解析';
    showBanner(mode + '完成：共 ' +
      (((data.spec || {}).segments || []).length) + ' 个段，质量检查通过，请确认架构后生成程序。', 'ok');
  }
}

async function doSpecValidate() {
  const spec = parseSpecTextarea();
  const data = await api.specValidate(spec);
  if (data.ok) {
    showBanner('ProgramSpec 校验通过。', 'ok');
  } else {
    showBanner('ProgramSpec 校验未通过：\n' + (data.error || ''), 'error', 15000);
  }
}

function doSpecFormat() {
  const spec = parseSpecTextarea();
  renderSpecTextarea(spec);
  showBanner('JSON 已格式化。', 'ok');
}

async function saveSpecTextarea() {
  if (!state.project) throw new Error('请先在左侧选择或新建项目');
  const spec = parseSpecTextarea();
  const data = await api.updateProject(state.project.id, { spec });
  state.project = data.project;
  renderProjectList();
  renderCurrentProject();
  updateStepStates();
  updateActionAvailability();
  return spec;
}

async function doSpecSave() {
  await saveSpecTextarea();
  showBanner('架构已保存。', 'ok');
}

async function doGenerate() {
  if (!state.project) throw new Error('请先在左侧选择或新建项目');
  const data = await api.generate(state.project.id);
  state.project = data.project || state.project;
  state.generated = {
    outputs: data.outputs || {},
    stats: data.stats || {},
    summary: data.summary || '',
    lint: data.lint || [],
    quality: data.quality || {},
    artifacts: artifactsFromOutputs(state.project.id, data.outputs || {}),
    segments: data.segments || segmentsFromSpec(state.project.spec),
    assumptions: data.assumptions || [],
  };
  renderGenerated();
  renderProjectList();
  renderCurrentProject();
  updateStepStates();
  updateActionAvailability();
  switchStep('generate');
  const count = Object.keys(data.outputs || {}).length;
  showBanner('程序已生成：' + count + ' 个文件，可在下方下载或去「审阅 / 反馈」。', 'ok');
  await runQuiet(refreshState);
}

async function doSpecGenerate() {
  await saveSpecTextarea();
  await doGenerate();
}

async function doSubmitFeedback() {
  if (!state.project) throw new Error('请先在左侧选择或新建项目');
  const kind = $('#feedback-target-kind').value;
  const target = { kind };
  if (kind === 'segment') {
    target.segment = $('#feedback-segment').value;
    if (!target.segment) throw new Error('请选择反馈的段');
  } else if (kind === 'network') {
    target.segment = $('#feedback-segment').value;
    if (!target.segment) throw new Error('请选择网络所属的段');
    target.index = Number($('#feedback-network').value || 0);
  }
  const message = $('#feedback-message').value.trim();
  if (!message) throw new Error('请填写问题说明');
  const body = {
    project_id: state.project.id,
    target,
    category: $('#feedback-category').value,
    severity: $('#feedback-severity').value,
    message,
    expected: $('#feedback-expected').value.trim(),
  };
  const data = await api.feedbackAdd(body);
  state.feedback = data.feedback || [];
  state.lessons = data.lessons || [];
  renderFeedback();
  updateStepStates();
  $('#feedback-message').value = '';
  $('#feedback-expected').value = '';
  showBanner('反馈已记录，下一轮规划/生成会带上这类经验。', 'ok');
  await runQuiet(refreshState);
}

async function doOptimize(regenerate) {
  if (!state.project) throw new Error('请先在左侧选择或新建项目');
  const data = await api.optimize(state.project.id);
  state.project = data.project || state.project;
  renderSpecTextarea(data.spec);
  renderPlanInfo({
    assumptions: (data.spec && data.spec.assumptions) || [],
    checklist: data.checklist || [],
    prompt_hint: data.prompt_hint || '',
  });
  updateActionAvailability();
  showBanner('已把历史反馈聚合成检查清单 / 规划提示。', 'ok');
  if (regenerate) {
    await doGenerate();
  }
}

async function doSaveConfig() {
  const llm = {
    base_url: $('#cfg-base-url').value.trim(),
    model: $('#cfg-model').value.trim(),
    timeout: parseInt($('#cfg-timeout').value, 10) || 120,
    temperature: parseFloat($('#cfg-temperature').value),
  };
  if (Number.isNaN(llm.temperature)) llm.temperature = 0.2;
  const key = $('#cfg-api-key').value.trim();
  if (key) llm.api_key = key;
  const data = await api.saveConfig({ llm });
  state.config = data.config || {};
  state.llm = data.llm || {};
  renderConfig();
  showBanner('设置已保存（api_key 不回显）。', 'ok');
}

// ---------------------------------------------------------------------------
// 事件绑定
// ---------------------------------------------------------------------------
function bindEvents() {
  $('#btn-create-project').addEventListener('click', () =>
    runAction($('#btn-create-project'), doCreateProject, '创建中…'));
  $('#new-project-name').addEventListener('keydown', event => {
    if (event.key === 'Enter') $('#btn-create-project').click();
  });

  $('#project-list').addEventListener('click', event => {
    const item = event.target.closest('[data-project-id]');
    if (!item) return;
    runQuiet(() => selectProject(item.dataset.projectId));
  });

  $('#stepbar').addEventListener('click', event => {
    const button = event.target.closest('[data-step]');
    if (button) switchStep(button.dataset.step);
  });

  $('#style-files').addEventListener('change', event => {
    const files = Array.from(event.target.files || []);
    const hint = $('#style-file-hint');
    if (!files.length) {
      hint.textContent = '尚未选择文件';
      return;
    }
    hint.textContent = '已选择 ' + files.length + ' 个文件：' +
      files.map(file => file.name).join('、');
  });
  $('#btn-style-import').addEventListener('click', () =>
    runAction($('#btn-style-import'), doImportStyle, '分析风格中…'));
  $('#btn-style-apply').addEventListener('click', () =>
    runAction($('#btn-style-apply'), doApplyStyle, '保存中…'));

  $('#style-library').addEventListener('click', event => {
    const item = event.target.closest('[data-style-id]');
    if (item) runQuiet(() => selectStylePreview(item.dataset.styleId));
  });

  $('#io-files').addEventListener('change', event => {
    const files = Array.from(event.target.files || []);
    if (files.length) {
      showBanner('已选择 ' + files.length + ' 个 IO 文件，点「解析 IO 表」后会自动填入文本框。', 'ok');
    }
  });
  $('#btn-io-parse').addEventListener('click', () =>
    runAction($('#btn-io-parse'), doParseIo, '解析中…'));
  $('#btn-save-requirement').addEventListener('click', () =>
    runAction($('#btn-save-requirement'), doSaveRequirement, '保存中…'));
  $('#btn-plan-rule').addEventListener('click', () =>
    runAction($('#btn-plan-rule'), () => doPlan(false), '规则解析中…'));
  $('#btn-plan-ai').addEventListener('click', () =>
    runAction($('#btn-plan-ai'), () => doPlan(true), '本地 LLM 解析中（可选）…'));

  $('#btn-spec-validate').addEventListener('click', () =>
    runAction($('#btn-spec-validate'), doSpecValidate, '校验中…'));
  $('#btn-spec-format').addEventListener('click', () =>
    runAction($('#btn-spec-format'), doSpecFormat));
  $('#btn-spec-save').addEventListener('click', () =>
    runAction($('#btn-spec-save'), doSpecSave, '保存中…'));
  $('#btn-spec-generate').addEventListener('click', () =>
    runAction($('#btn-spec-generate'), doSpecGenerate, '生成中…'));

  $('#btn-generate').addEventListener('click', () =>
    runAction($('#btn-generate'), doGenerate, '生成中…'));
  $('#btn-goto-review').addEventListener('click', () => switchStep('review'));

  $('#feedback-target-kind').addEventListener('change', event => {
    const kind = event.target.value;
    $('#feedback-segment-field').classList.toggle('hidden', kind === 'program');
    $('#feedback-network-field').classList.toggle('hidden', kind !== 'network');
  });
  $('#btn-feedback-submit').addEventListener('click', () =>
    runAction($('#btn-feedback-submit'), doSubmitFeedback, '提交中…'));
  $('#btn-feedback-refresh').addEventListener('click', () =>
    runAction($('#btn-feedback-refresh'), loadFeedback, '刷新中…'));
  $('#btn-optimize').addEventListener('click', () =>
    runAction($('#btn-optimize'), () => doOptimize(false), '应用中…'));
  $('#btn-optimize-regenerate').addEventListener('click', () =>
    runAction($('#btn-optimize-regenerate'), () => doOptimize(true), '优化并重新生成中…'));

  $('#btn-save-config').addEventListener('click', () =>
    runAction($('#btn-save-config'), doSaveConfig, '保存中…'));
}

// ---------------------------------------------------------------------------
// 启动
// ---------------------------------------------------------------------------
async function boot() {
  bindEvents();
  window.addEventListener('error', event => {
    showBanner('页面脚本错误：' + (event.message || ''), 'error', 15000);
  });
  window.addEventListener('unhandledrejection', event => {
    const reason = event.reason;
    showBanner('操作失败：' + ((reason && reason.message) || reason || '未知错误'), 'error', 15000);
  });

  await refreshState();

  // 支持 ?project=<id>&step=<style|requirement|plan|generate|review|settings> 深链接
  const params = new URLSearchParams(window.location.search);
  const requestedProject = params.get('project');
  const requestedStep = params.get('step');
  const savedId = localStorage.getItem('gx3gen.project');
  const targetId = (requestedProject && state.projects.some(project => project.id === requestedProject))
    ? requestedProject
    : savedId;

  let restored = false;
  if (targetId && state.projects.some(project => project.id === targetId)) {
    try {
      await selectProject(targetId);
      restored = true;
    } catch (error) {
      showBanner('恢复上次项目失败：' + error.message, 'warn');
    }
  }

  if (requestedStep && STEP_ORDER.indexOf(requestedStep) >= 0) {
    switchStep(requestedStep);
  } else if (restored && state.project) {
    if (state.generated) switchStep('generate');
    else if (state.project.spec) switchStep('plan');
    else switchStep('requirement');
  } else {
    switchStep('style');
  }
  updateActionAvailability();
}

document.addEventListener('DOMContentLoaded', () => {
  boot().catch(error => {
    showBanner('初始化失败：' + (error && error.message ? error.message : error), 'error', 20000);
  });
});
