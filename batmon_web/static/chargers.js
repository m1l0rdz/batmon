/* Charger evidence widgets. Physical setup labels belong to this browser only. */
(() => {
  'use strict';
  window.BatmonChargers = {create(H) {
    const {esc,num,wh,duration,tsLabel,card,table,canvas,chart,timeOptions,saved,save} = H;
    const $ = s => document.querySelector(s);
    const stored = saved('chargerLabels', {});
    const labels = stored && typeof stored === 'object' && !Array.isArray(stored) ? stored : {};
    let selected = null, a = null, b = null, filter = '', page = 0, message = '';
    const label = s => {
      const value = labels[String(s.id)];
      return value && typeof value === 'object' ? value : {};
    };
    const descriptor = s => `${s.descriptor?.description || 'Unidentified source'} / ${num(s.descriptor?.capability_w,0,' W')}`;
    const title = s => label(s).adapter || descriptor(s);
    const setup = s => [label(s).cable,label(s).port].filter(Boolean).join(' / ') || 'Cable and port not labelled';
    const option = (s, current) => `<option value="${s.id}" ${s.id===current?'selected':''}>#${s.id} ${esc(title(s))} | ${esc(tsLabel(s.started))}</option>`;
    const selectedSession = d => d.sessions.find(s=>s.id===selected) || d.sessions[0];
    const statusNames = {observing:'Observing',stale:'Readings are stale',disconnected:'On battery',policy_hold:'Holding at charge limit',charging:'Charging under current load',paused:'Charging paused',battery_assisting:'Battery assists the source'};
    function current(d) {
      const c=d.current;
      if (d.service_update_required) return '<section class="charger-panel"><h2>Charger monitoring needs a service restart</h2><p class="note">The running web service has not loaded the charger update. Restart batmon to enable it. Existing battery history remains available below.</p></section>';
      if (!c) return `<section class="charger-panel"><h2>Connected source</h2><p class="empty">No charger observations yet. ${d.available?'Connect a charger to begin collecting.':'Update the collector to begin recording source history.'}</p></section>`;
      const st=c.assessment||{}, connected=c.on_ac, live=st.code!=='stale';
      const s=d.sessions.find(s=>s.id===c.session_id);
      return `<section class="charger-panel charger-current"><div class="charger-title"><div><p class="eyebrow">Connected source</p><h2>${connected?esc(s?title(s):c.description||'Unidentified source'):'Running on battery'}</h2></div><span class="charger-status ${['battery_assisting','stale'].includes(st.code)?'attention':''}">${esc(statusNames[st.code]||'Observing')}</span></div><p>${esc(st.message)}</p>${live&&c.recent_disconnects>=2?`<p class="charger-observation">${c.recent_disconnects} observed power disconnections in the last 15 minutes. If these were not intentional, check the cable, port, dock or adapter. Sleep gaps are excluded.</p>`:''}${connected?`<div class="charger-flow">${card('Source capability',num(c.capability_w,0,' W'),'Reported by macOS; not actual consumption.')}${card('Into the Mac',num(live?c.input_w:null,1,' W'),'Cross-checked OS input telemetry.')}${card(c.watts<0?'From the battery':'Into the battery',num(live&&c.watts!=null?Math.abs(c.watts):null,1,' W'),c.watts<0?'The battery is supplying energy.':'Net battery power; separate from source capability.')}</div><p class="note">Profile ${num(c.profile_v,1,' V')} / ${num(c.profile_a,2,' A')}. Battery ${num(c.soc,0,'%')} / ${num(c.temp,1,' °C')}. Last observed ${tsLabel(c.ts)}.</p>${s?`<p class="note">Session #${s.id}: ${esc(setup(s))}. <button data-charger-inspect="${s.id}">Inspect and label</button></p>`:''}${c.model?.fast_charge_reference_w?`<p class="note">${esc(c.model.name)}: Apple lists a ${num(c.model.fast_charge_reference_w,0,' W')} adapter with a suitable cable for fast charging. ${c.capability_w!=null&&c.capability_w<c.model.fast_charge_reference_w?'This source is below that reference; it can still charge the Mac.':''} <a href="https://support.apple.com/en-us/102378" target="_blank" rel="noopener noreferrer">Apple guidance</a></p>`:''}<details><summary>Source identity and available profiles</summary><p class="note">${c.identity_kind==='reported_identifier'?'macOS supplies an identifier, stored as a hash. It is not proof of authenticity.':'No unique identifier reported. Matching descriptors can belong to different physical chargers.'} Source fingerprint ${esc(c.source_key)}. Label each setup to distinguish adapter, cable and port.</p><p class="note">${(c.profiles||[]).map(p=>`${num(p.v,1,' V')} / ${num(p.a,2,' A')}`).join(' · ')||'No power profiles reported.'}</p></details>`:''}<p class="note">This evaluates observed charging behavior. It cannot certify electrical safety, cable quality or adapter authenticity.</p></section>`;
    }
    function history(d) {
      const s=selectedSession(d); if (s) selected=s.id;
      const filtered=d.sessions.filter(s=>!filter||[title(s),setup(s),String(s.id)].join(' ').toLowerCase().includes(filter.toLowerCase()));
      page=Math.min(page,Math.max(0,Math.ceil(filtered.length/10)-1));
      const rows=filtered.slice(page*10,page*10+10).map(s=>`<tr class="${s.id===selected?'selected-row':''}"><td><button data-charger-inspect="${s.id}" aria-pressed="${s.id===selected}">#${s.id} ${esc(title(s))}</button><div class="note">${esc(setup(s))}</div><div class="note">${esc(descriptor(s))}</div></td><td>${tsLabel(s.started)}<div class="note">${s.ended==null?(d.current?.session_id===s.id&&d.current?.assessment?.code!=='stale'?'Recording':'Awaiting collector'):'Closed'} · ${duration(s.observed_sec/60)} observed</div></td><td>${num(s.soc_start,0,'%')} to ${num(s.soc_end,0,'%')}<div class="note">${num(s.avg_charge_w,1,' W')} while charging</div></td><td>${wh(s.battery_in_wh)}<div class="note">${duration(s.deficit_sec/60)} battery assist</div></td></tr>`).join('');
      return `<section class="charger-panel"><h2>Connection history</h2><p class="note">Full sessions observed in the last ${d.days||30} days. Sleep gaps and source/profile changes start new sessions. A session is not a battery cycle.</p><div class="charger-toolbar"><label for="charger-days">History <select id="charger-days">${[7,30,90].map(n=>`<option value="${n}" ${n===(d.days||30)?'selected':''}>${n} days</option>`).join('')}</select></label><label for="charger-search">Find a setup <input id="charger-search" type="search" value="${esc(filter)}" placeholder="Adapter, cable, port or session"></label></div>${table(['Source and setup','Observed session','Battery charge','Energy into battery'],rows)}<div class="charger-toolbar"><button id="charger-prev" ${page===0?'disabled':''}>Previous</button><span>${filtered.length?`${page*10+1}-${Math.min(filtered.length,page*10+10)}`:'0'} of ${filtered.length} sessions${d.truncated?` (latest 500 of ${d.total_sessions})`:''}</span><button id="charger-next" ${(page+1)*10>=filtered.length?'disabled':''}>Next</button></div>${s?`<div class="charger-detail"><h3>Session #${s.id}: ${esc(title(s))}</h3><form id="charger-label-form" class="charger-labels">${[['adapter','Adapter name','Desk USB-C charger'],['cable','Cable','USB-C cable, 1 m'],['port','Port or dock','Left USB-C, direct']].map(([k,name,placeholder])=>`<label for="charger-${k}">${name}<input id="charger-${k}" name="${k}" maxlength="80" value="${esc(label(s)[k]||'')}" placeholder="${placeholder}"></label>`).join('')}<button type="submit">Save setup labels</button></form><p class="note">Labels are saved only in this browser. They do not identify future connections automatically.</p><p id="charger-label-status" role="status">${esc(message)}</p><div id="charger-session-detail" aria-live="polite"><p class="note">Loading session details...</p></div></div>`:'<p class="empty">Connect a charger to record your first session. Earlier battery history has no adapter identity.</p>'}</section>`;
    }
    function comparator(d) {
      const list=d.sessions;
      if(!list.some(s=>s.id===a))a=list[1]?.id||null;
      if(!list.some(s=>s.id===b))b=list[0]?.id||null;
      return `<section class="charger-panel"><h2>Compare two sessions</h2><p>See how two setups charged under similar conditions.</p><div class="charger-compare-controls"><label for="charger-a">A / Baseline<select id="charger-a"><option value="">Select a session</option>${list.map(s=>option(s,a)).join('')}</select></label><label for="charger-b">B / Alternative<select id="charger-b"><option value="">Select a session</option>${list.map(s=>option(s,b)).join('')}</select></label></div><div id="charger-comparison" aria-live="polite"><p class="note">${a&&b?'Comparing observed conditions...':'Record and select two sessions to compare.'}</p></div><details><summary>How to compare chargers or cables</summary><ol><li>Connect the first setup and label its adapter, cable and port in Connection history.</li><li>Use a steady, ordinary workload for 10-15 minutes while the battery is charging.</li><li>Change one part of the setup, label the new session and repeat at a similar charge level and temperature.</li><li>Select the two sessions above. The result uses only shared conditions and explains when evidence is missing.</li></ol><p class="note">Comparable intervals require 20-70% charge, 20-38 °C battery temperature, positive charging power and available input telemetry. Matching uses 10 percentage-point SOC bands, 2 °C battery-temperature bands and 5 W estimated system-load bands. System load is estimated as input power minus net battery power. These are comparison filters, not safety limits. Do not discharge the battery just to run a test.</p></details></section>`;
    }
    function patterns(d) {
      const groups=new Map();
      for(const s of d.sessions){
        const l=label(s),hasName=!!(l.adapter||l.cable||l.port);
        const key=JSON.stringify([s.source_key,hasName?l.adapter:'',hasName?l.cable||'':'',hasName?l.port||'':'']);
        if(!groups.has(key))groups.set(key,{source:s,n:0,sec:0,charge:0,hold:0,assist:0,wh:0,known:false,powerSec:0,assistSessions:0});
        const g=groups.get(key);g.n++;g.sec+=s.observed_sec||0;g.charge+=s.charge_sec||0;g.hold+=s.hold_sec||0;g.assist+=s.deficit_sec||0;g.powerSec+=s.power_observed_sec||0;g.assistSessions+=(s.deficit_sec>=540?1:0);
        if(s.battery_in_wh!=null){g.wh+=s.battery_in_wh;g.known=true;}
      }
      const rows=[...groups.values()].sort((a,b)=>b.sec-a.sec).map(g=>`<tr><td>${esc(title(g.source))}<div class="note">${esc(setup(g.source))}</div><div class="note">${Object.values(label(g.source)).some(Boolean)?'User-labelled setup':'Descriptor group; physical identity unknown'}</div></td><td>${g.n}<div class="note">${duration(g.sec/60)} observed</div></td><td>${duration(g.charge/60)}<div class="note">${wh(g.known?g.wh:null)} into battery</div></td><td>${duration(g.hold/60)}</td><td>${duration(g.assist/60)}<div class="note">${g.assistSessions} sessions with at least 9 min</div></td></tr>`).join('');
      const recurrent=[...groups.values()].filter(g=>g.assistSessions>=3);
      return `<section class="charger-panel"><h2>Usage patterns</h2><p class="note">Observed use by labelled setup and source profile. Unlabelled sources with identical descriptors share a provisional group. Charging duration alone does not establish which adapter is better.</p>${table(['Setup / source profile','Sessions','Charging','At native limit','Battery assists'],rows)}${recurrent.map(g=>`<p class="charger-observation">${esc(title(g.source))}: battery assistance recurred in ${g.assistSessions} sessions. Compare a different setup under matched load before attributing this to the adapter.</p>`).join('')}<p class="note">Energy covers measured intervals only. Battery assistance means discharge above 5 W, excluding a confirmed hold near the native limit. The Mac may intentionally draw on the battery. Raw readings are retained for 48 hours, minute traces for 90 days and session summaries long-term.</p></section>`;
    }
    function markup(d={}) {
      d={sessions:[],patterns:[],...d};
      if (!d.available) return `<div id="charger-widgets">${current(d)}</div>`;
      return `<div id="charger-widgets">${current(d)}${history(d)}${comparator(d)}${patterns(d)}</div>`;
    }
    async function bind(d, controls) {
      const {json,isCurrent,refresh,setDays}=controls;
      document.querySelectorAll('[data-charger-inspect]').forEach(el=>el.addEventListener('click',()=>{selected=Number(el.dataset.chargerInspect);message='';refresh();}));
      $('#charger-prev')?.addEventListener('click',()=>{page--;refresh();});
      $('#charger-next')?.addEventListener('click',()=>{page++;refresh();});
      $('#charger-days')?.addEventListener('change',e=>{page=0;setDays(Number(e.target.value));refresh();});
      $('#charger-search')?.addEventListener('change',e=>{filter=e.target.value;page=0;refresh();});
      $('#charger-label-form')?.addEventListener('submit',e=>{
        e.preventDefault();const s=selectedSession(d);if(!s)return;
        labels[String(s.id)]={adapter:$('#charger-adapter').value.trim().slice(0,80),cable:$('#charger-cable').value.trim().slice(0,80),port:$('#charger-port').value.trim().slice(0,80)};
        const persisted=save('chargerLabels',labels);message=persisted===false?'Browser storage is unavailable. Labels are kept for this page session only.':'Setup labels saved for this session in this browser.';refresh();
      });
      $('#charger-a')?.addEventListener('change',e=>{a=Number(e.target.value)||null;refresh();});
      $('#charger-b')?.addEventListener('change',e=>{b=Number(e.target.value)||null;refresh();});
      const tasks=[];
      const s=selectedSession(d);
      if(s)tasks.push((async()=>{
        try{
          const detail=await json(`/api/chargers/sessions/${s.id}`);
          if(!isCurrent()||selected!==s.id)return;
          const reason={observation_gap:'Observation gap (sleep or missing readings)',disconnected:'External power disconnected',source_or_profile_changed:'Source or power profile changed'};
          $('#charger-session-detail').innerHTML=`<p class="note">${tsLabel(detail.started)} to ${tsLabel(detail.last_ts)}. ${esc(reason[detail.end_reason]||'Session remains open')}. Coverage ${num(detail.coverage_pct,0,'%')} of this observed span.</p><div class="grid">${card('Battery received',wh(detail.battery_in_wh),`${duration(detail.power_observed_sec/60)} with battery power readings`)}${card('Battery supplied',wh(detail.battery_out_wh))}${card('Charging power',num(detail.avg_charge_w,1,' W'),'Average during positive charging intervals.')}${card('Battery temperature',num(detail.avg_temp_c,1,' °C'),`Peak ${num(detail.max_temp_c,1,' °C')}`)}</div>${detail.points?.length?canvas('charger-power','Selected session: input power and net battery power')+canvas('charger-soc','Selected session: battery charge level'):'<p class="note">Minute traces are not available yet or have expired. Session totals remain available.</p>'}<p class="note">Charts show minute observations${detail.points_truncated?'; only the latest 1,440 points are shown':''}. Input telemetry is an OS estimate. Net battery power is positive when charging and negative when supplying energy. No measurements are reconstructed through gaps.</p>`;
          if(detail.points?.length){
            chart('charger-power',[{label:'Into Mac (OS telemetry)',data:detail.points.map(p=>({x:p.minute,y:p.avg_input_w}))},{label:'Net battery power',data:detail.points.map(p=>({x:p.minute,y:p.avg_battery_w}))}],timeOptions('W'));
            chart('charger-soc',[{label:'Battery charge',data:detail.points.map(p=>({x:p.minute,y:p.soc}))}],timeOptions('%',{min:0,max:100}));
          }
        }catch(e){if(isCurrent())$('#charger-session-detail').innerHTML=`<p class="note">Session details unavailable: ${esc(e.message)}. Refresh to retry.</p>`;}
      })());
      if(a&&b)tasks.push((async()=>{
        try{
          const result=await json(`/api/chargers/compare?a=${a}&b=${b}`);
          if(!isCurrent())return;
          $('#charger-comparison').innerHTML=result.status==='comparable'?`<div class="charger-flow">${card('A / Matched charging power',num(result.a_w,1,' W'))}${card('B / Matched charging power',num(result.b_w,1,' W'))}${card('B compared with A',(result.delta_w>0?'+':'')+num(result.delta_w,1,' W'))}</div><p>${esc(result.reason)}</p><p class="note">${duration(result.matched_sec/60)} matched in each session across ${result.strata.length} shared condition bands. A eligible: ${duration(result.a_eligible_sec/60)}. B eligible: ${duration(result.b_eligible_sec/60)}. Small differences may be measurement variability.</p>`:`<p class="charger-observation">Comparison withheld</p><p>${esc(result.reason)}</p><p class="note">Shared conditions so far: ${duration(result.matched_sec/60)}. At least 5 minutes are needed.</p>`;
        }catch(e){if(isCurrent())$('#charger-comparison').innerHTML=`<p class="note">Comparison unavailable: ${esc(e.message)}. Refresh to retry.</p>`;}
      })());
      await Promise.all(tasks);
    }
    return {markup,bind};
  }};
})();
