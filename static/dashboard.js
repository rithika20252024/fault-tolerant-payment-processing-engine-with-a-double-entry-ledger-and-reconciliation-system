const el = id => document.getElementById(id);
let connected = false, refreshing = false;
async function api(path, options = {}) {
  const response = await fetch(path, {...options, headers: {
    Authorization: `Bearer ${el('token').value}`, 'Content-Type': 'application/json', ...(options.headers || {})
  }});
  const body = await response.json();
  if (!response.ok) throw new Error(body.detail || `HTTP ${response.status}`);
  return body;
}
function output(body) { el('result').textContent = JSON.stringify(body, null, 2); }
async function refresh() {
  if (refreshing) return;
  refreshing = true;
  try {
    const [stats, payments] = await Promise.all([api('/stats'), api('/payments/recent/list')]);
    const cards = [['Provider', stats.provider], ['Completed', stats.payments.succeeded || 0],
      ['Pending jobs', (stats.jobs.ready || 0) + (stats.jobs.running || 0)], ['Dead jobs', stats.jobs.dead || 0],
      ['Completion p95', stats.latency.p95_ms === null ? '—' : `${Math.round(stats.latency.p95_ms)} ms`]];
    el('cards').replaceChildren(...cards.map(([label, value]) => {
      const div = document.createElement('div'); div.className = 'card';
      div.textContent = label; const b = document.createElement('b'); b.textContent = value; div.append(b); return div;
    }));
    el('rows').replaceChildren(...payments.map(p => {
      const tr = document.createElement('tr');
      for (const value of [p.id, `${p.amount} ${p.currency.toUpperCase()} minor units`, p.status, new Date(p.created_at).toLocaleString()]) {
        const td = document.createElement('td'); td.textContent = value; tr.append(td);
      } return tr;
    }));
    el('message').textContent = 'Connected · sandbox data'; connected = true;
  } catch (error) { el('message').textContent = error.message; connected = false; }
  finally { refreshing = false; }
}
el('connect').onclick = refresh;
el('create').onclick = async () => {
  try { output(await api('/payments', {method:'POST', headers:{'Idempotency-Key':crypto.randomUUID()},
    body:JSON.stringify({amount:Number(el('amount').value), currency:'usd'})})); await refresh(); }
  catch (e) { output({error:e.message}); }
};
el('audit').onclick = async () => { try { output(await api('/invariants')); } catch(e) { output({error:e.message}); } };
el('reconcile').onclick = async () => {
  try { let after='', runs=[]; do { const run = await api(`/reconcile?after=${encodeURIComponent(after)}`, {method:'POST'});
    runs.push(run); after=run.next_cursor; } while(after); output(runs); await refresh(); }
  catch(e) { output({error:e.message}); }
};
setInterval(() => { if (connected) refresh(); }, 3000);
if (location.protocol === 'file:') {
  el('message').textContent = 'Open the running application: ';
  const link = document.createElement('a'); link.href = 'http://localhost:18080';
  link.textContent = 'http://localhost:18080'; el('message').append(link);
  for (const id of ['connect','create','audit','reconcile']) el(id).disabled = true;
}
