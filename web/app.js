const state = {context: null, current: null};
const element = identifier => document.getElementById(identifier);

async function request(path, body) {
  const response = await fetch(path, body === undefined ? {} : {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
  });
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || '请求失败');
  return value;
}

function message(value, error = false) {
  element('status').textContent = value;
  element('status').className = error ? 'error' : '';
}

async function guarded(action) {
  try { await action(); } catch (error) { message(error.message, true); }
}

function selected() {
  const selection = {};
  for (const control of state.context.product.options) selection[control[0]] = element('select-' + control[0]).value;
  return {revision: element('revision').value, selection};
}

function renderControls() {
  const revision = element('revision').value || state.context.default_revision;
  element('revision').replaceChildren();
  for (const dataset of state.context.datasets) {
    const option = document.createElement('option');
    option.value = dataset.revision;
    option.textContent = dataset.label + ' · ' + dataset.revision.slice(0, 9);
    element('revision').append(option);
  }
  element('revision').value = revision;
  if (!element('revision').value) element('revision').value = state.context.default_revision;
  if (element('controls').children.length) return;
  for (const [key, labelText, choices] of state.context.product.options) {
    const label = document.createElement('label');
    label.textContent = labelText;
    const select = document.createElement('select');
    select.id = 'select-' + key;
    for (const choice of choices) {
      const option = document.createElement('option');
      option.value = choice;
      option.textContent = choice;
      select.append(option);
    }
    select.value = state.context.product.selection[key];
    label.append(select);
    element('controls').append(label);
  }
}

function displayValue(value) {
  if (value === null || value === undefined) return '未观测';
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(4);
  return typeof value === 'object' ? JSON.stringify(value) : String(value);
}

function drawChart(series) {
  const canvas = element('chart');
  const context = canvas.getContext('2d');
  context.clearRect(0, 0, canvas.width, canvas.height);
  const values = series.filter(point => typeof point.y === 'number' && Number.isFinite(point.y));
  context.font = '12px sans-serif';
  if (!values.length) { context.fillText('该范围没有可定量的描述值', 30, 50); return; }
  const low = Math.min(0, ...values.map(point => point.y));
  const high = Math.max(1, ...values.map(point => point.y));
  const scale = value => 250 - (value - low) / (high - low || 1) * 210;
  context.strokeStyle = '#90a5ad';
  context.beginPath(); context.moveTo(45, 20); context.lineTo(45, 250); context.lineTo(940, 250); context.stroke();
  context.fillStyle = '#0a7168';
  const scatter = values.every(point => typeof point.x === 'number');
  const minimumX = scatter ? Math.min(...values.map(point => point.x)) : 0;
  const maximumX = scatter ? Math.max(...values.map(point => point.x)) : 1;
  values.forEach((point, index) => {
    const position = scatter ? 60 + (point.x - minimumX) / (maximumX - minimumX || 1) * 850 : 55 + index * 875 / values.length;
    if (scatter) {
      context.beginPath(); context.arc(position, scale(point.y), 3.5, 0, Math.PI * 2); context.fill();
    } else {
      context.fillRect(position, Math.min(scale(point.y), scale(0)), Math.max(3, 800 / values.length), Math.max(1, Math.abs(scale(point.y) - scale(0))));
    }
    if (values.length < 12) context.fillText(point.label, position, 275);
  });
  context.fillStyle = '#29434e';
  context.fillText(high.toFixed(2), 0, 45); context.fillText(low.toFixed(2), 0, 250);
  context.fillText(scatter ? '沿用方法均值 → 候选方法均值（原始定量配对）' : '按表中顺序展示描述值；缺测不绘制为零', 50, 20);
}

function drawTrends(trends, xlabels) {
  const canvas = element('chart');
  const context = canvas.getContext('2d');
  context.clearRect(0, 0, canvas.width, canvas.height);
  context.font = '12px sans-serif';
  const palette = ['#0a7168', '#c2571a', '#4a6fa5', '#8b4a8b', '#b03a2e', '#5b7f2b'];
  const all = [];
  for (const series of trends) for (const value of series.values) {
    if (typeof value === 'number' && Number.isFinite(value)) all.push(value);
  }
  if (!all.length) { context.fillText('该范围没有可定量的描述值', 30, 50); return; }
  const low = Math.min(0, ...all);
  const high = Math.max(1, ...all);
  const scale = value => 250 - (value - low) / (high - low || 1) * 210;
  const count = Math.max(...trends.map(series => series.values.length), 1);
  const position = index => count === 1 ? 490 : 60 + index * 850 / (count - 1);
  context.strokeStyle = '#90a5ad';
  context.beginPath(); context.moveTo(45, 20); context.lineTo(45, 250); context.lineTo(940, 250); context.stroke();
  trends.forEach((series, order) => {
    const color = palette[order % palette.length];
    context.strokeStyle = color; context.fillStyle = color;
    context.beginPath();
    let started = false;
    series.values.forEach((value, index) => {
      if (typeof value !== 'number' || !Number.isFinite(value)) { started = false; return; }
      const x = position(index), y = scale(value);
      if (started) context.lineTo(x, y); else { context.moveTo(x, y); started = true; }
    });
    context.stroke();
    series.values.forEach((value, index) => {
      if (typeof value !== 'number' || !Number.isFinite(value)) return;
      context.beginPath(); context.arc(position(index), scale(value), 3, 0, Math.PI * 2); context.fill();
    });
    const legendX = 610 + (order % 2) * 165, legendY = 10 + Math.floor(order / 2) * 16;
    context.fillRect(legendX, legendY - 8, 10, 10);
    context.fillText(series.label, legendX + 14, legendY);
  });
  context.fillStyle = '#29434e';
  context.fillText(high.toFixed(2), 0, 45); context.fillText(low.toFixed(2), 0, 250);
  if (xlabels.length <= 8) xlabels.forEach((label, index) => context.fillText(label, position(index) - 14, 268));
  context.fillText('计数类为首窗=100的指数；占比类为百分值', 50, 20);
}

function fillTable(head, body, rows) {
  const fields = rows.length ? Object.keys(rows[0]).filter(field => !field.startsWith('_')) : [];
  const header = document.createElement('tr');
  for (const field of fields) { const cell = document.createElement('th'); cell.textContent = field; header.append(cell); }
  head.replaceChildren(header); body.replaceChildren();
  for (const row of rows) {
    const line = document.createElement('tr'); line.tabIndex = 0;
    for (const field of fields) { const cell = document.createElement('td'); cell.textContent = displayValue(row[field]); line.append(cell); }
    const detail = row._detail !== undefined ? row._detail : row;
    line.onclick = () => { element('details').textContent = JSON.stringify(detail, null, 2); };
    line.onkeydown = event => { if (event.key === 'Enter') line.click(); };
    body.append(line);
  }
}

function render(result, heading) {
  state.current = result;
  element('summary').replaceChildren();
  for (const [label, value] of Object.entries(result.view.summary)) {
    const card = document.createElement('article');
    const title = document.createElement('span'); title.textContent = label;
    const amount = document.createElement('strong'); amount.textContent = displayValue(value);
    card.append(title, amount); element('summary').append(card);
  }
  element('notes').replaceChildren();
  for (const note of result.view.notes) {
    const paragraph = document.createElement('p'); paragraph.textContent = note; element('notes').append(paragraph);
  }
  element('view-heading').textContent = result.view.heading || '按作品展开的协作网络';
  fillTable(element('columns'), element('rows'), result.view.rows || []);
  element('extra-tables').replaceChildren();
  for (const table of result.view.tables || []) {
    const title = document.createElement('h3'); title.textContent = table.title;
    const wrap = document.createElement('div'); wrap.className = 'table-wrap';
    const grid = document.createElement('table');
    const head = document.createElement('thead'), body = document.createElement('tbody');
    grid.append(head, body); wrap.append(grid);
    fillTable(head, body, table.rows || []);
    element('extra-tables').append(title, wrap);
  }
  if (result.view.trends) drawTrends(result.view.trends, result.view.xlabels || []);
  else drawChart(result.view.series || []);
  element('details').textContent = JSON.stringify({revision: result.revision, selection: result.selection,
    definitions: result.view.definitions || null, history: result.view.history || null}, null, 2);
  message((heading || '已更新描述') + ' · 数据 ' + result.revision.slice(0, 12));
}

async function refresh() {
  state.context = await request('/api/context');
  renderControls();
  element('archive').replaceChildren();
  for (const notebook of state.context.notebooks) {
    const button = document.createElement('button'); button.textContent = notebook.title + ' #' + notebook.id;
    button.onclick = () => guarded(async () => {
      const saved = await request('/api/notebooks/' + notebook.id);
      element('revision').value = saved.revision;
      for (const [key, value] of Object.entries(saved.selection)) {
        const control = element('select-' + key); if (control) control.value = value;
      }
      render(saved.result, '已读取保存的描述，不随新数据变化');
    });
    element('archive').append(button);
  }
}

element('inspect').onclick = () => guarded(async () => render(await request('/api/explore', selected())));
element('save').onclick = () => guarded(async () => {
  const saved = await request('/api/notebooks', {...selected(), title: element('title').value});
  await refresh(); render(saved.result, '已保存记录 #' + saved.id);
});
element('raw').onclick = () => guarded(async () => {
  const revision = state.current ? state.current.revision : element('revision').value;
  element('details').textContent = JSON.stringify(await request('/api/data?revision=' + encodeURIComponent(revision)), null, 2);
});
element('upload').onchange = () => guarded(async () => {
  const file = element('upload').files[0]; if (!file) return;
  const data = JSON.parse(await file.text());
  const uploaded = await request('/api/datasets', {label: file.name, data});
  await refresh(); element('revision').value = uploaded.revision;
  render(await request('/api/explore', selected()), '已导入独立数据版本');
});
element('export').onclick = () => guarded(async () => {
  if (!state.current) throw new Error('请先查看一个范围');
  const data = await request('/api/data?revision=' + encodeURIComponent(state.current.revision));
  const blob = new Blob([JSON.stringify({description: state.current, data}, null, 2)], {type: 'application/json'});
  const url = URL.createObjectURL(blob); const link = document.createElement('a');
  link.href = url; link.download = 'analysis-' + state.current.revision.slice(0, 12) + '.json'; link.click();
  URL.revokeObjectURL(url);
});
guarded(async () => { await refresh(); render(await request('/api/explore', selected())); });
