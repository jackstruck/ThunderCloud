const views=[...document.querySelectorAll('.view')],state={runId:null,etag:null,poll:null,session:null,results:null,handlingPolicy:null,faceGroups:[],assignments:[],runRequest:0};
const themeToggle=document.querySelector('#theme-toggle');
const setTheme=theme=>{document.documentElement.dataset.theme=theme;themeToggle.textContent=theme==='dark'?'Light theme':'Dark theme';themeToggle.setAttribute('aria-label',`Switch to ${theme==='dark'?'light':'dark'} theme`)};
setTheme(localStorage.getItem('thundercloud-theme')==='light'?'light':'dark');
themeToggle.addEventListener('click',()=>{const theme=document.documentElement.dataset.theme==='dark'?'light':'dark';localStorage.setItem('thundercloud-theme',theme);setTheme(theme)});
let recentRequest=0;
const show=id=>{views.forEach(v=>v.classList.toggle('active',v.id===id));if(['check','subjects','subject','submit'].includes(id)){clearTimeout(state.poll);++state.runRequest;}++recentRequest;if(id==='check')loadRecentRuns();};
const api=async(url,options={})=>{const response=await fetch(url,{credentials:'same-origin',...options,headers:{'Content-Type':'application/json',...(options.headers||{})}}),body=await response.json().catch(()=>null);if(!response.ok){const e=new Error(body?.message||`Request failed (${response.status})`);e.status=response.status;e.code=body?.code;throw e;}return{body,response}};
const fail=(e,retryable=false)=>{clearTimeout(state.poll);document.querySelector('#error-message').textContent=e.message;document.querySelector('#retry-run').hidden=!(retryable&&state.runId);show('error')};
const create=async source=>(await api('/api/runs',{method:'POST',headers:{'Idempotency-Key':crypto.randomUUID()+crypto.randomUUID()},body:JSON.stringify({handling_policy:document.querySelector('input[name="handling-policy"]:checked').value,source})})).body;
const openRun=id=>{clearTimeout(state.poll);++state.runRequest;state.runId=id;state.assignments=[];history.replaceState({},'',`/runs/${id}`);poll()};
const runLabel=run=>run.state.replaceAll('_',' ');
async function loadRecentRuns(){
 const root=document.querySelector('#recent-runs'),request=++recentRequest;
 root.innerHTML='<p class="hint">Loading recent runs…</p>';
 try{
  const{body}=await api('/api/runs/recent');
  if(request!==recentRequest)return;
  root.replaceChildren();
  if(!body.runs.length){root.innerHTML='<p class="hint">No recent runs.</p>';return;}
  body.runs.forEach(run=>{
   const link=document.createElement('a');link.className='recent-run';link.href=run.status_url;
   link.addEventListener('click',e=>{if(e.button===0&&!e.ctrlKey&&!e.metaKey&&!e.shiftKey&&!e.altKey){e.preventDefault();openRun(run.run_id);}});
   const title=document.createElement('strong');title.textContent=runLabel(run);
   const detail=document.createElement('span');detail.textContent=`${new Date(run.created_at).toLocaleString()} · ${run.run_id}`;
   link.append(title,detail);root.append(link);
  });
 }catch(_error){if(request===recentRequest)root.innerHTML='<p class="hint">Recent runs are temporarily unavailable.</p>';}
}
async function poll(){const request=state.runRequest;try{const{body:r}=await api(`/api/runs/${state.runId}`);if(request!==state.runRequest)return;state.handlingPolicy=r.handling_policy;show('status');document.querySelector('#state-label').textContent=r.state.replaceAll('_',' ');document.querySelector('#status-detail').textContent=`Run ${r.run_id} · expires ${new Date(r.expires_at).toLocaleString()}`;document.querySelector('#cancel-run').hidden=!['awaiting_media','fetching','queued','detecting','awaiting_face_selection'].includes(r.state);if(r.state==='awaiting_face_selection')return groups();if(r.state==='succeeded')return r.outcome==='no_faces'?noFaces():results();if(['failed','cancelled','expired'].includes(r.state))return fail(new Error(r.error?.message||`Run ${r.state}.`),r.retryable);state.poll=setTimeout(poll,2500)}catch(e){if(request===state.runRequest)fail(e)}}
document.querySelector('#url-form').addEventListener('submit',async e=>{e.preventDefault();try{openRun((await create({kind:'url',url:document.querySelector('#source-url').value})).run_id)}catch(x){fail(x)}});
document.querySelector('#upload-form').addEventListener('submit',async e=>{e.preventDefault();const file=document.querySelector('#source-file').files[0];if(!file)return;const cancel=document.querySelector('#cancel-upload');try{state.runId=(await create({kind:'upload',content_type:file.type,bytes:file.size})).run_id;state.session=(await api(`/api/runs/${state.runId}/upload-session`,{method:'POST',body:'{}'})).body.session_uri;cancel.hidden=false;const upload=await fetch(state.session,{method:'PUT',headers:{'Content-Type':file.type},body:file});if(!upload.ok)throw new Error(`Upload failed (${upload.status})`);const digest=[...new Uint8Array(await crypto.subtle.digest('SHA-256',await file.arrayBuffer()))].map(x=>x.toString(16).padStart(2,'0')).join('');await api(`/api/runs/${state.runId}/upload-complete`,{method:'POST',body:JSON.stringify({bytes:file.size,sha256:digest})});state.session=null;openRun(state.runId)}catch(x){fail(x)}finally{cancel.hidden=true}});
document.querySelector('#cancel-upload').addEventListener('click',async()=>{if(state.session)await fetch(state.session,{method:'DELETE'}).catch(()=>{});if(state.runId)await api(`/api/runs/${state.runId}/cancel`,{method:'POST',body:'{}'}).catch(()=>{});state.session=null;show('submit')});
const durationLabel=range=>{if(!range)return'';const seconds=Math.max(0,range.end-range.start)/1000;return`${(range.start/1000).toFixed(1)}–${(range.end/1000).toFixed(1)}s · ${seconds.toFixed(1)}s track`};
const qualityLabel=quality=>{const count=quality?.observation_count;if(!count)return'';return`${count} observation${count===1?'':'s'}`};
async function groups(){const request=state.runRequest;try{const{body,response}=await api(`/api/runs/${state.runId}/face-groups`);if(request!==state.runRequest)return;state.etag=response.headers.get('ETag');state.faceGroups=body.groups;const root=document.querySelector('#faces');root.replaceChildren();body.groups.forEach((g,i)=>{const label=document.createElement('label');label.className='face';label.innerHTML=`<input type="checkbox" value="${g.group_id}"><img alt="Face track ${i+1}" src="${g.preview_url}"><strong>${g.time_range_ms?'Face track':'Face'} ${i+1}</strong>`;label.querySelector('input').checked=g.selected;const details=document.createElement('span');details.className='face-meta';details.textContent=[durationLabel(g.time_range_ms),qualityLabel(g.quality)].filter(Boolean).join(' · ')||'Image detection';label.append(details);root.append(label)});show('selection');window.setupEnrollmentAssignments?.()}catch(e){if(request===state.runRequest)fail(e)}}
const setAllFaces=checked=>document.querySelectorAll('#faces input[type="checkbox"]').forEach(input=>{input.checked=checked});
document.querySelector('#select-all').addEventListener('click',()=>setAllFaces(true));document.querySelector('#clear-all').addEventListener('click',()=>setAllFaces(false));
document.querySelector('#selection-form').addEventListener('submit',async e=>{e.preventDefault();const group_ids=[...document.querySelectorAll('#faces input:checked')].map(x=>x.value);if(!group_ids.length)return;const submit=document.querySelector('#selection-submit');submit.disabled=true;try{const selection=window.enrollmentSelection?await window.enrollmentSelection(group_ids):{group_ids};if(!selection)return;await api(`/api/runs/${state.runId}/face-selection`,{method:'PUT',headers:{'If-Match':state.etag},body:JSON.stringify(selection)});poll()}catch(x){fail(x)}finally{submit.disabled=false}});
async function results(){const request=state.runRequest;try{const data=(await api(`/api/runs/${state.runId}/results`)).body;if(request!==state.runRequest)return;state.results=data;renderResults()}catch(e){if(request===state.runRequest)fail(e)}}
const sourceLinks=urls=>{const wrap=document.createElement('div');wrap.className='source-links';(urls||[]).forEach((url,index)=>{const link=document.createElement('a');if(!/^https:\/\//i.test(url))return;link.href=url;link.target='_blank';link.rel='noopener noreferrer';link.textContent=`Source page ${index+1}`;wrap.append(link)});return wrap};
function renderResults(){
 const root=document.querySelector('#candidate-groups');root.replaceChildren();
 const enrollments=new Map();
 state.results.groups.forEach(g=>{
  if(!g.enrollment)return;
  const e=g.enrollment;
  if(!enrollments.has(e.subject_id))enrollments.set(e.subject_id,new Map());
  (e.representative_faces||[]).forEach(f=>enrollments.get(e.subject_id).set(f.representative_id,f));
 });
 document.querySelector('#results h2').textContent=enrollments.size?'Enrollment and matching results':'Candidate matches';
 enrollments.forEach((faces,subjectId)=>{
  const section=document.createElement('section');section.className='enrolled-subject';
  const h=document.createElement('h3');h.textContent='Enrolled subject';
  const link=document.createElement('a');link.href=`/subjects/${subjectId}`;link.textContent=`View subject and potential matches · ${subjectId}`;
  const gallery=document.createElement('div');gallery.className='representatives';
  faces.forEach(f=>{const img=document.createElement('img');img.src=f.url;img.alt='Enrolled face from this run';img.loading='lazy';gallery.append(img);});
  section.append(h,link,gallery);
  if(!faces.size){const p=document.createElement('p');p.textContent='No gallery images are available for this enrollment.';section.append(p);}
  root.append(section);
 });
 if(state.results.handling_policy==='enroll_only'){
  const p=document.createElement('p');p.className='matching-status';p.textContent='Enroll Only: matching against existing subjects was not run. Open the enrolled subject to view potential matches.';root.append(p);
 }
 state.results.groups.forEach((g,i)=>{
  if(state.results.handling_policy==='enroll_only'&&!g.candidates.length)return;
  const h=document.createElement('h3');h.textContent=`Matches for selected face group ${i+1}`;root.append(h);
  if(!g.candidates.length){const p=document.createElement('p');p.textContent='No matching candidates were found.';root.append(p);}
  g.candidates.forEach(c=>{
   const a=document.createElement('article');a.className='candidate';const copy=document.createElement('div');copy.className='candidate-copy';
   const name=document.createElement('strong');name.textContent=c.display_name||'Unnamed subject';
   const score=document.createElement('p');score.textContent=`${(c.similarity*100).toFixed(1)}% similarity · rank ${c.rank}`;
   const gallery=document.createElement('div');gallery.className='representatives';
   c.representative_faces.forEach(f=>{const img=document.createElement('img');img.src=f.url;img.alt='Representative face';gallery.append(img);});
   const link=document.createElement('a');link.className='subject-result-link';link.href=`/subjects/${c.subject_id}`;link.textContent=`View subject · ${c.subject_id}`;
   copy.append(name,score);
   if(c.subject_version_status==='changed'||c.subject_version_status==='unavailable'){const version=document.createElement('p');version.className='subject-version-note';version.textContent=c.subject_version_status==='changed'?'Subject changed since this search':'Prior subject version is unavailable';copy.append(version);}
   copy.append(gallery,sourceLinks(c.page_urls),link);a.append(copy);root.append(a);
  });
 });show('results');
}
function noFaces(){document.querySelector('#candidate-groups').innerHTML='<p>No faces were detected in this media.</p>';show('results')}
document.querySelector('#check-form').addEventListener('submit',e=>{e.preventDefault();openRun(document.querySelector('#check-id').value)});document.querySelector('#copy-id').addEventListener('click',()=>navigator.clipboard.writeText(state.runId));const cancel=async()=>{try{await api(`/api/runs/${state.runId}/cancel`,{method:'POST',body:'{}'});poll()}catch(e){fail(e)}};document.querySelector('#cancel-run').addEventListener('click',cancel);document.querySelector('#selection-cancel').addEventListener('click',cancel);document.querySelector('#retry-run').addEventListener('click',async()=>{try{await api(`/api/runs/${state.runId}/retry`,{method:'POST',body:'{}'});poll()}catch(e){fail(e)}});document.querySelector('#back-results').addEventListener('click',()=>history.back());const startOver=()=>location.assign('/');document.querySelector('#error-home').addEventListener('click',startOver);document.querySelector('#new-run').addEventListener('click',startOver);document.querySelectorAll('nav button').forEach(b=>b.addEventListener('click',()=>b.dataset.view==='submit'?startOver():(history.pushState({},'', '/?view=check'),show('check'))));const saved=location.pathname.match(/^\/runs\/([0-9a-f-]{36})$/i);if(saved)openRun(saved[1]);else if(location.pathname==='/'&&new URLSearchParams(location.search).get('view')==='check')show('check');
