// Assign image URLs only near the viewport, including dynamically added cards.
const pendingGalleryImages = new Set();
const galleryImageObserver = typeof IntersectionObserver === 'function' ? new IntersectionObserver(entries => {
 entries.forEach(({target:img,isIntersecting})=>{if(!isIntersecting)return;galleryImageObserver.unobserve(img);pendingGalleryImages.delete(img);img.src=img.dataset.gallerySrc;delete img.dataset.gallerySrc;});
}, {rootMargin:'200px'}) : null;
function setGalleryImage(img,url){
 img.loading='lazy';img.decoding='async';
 if(!galleryImageObserver){img.src=url;return;}
 img.dataset.gallerySrc=url;pendingGalleryImages.add(img);galleryImageObserver.observe(img);
}
new MutationObserver(()=>{for(const img of pendingGalleryImages){if(!img.isConnected){galleryImageObserver.unobserve(img);pendingGalleryImages.delete(img);}}}).observe(document.body,{childList:true,subtree:true});
const views=[...document.querySelectorAll('.view')],state={runId:null,etag:null,poll:null,session:null,results:null,handlingPolicy:null,dataGroups:[],assignments:[],runRequest:0};
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
function runSourceButton(run){
 if(!run.source_url)return null;
 const link=document.createElement('a');link.className='button-link secondary';link.href=run.source_url;link.textContent='View Source';return link;
}
function appendRunEntry(root,run,detail){
 const row=document.createElement('div');row.className='recent-run-row';
 const link=document.createElement('a');link.className='recent-run';link.href=run.status_url;
 const title=document.createElement('strong');title.textContent=runLabel(run);
 const description=document.createElement('span');description.textContent=detail;link.append(title,description);
 row.append(link);const source=runSourceButton(run);if(source)row.append(source);root.append(row);
}
async function viewRunSource(id){
 clearTimeout(state.poll);++state.runRequest;show('subject');
 const root=document.querySelector('#subject-detail');root.replaceChildren();
 try{
  const {body:run}=await api(`/api/runs/${id}`);
  if(run.source?.source_id){location.replace(`/sources/${run.source.source_id}`);return;}
  const heading=document.createElement('h1');heading.textContent='Source';
  const message=document.createElement('p');message.textContent=run.outcome==='no_faces'?'No usable data was detected in this media.':'No subjects have been enrolled from this run.';
  const actions=document.createElement('div');actions.className='source-navigation';
  const back=document.createElement('a');back.className='button-link secondary';back.href=run.status_url;back.textContent='View Run';actions.append(back);
  const external=externalSourceLink(run.source?.page_url);if(external)actions.append(external);
  root.append(heading,message,actions);
 }catch(error){root.textContent=error.message;}
}
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
   appendRunEntry(root,run,`${new Date(run.created_at).toLocaleString()} · ${run.run_id}`);
  });
 }catch(_error){if(request===recentRequest)root.innerHTML='<p class="hint">Recent runs are temporarily unavailable.</p>';}
}
async function poll(){const request=state.runRequest;try{const{body:r}=await api(`/api/runs/${state.runId}`);if(request!==state.runRequest)return;state.handlingPolicy=r.handling_policy;show('status');document.querySelector('#state-label').textContent=r.state==='awaiting_face_selection'?'awaiting data selection':r.state.replaceAll('_',' ');document.querySelector('#status-detail').textContent=`Run ${r.run_id} · expires ${new Date(r.expires_at).toLocaleString()}`;document.querySelector('#cancel-run').hidden=!['awaiting_media','fetching','queued','detecting','awaiting_face_selection'].includes(r.state);if(r.state==='awaiting_face_selection')return groups();if(r.state==='succeeded')return results();if(['failed','cancelled','expired'].includes(r.state))return fail(new Error(r.error?.message||`Run ${r.state}.`),r.retryable);state.poll=setTimeout(poll,2500)}catch(e){if(request===state.runRequest)fail(e)}}
const profileRequests=new Map();
async function openBatch(id){
 clearTimeout(state.poll);const request=++state.runRequest;history.replaceState({},'',`/batches/${id}`);show('batch');
 const refresh=async()=>{try{
  const {body}=await api(`/api/profile-batches/${id}`);if(request!==state.runRequest)return;
  document.querySelector('#batch-title').textContent=`Videos from ${body.username}`;
  const status=body.status,finished=status.handoff_complete&&body.runs.every(r=>['succeeded','failed','cancelled','expired'].includes(r.state));
  document.querySelector('#batch-status').textContent=status.phase==='failed'?status.message:status.phase==='queued'?'Queued for processing…':status.phase==='discovering'?'Finding all videos…':`${status.selected||0} videos found · ${status.acquired||0} downloaded · ${body.runs.length} runs · ${finished?'Finished':status.handoff_complete?'Processing videos':'Submitting videos'}`;
  const failures=(status.acquisition_failures||0)+(body.discovery_failures||0);
  if(failures)document.querySelector('#batch-status').textContent+=` · ${failures} videos could not be acquired`;
  const list=document.querySelector('#batch-runs');list.replaceChildren();
  body.runs.forEach(run=>appendRunEntry(list,run,run.run_id));
  if(!finished&&status.phase!=='failed')state.poll=setTimeout(refresh,5000);
 }catch(error){if(request===state.runRequest){document.querySelector('#batch-status').textContent=error.message;state.poll=setTimeout(refresh,10000);}}};
 await refresh();
}
document.querySelector('#url-form').addEventListener('submit',async e=>{
 e.preventDefault();const button=e.currentTarget.querySelector('button');button.disabled=true;
 try{let url=document.querySelector('#source-url').value.trim();if(url.startsWith('/user/'))url='https://hotscope.tv'+url;
 const parsed=new URL(url);
 if(['hotscope.tv','www.hotscope.tv'].includes(parsed.hostname)&&parsed.pathname.startsWith('/user/')){
  const data=JSON.stringify({url,handling_policy:document.querySelector('input[name="handling-policy"]:checked').value});
  if(!profileRequests.has(data))profileRequests.set(data,crypto.randomUUID());
  const {body}=await api('/api/profile-batches',{method:'POST',headers:{'Idempotency-Key':profileRequests.get(data)},body:data});await openBatch(body.batch_id);
 }else openRun((await create({kind:'url',url})).run_id);
 }catch(error){fail(error);}finally{button.disabled=false;}
});
document.querySelector('#upload-form').addEventListener('submit',async e=>{e.preventDefault();const file=document.querySelector('#source-file').files[0];if(!file)return;const cancel=document.querySelector('#cancel-upload');try{state.runId=(await create({kind:'upload',content_type:file.type,bytes:file.size})).run_id;state.session=(await api(`/api/runs/${state.runId}/upload-session`,{method:'POST',body:'{}'})).body.session_uri;cancel.hidden=false;const upload=await fetch(state.session,{method:'PUT',headers:{'Content-Type':file.type},body:file});if(!upload.ok)throw new Error(`Upload failed (${upload.status})`);const digest=[...new Uint8Array(await crypto.subtle.digest('SHA-256',await file.arrayBuffer()))].map(x=>x.toString(16).padStart(2,'0')).join('');await api(`/api/runs/${state.runId}/upload-complete`,{method:'POST',body:JSON.stringify({bytes:file.size,sha256:digest})});state.session=null;openRun(state.runId)}catch(x){fail(x)}finally{cancel.hidden=true}});
document.querySelector('#cancel-upload').addEventListener('click',async()=>{if(state.session)await fetch(state.session,{method:'DELETE'}).catch(()=>{});if(state.runId)await api(`/api/runs/${state.runId}/cancel`,{method:'POST',body:'{}'}).catch(()=>{});state.session=null;show('submit')});
const durationLabel=range=>{if(!range)return'';const seconds=Math.max(0,range.end-range.start)/1000;return`${(range.start/1000).toFixed(1)}–${(range.end/1000).toFixed(1)}s · ${seconds.toFixed(1)}s track`};
const qualityLabel=quality=>{const count=quality?.observation_count;if(!count)return'';return`${count} observation${count===1?'':'s'}`};
async function groups(){const request=state.runRequest;try{const{body,response}=await api(`/api/runs/${state.runId}/face-groups`);if(request!==state.runRequest)return;state.etag=response.headers.get('ETag');state.dataGroups=body.groups;const root=document.querySelector('#data-items');root.replaceChildren();body.groups.forEach((g,i)=>{const label=document.createElement('label');label.className='data-item';label.innerHTML=`<input type="checkbox" value="${g.group_id}"><img alt="Data track ${i+1}"><strong>${g.time_range_ms?'Data track':'Data item'} ${i+1}</strong>`;setGalleryImage(label.querySelector('img'),g.preview_url);label.querySelector('input').checked=g.selected;const details=document.createElement('span');details.className='data-meta';details.textContent=[durationLabel(g.time_range_ms),qualityLabel(g.quality)].filter(Boolean).join(' · ')||'Image detection';label.append(details);root.append(label)});show('selection');window.setupEnrollmentAssignments?.()}catch(e){if(request===state.runRequest)fail(e)}}
const setAllData=checked=>document.querySelectorAll('#data-items input[type="checkbox"]').forEach(input=>{input.checked=checked});
document.querySelector('#select-all').addEventListener('click',()=>setAllData(true));document.querySelector('#clear-all').addEventListener('click',()=>setAllData(false));
document.querySelector('#selection-form').addEventListener('submit',async e=>{e.preventDefault();const group_ids=[...document.querySelectorAll('#data-items input:checked')].map(x=>x.value);if(!group_ids.length)return;const submit=document.querySelector('#selection-submit');submit.disabled=true;try{const selection=window.enrollmentSelection?await window.enrollmentSelection(group_ids):{group_ids};if(!selection)return;await api(`/api/runs/${state.runId}/face-selection`,{method:'PUT',headers:{'If-Match':state.etag},body:JSON.stringify(selection)});poll()}catch(x){fail(x)}finally{submit.disabled=false}});
async function results(){const request=state.runRequest;try{const data=(await api(`/api/runs/${state.runId}/results`)).body;if(request!==state.runRequest)return;state.results=data;renderResults()}catch(e){if(request===state.runRequest)fail(e)}}
const externalSourceLink=url=>{
 if(!/^https:\/\//i.test(url||''))return null;
 const link=document.createElement('a');link.className='button-link secondary external-source-link';link.href=url;link.target='_blank';link.rel='noopener noreferrer';link.title=url;
 link.append(document.createTextNode('Open Source'));
 const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('aria-hidden','true');svg.setAttribute('fill','none');svg.setAttribute('stroke','currentColor');svg.setAttribute('stroke-width','2');
 const path=document.createElementNS(svg.namespaceURI,'path');path.setAttribute('d','M14 3h7v7M10 14 21 3M21 14v7H3V3h7');svg.append(path);link.append(svg);return link;
};
const sourceLinks=urls=>{const wrap=document.createElement('div');wrap.className='source-links';[...new Set(urls||[])].forEach(url=>{const link=externalSourceLink(url);if(link)wrap.append(link)});return wrap};
function flattenResultActions(actions){
 const sources=actions.querySelector('.source-card-links'),external=actions.querySelector('.source-links');
 const inside=[...sources.children],outside=[...external.children];sources.remove();external.remove();
 for(let i=0;i<Math.max(inside.length,outside.length);i++){
  if(inside[i])actions.append(inside[i]);if(outside[i])actions.append(outside[i]);
 }
}
function renderResults(){
 const root=document.querySelector('#candidate-groups');root.replaceChildren();
 const source=state.results.source;
 if(source?.source_id||source?.page_url){
  const actions=document.createElement('div');actions.className='source-navigation';
  if(source.source_id){const link=document.createElement('a');link.className='button-link secondary';link.textContent='View Source';link.href=`/sources/${source.source_id}`;actions.append(link);}
  actions.append(sourceLinks(source.page_urls?.length?source.page_urls:[source.page_url]));root.append(actions);
 }
 if(state.results.outcome==='no_faces'){const p=document.createElement('p');p.textContent='No usable data was detected in this media.';root.append(p);}
 const enrollments=new Map(),enrollmentSources=new Map();
 if(state.results.enrollment_reused){const notice=document.createElement('p');notice.className='matching-status';notice.textContent='This video was already enrolled. Existing subjects are shown below; no new subjects or examples were created.';root.append(notice);}
 [...state.results.groups.map(g=>g.enrollment),...(state.results.reused_enrollments||[])].forEach(e=>{
  if(!e)return;
  if(!enrollmentSources.has(e.subject_id))enrollmentSources.set(e.subject_id,new Set());
  if(e.source_id)enrollmentSources.get(e.subject_id).add(e.source_id);
  if(!enrollments.has(e.subject_id))enrollments.set(e.subject_id,new Map());
  (e.representative_faces||[]).forEach(f=>enrollments.get(e.subject_id).set(f.representative_id,f));
 });
 document.querySelector('#results h2').textContent=enrollments.size?'Enrollment and matching results':'Candidate matches';
 const enrolledGrid=document.createElement('div');enrolledGrid.className='subject-grid enrolled-grid';
 if(enrollments.size)root.append(enrolledGrid);
 enrollments.forEach((faces,subjectId)=>{
  const section=document.createElement('section');section.className='enrolled-subject';
  const h=document.createElement('h3');h.textContent=`${state.results.enrollment_reused?'Existing subject':'Enrolled'} ${subjectId}`;
  const link=document.createElement('a');link.href=`/subjects/${subjectId}`;link.textContent='View Subject';link.className='button-link secondary';
  const gallery=document.createElement('div');gallery.className='representatives';
  faces.forEach(f=>{const img=document.createElement('img');setGalleryImage(img,f.url);img.alt='Enrolled data from this run';img.loading='lazy';gallery.append(img);});
  const actions=document.createElement('div');actions.className='result-actions';const sources=document.createElement('div');sources.className='source-card-links';actions.append(link,sources);
  enrollmentSources.get(subjectId).forEach(sourceId=>{const source=document.createElement('a');source.href=`/sources/${sourceId}`;source.textContent='View Source';source.className='button-link secondary';sources.append(source);});
  actions.append(sourceLinks(state.results.source?.page_urls?.length?state.results.source.page_urls:[state.results.source?.page_url]));
  flattenResultActions(actions);section.append(h,actions,gallery);
  if(!faces.size){const p=document.createElement('p');p.textContent='No gallery images are available for this enrollment.';section.append(p);}
  enrolledGrid.append(section);
 });
 if(state.results.handling_policy==='enroll_only'){
  const p=document.createElement('p');p.className='matching-status';p.textContent='Enroll Only: matching against existing subjects was not run. Open the enrolled subject to view potential matches.';root.append(p);
 }
 state.results.groups.forEach((g,i)=>{
  if(state.results.handling_policy==='enroll_only'&&!g.candidates.length)return;
  const h=document.createElement('h3');h.textContent=`Matches for selected data group ${i+1}`;root.append(h);
  if(!g.candidates.length){const p=document.createElement('p');p.textContent='No matching candidates were found.';root.append(p);}
  g.candidates.forEach(c=>{
   const a=document.createElement('article');a.className='candidate';const copy=document.createElement('div');copy.className='candidate-copy';
   const name=document.createElement('strong');name.textContent=c.display_name||'Unnamed subject';
   const score=document.createElement('p');score.textContent=`${(c.similarity*100).toFixed(1)}% similarity · rank ${c.rank}`;
   const gallery=document.createElement('div');gallery.className='representatives';
   c.representative_faces.forEach(f=>{const img=document.createElement('img');setGalleryImage(img,f.url);img.alt='Representative data image';gallery.append(img);});
   const link=document.createElement('a');link.className='button-link secondary';link.href=`/subjects/${c.resolved_subject_id||c.subject_id}`;link.textContent='View Subject';link.title=c.subject_id;
   const actions=document.createElement('div');actions.className='result-actions';const sources=document.createElement('div');sources.className='source-card-links';actions.append(link,sources,sourceLinks(c.page_urls?.length?c.page_urls:(c.sources||[]).flatMap(s=>s.page_urls?.length?s.page_urls:[s.page_url])));
   (c.sources||[]).forEach((source,index)=>{const button=document.createElement('a');button.href=`/sources/${source.source_id}`;button.className='button-link secondary';button.textContent=c.sources.length>1?`View Source ${index+1}`:'View Source';button.title=source.page_url||source.source_id;sources.append(button);});
   copy.append(name,score);
   if(c.subject_version_status==='changed'||c.subject_version_status==='unavailable'){const version=document.createElement('p');version.className='subject-version-note';version.textContent=c.resolved_subject_id&&c.resolved_subject_id!==c.subject_id?'Merged since this search · showing the current subject':c.subject_version_status==='changed'?'Subject changed since this search':'Prior subject version is unavailable';copy.append(version);}
   flattenResultActions(actions);copy.append(gallery,actions);a.append(copy);root.append(a);
  });
 });show('results');
}
document.querySelector('#check-form').addEventListener('submit',e=>{e.preventDefault();openRun(document.querySelector('#check-id').value)});document.querySelector('#copy-id').addEventListener('click',()=>navigator.clipboard.writeText(state.runId));const cancel=async()=>{try{await api(`/api/runs/${state.runId}/cancel`,{method:'POST',body:'{}'});poll()}catch(e){fail(e)}};document.querySelector('#cancel-run').addEventListener('click',cancel);document.querySelector('#selection-cancel').addEventListener('click',cancel);document.querySelector('#retry-run').addEventListener('click',async()=>{try{await api(`/api/runs/${state.runId}/retry`,{method:'POST',body:'{}'});poll()}catch(e){fail(e)}});document.querySelector('#back-results').addEventListener('click',()=>history.back());const startOver=()=>location.assign('/');document.querySelector('#error-home').addEventListener('click',startOver);document.querySelector('#new-run').addEventListener('click',startOver);document.querySelectorAll('nav button').forEach(b=>b.addEventListener('click',()=>b.dataset.view==='submit'?startOver():(history.pushState({},'', '/?view=check'),show('check'))));const saved=location.pathname.match(/^\/runs\/([0-9a-f-]{36})$/i);if(saved)openRun(saved[1]);else if(location.pathname==='/'&&new URLSearchParams(location.search).get('view')==='check')show('check');

const savedBatch=location.pathname.match(/^\/batches\/([0-9a-f-]{36})$/i);if(savedBatch)openBatch(savedBatch[1]);

const savedRunSource=location.pathname.match(/^\/runs\/([0-9a-f-]{36})\/source$/i);if(savedRunSource)viewRunSource(savedRunSource[1]);
