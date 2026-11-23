import {api,h} from '/base.js';
import {ref,selectField} from '/patrol.js';
import {editionBox,readers} from './archive.js';

const changeNote = item =>
  `${ref(item.walk_id)} · ${h(item.route_code)} · ${h(item.crew)} · ${h(item.walked_on)} — ${h(item.change)}`;

function guardList(report){
  const blocking=[...(report?.pending_walks??[]),...(report?.stale_walks??[])];
  if(!blocking.length)return '';
  return `<ul class="stale-list">${blocking.map(item=>`<li>${changeNote(item)}</li>`).join('')}</ul>`;
}

export async function editionPanel(role){
  const [{editions,inbox},snapshots,readiness]=await Promise.all([
    api('/api/editions'),api('/api/effort'),api('/api/editions/readiness')]);
  const guard=new Map(readiness.snapshots.map(row=>[row.snapshot_id,row]));
  const frozen=snapshots.filter(row=>row.status==='frozen');
  const blockedIds=new Set(readiness.snapshots
    .filter(row=>row.stale_walks.length||row.pending_walks.length||!row.has_live_effort)
    .map(row=>row.snapshot_id));
  const saved=Object.fromEntries(frozen.map(row=>[row.id,
    `${row.label}${blockedIds.has(row.id)?'（旧透视·需重编）':''}`]));
  const previous={0:'首次发布',...Object.fromEntries(editions.map(row=>[row.id,row.series+' V'+row.volume]))};
  return `<div class="edition-grid"><section class="field-panel"><h2>巡护汇总发布簿</h2>
    ${editions.map(row=>editionBox(row,inbox.filter(mail=>mail.edition_id===row.id),role)).join('') || '<p>暂无签发版。</p>'}
    </section><aside class="field-panel"><h2>签发一期</h2>
    ${role==='reviewer' && frozen.length ? `<form data-station-path="/api/editions" data-number="snapshot_id supersedes"
        data-readiness='${h(JSON.stringify(Object.fromEntries(guard)))}' data-audiences>
      <label>站内系列号<input name="series" pattern="[a-z][a-z0-9-]{1,39}" placeholder="例如 birds-weekly" required></label>
      ${selectField('snapshot_id','冻结透视',saved)}${selectField('supersedes','更正来源',previous,0)}
      <label>更正依据<input name="reason" maxlength="300"></label>
      <fieldset><legend>站内收件岗位</legend>${Object.entries(readers).map(([code,label])=>`<label class="check-row">
        <input type="checkbox" name="audiences" value="${code}" ${code==='station'?'checked':''}>${h(label)}</label>`).join('')}</fieldset>
      <div id="pivot-guard" class="pivot-guard" role="status"></div>
      <button>签发统计版</button></form>` : ''}
    <p class="field-note">只签发范围内所有班次已处理、且与当前巡护记录一致的透视。补登或复核变化后须先在工时透视页重编，再签发更正版；范围外补登不拦签发。</p>
    <h2>透视时效核对</h2>
    ${frozen.length ? `<ul class="readiness-list">${frozen.map(item=>{
      const row=guard.get(item.id);
      const blocked=blockedIds.has(item.id);
      return `<li class="${blocked?'is-stale':'is-ready'}"><strong>${h(item.label)}</strong>
        <span>${blocked?'旧透视：需重编后签发':'与当前记录一致，可签发'}</span>
        ${guardList(row)}</li>`;
    }).join('')}</ul>`:'<p>暂无冻结透视。</p>'}
    </aside></div>`;
}
