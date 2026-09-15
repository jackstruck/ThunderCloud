const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {parseHTML} = require('linkedom');
const base = path.resolve(__dirname, '../../ui');
const sid='11111111-1111-1111-1111-111111111111', source='22222222-2222-2222-2222-222222222222';
const subject={subject_id:sid,display_name:'Example',source_count:1,example_count:1,version:1,shared_identity_subject_ids:[],representative_faces:[]};
const settle = async () => { for(let n=0;n<10;n++) await new Promise(resolve=>setImmediate(resolve)); };
function setup(route='/', responses={}) {
 const {window,document}=parseHTML(fs.readFileSync(path.join(base,'index.html'),'utf8'));
 const location=new URL(route,'https://console.example');
 const requests=[];
 const context=vm.createContext({document,window,location,URL,URLSearchParams,console,crypto:require('node:crypto').webcrypto,
  history:{replaceState(){},pushState(){},back(){}},localStorage:{getItem(key){return key==='thundercloud-private-gallery'&&responses.__privacy?'true':null},setItem(key,value){responses.__stored=[key,value]}},
  sessionStorage:{getItem(){return JSON.stringify(responses.__saved||{})},setItem(){}},
  setTimeout(){},clearTimeout(){},navigator:{clipboard:{writeText:async()=>{}}},
  fetch:async url=>{const u=new URL(url,location);requests.push(u);let body;
   if(responses[u.pathname])body=typeof responses[u.pathname]==='function'?responses[u.pathname](u):responses[u.pathname];
   else if(u.pathname==='/api/features')body={subject_management:true,source_merges_apply_enabled:false};
   else if(u.pathname==='/api/subjects')body={subjects:[subject],next_cursor:'next-page'};
   else if(u.pathname===`/api/subjects/${sid}`)body=subject;
   else if(u.pathname===`/api/subjects/${sid}/sources`)body={subject_id:sid,version:1,sources:[{source_id:source,subject_count:1,page_url:'https://media.example/video'}]};
   else if(u.pathname===`/api/subjects/${sid}/examples`)body={examples:[{example_id:'example',source_id:source,run_id:sid,start_ms:null,page_url:'https://media.example/video'}],next_cursor:null};
   else if(u.pathname==='/api/sources')body={sources:[{source_id:source,page_url:'https://media.example/video'}],next_cursor:'next-page'};
   else throw new Error('Unexpected fixture request '+u.pathname);
   return {ok:true,json:async()=>body,headers:{get(){return null}}};
  }});
 vm.runInContext(fs.readFileSync(path.join(base,'privacy.js'),'utf8'),context);
 vm.runInContext(fs.readFileSync(path.join(base,'app.js'),'utf8'),context);
 return {context,document,requests,loadSubjects(){vm.runInContext(fs.readFileSync(path.join(base,'subjects.js'),'utf8'),context)}};
}
test('run cards show internal source and subject buttons alongside origin links',()=>{
 const app=setup();
 const fixture={handling_policy:'retain_and_enroll',groups:[{enrollment:{subject_id:sid,source_id:source,representative_faces:[]},candidates:[{subject_id:sid,resolved_subject_id:source,rank:9,similarity:.5,representative_faces:[{url:'/preview'}],page_urls:['https://media.example/video'],sources:[{source_id:source}],subject_version_status:'changed'}]}]};
 vm.runInContext(`state.results=${JSON.stringify(fixture)};renderResults()`,app.context);
 assert.equal(app.document.querySelector('.enrolled-subject .result-actions a:last-child').getAttribute('href'),`/sources/${source}`);
 assert.equal(app.document.querySelector('.candidate .result-actions a.button-link').getAttribute('href'),`/subjects/${source}`);
 assert.match(app.document.querySelector('.subject-version-note').textContent,/Merged since/);
 assert.equal(app.document.querySelectorAll('.candidate .result-actions a').length,3);
});
test('source overview hides UUID search, promotes origin and keeps Next on the right',async()=>{
 const app=setup(`/sources/${source}`);app.loadSubjects();await settle();
 assert.equal(app.document.querySelector('.source-subject-browser input[type=search]'),null);
 assert.equal(app.document.querySelector('.source-subject-browser .subject-search-button'),null);
 assert.equal(app.document.querySelector('.source-origin a').textContent,'Open');
 assert.equal(app.document.querySelector('.pagination button').classList.contains('pagination-next'),true);
 assert.equal(app.document.querySelector('.pagination button').classList.contains('secondary'),true);
 assert.equal(app.document.querySelector('.source-heading h1').textContent,source);
 assert.equal(app.document.querySelector('.source-origin p'),null);
});
test('subject corrections are compact; run navigation is a button and Select all stays outside cards',async()=>{
 const app=setup(`/subjects/${sid}`,{[`/api/subjects/${sid}/potential-matches`]:{version:1,candidates:[{...subject,subject_id:source,similarity:.8}]}});
 app.loadSubjects();await settle();
 assert.deepEqual([...app.document.querySelectorAll('.subject-actions>button')].map(b=>b.textContent),['Merge','Move examples','New subject','Undo merge']);
 assert.equal(app.document.querySelector('.example-row .button-link').textContent,'View Run');
 app.document.querySelector('.find-potential-matches').click();await settle();
 assert.equal(app.document.querySelectorAll('.suggestion-selection-tools>button').length,2);
 assert.equal(app.document.querySelector('.potential-matches .subject-grid>button'),null);
});

test('Shift-click label selects and clears a range with a stable anchor',async()=>{
 const subjects=[1,2,3].map(n=>({...subject,subject_id:`00000000-0000-0000-0000-00000000000${n}`,source_count:0}));
 const app=setup(`/sources/${source}`,{'/api/subjects':{subjects,next_cursor:null}});app.loadSubjects();await settle();
 const boxes=[...app.document.querySelectorAll('input[data-merge-id]')];
 const clickLabel=(n,shift)=>{const event=new app.document.defaultView.Event('click',{bubbles:true,cancelable:true});event.shiftKey=shift;boxes[n].parentElement.dispatchEvent(event);};
 clickLabel(0,false);clickLabel(2,true);
 assert.deepEqual(boxes.map(b=>b.checked),[true,true,true]);
 clickLabel(1,true);
 assert.deepEqual(boxes.map(b=>b.checked),[false,false,true]);
});

test('group selection actions share a dedicated row outside the cards',async()=>{
 const plan={groups:[],threshold:.8,algorithm:'fixture',skipped:[],dismissed_pairs:[]};
 const app=setup(`/sources/${source}`,{__saved:{[source]:plan}});app.loadSubjects();await settle();
 assert.deepEqual([...app.document.querySelectorAll('.group-selection-actions>button')].map(b=>b.textContent),['Select all groups','Clear group selection']);
});

test('privacy toggle restores saved state and reveals images without replacing their URLs',()=>{
 const responses={__privacy:true};const app=setup('/',responses);
 const button=app.document.querySelector('#privacy-toggle');
 assert.equal(app.document.documentElement.dataset.privateGallery,'true');
 assert.equal(button.getAttribute('aria-pressed'),'true');
 assert.equal(button.nextElementSibling.id,'theme-toggle');
 const img=app.document.createElement('img');img.src='/gallery/new';app.document.body.append(img);
 button.click();assert.equal(app.document.documentElement.dataset.privateGallery,'false');
 assert.equal(button.getAttribute('aria-label'),'Hide gallery images');
 assert.equal(img.getAttribute('src'),'/gallery/new');
 assert.deepEqual(responses.__stored,['thundercloud-private-gallery','false']);
 button.click();assert.equal(button.getAttribute('aria-label'),'Reveal gallery images');
});

test('subject and source browse controls expose all new choices',async()=>{
 const app=setup('/subjects');app.loadSubjects();await settle();
 assert.deepEqual([...app.document.querySelectorAll('.subject-sort-inline option')].map(x=>x.value),['id','sources','examples','newest']);
 assert.deepEqual([...app.document.querySelectorAll('[aria-label="Source count"] option')].map(x=>x.value),['all','single','multiple']);
 assert.deepEqual([...app.document.querySelectorAll('[aria-label="Subject name"] option')].map(x=>x.value),['all','named','unnamed']);
 const sources=setup('/sources?origin=example.test');sources.loadSubjects();await settle();
 assert.deepEqual([...sources.document.querySelectorAll('[aria-label="Sort sources"] option')].map(x=>x.value),['id','subjects','examples','newest']);
 assert.equal(sources.requests.find(u=>u.pathname==='/api/sources').searchParams.get('origin'),'example.test');
});

test('duplicate enrollment displays existing subjects and a reuse notice',()=>{
 const app=setup();
 const fixture={handling_policy:'enroll_only',enrollment_reused:true,reused_enrollments:[{subject_id:sid,source_id:source,representative_faces:[{representative_id:'face',url:'/face'}]}],groups:[]};
 vm.runInContext(`state.results=${JSON.stringify(fixture)};renderResults()`,app.context);
 assert.match(app.document.querySelector('#candidate-groups').textContent,/no new subjects or examples/);
 assert.match(app.document.querySelector('.enrolled-subject h3').textContent,/Existing subject/);
 assert.equal(app.document.querySelector('.enrolled-subject img').getAttribute('src'),'/face');
});

test('source header shows every alias while keeping the original first',async()=>{
 const app=setup(`/sources/${source}`,{'/api/sources':{sources:[{source_id:source,page_url:'https://original.example/video',page_urls:['https://alias.example/video','https://original.example/video']}],next_cursor:null}});
 app.loadSubjects();await settle();
 const links=[...app.document.querySelectorAll('.source-origin a')];
 assert.deepEqual(links.map(a=>a.textContent),['Open','Open 2']);
 assert.deepEqual(links.map(a=>a.getAttribute('href')),['https://original.example/video','https://alias.example/video']);
});

test('applying matching groups clears manual selections and reloads current subjects',async()=>{
 let merged=false;
 const second={...subject,subject_id:'33333333-3333-3333-3333-333333333333'};
 const group={selected:true,members:[subject,second].map(s=>({...s,source_time_range:{start_ms:0,end_ms:1000}})),survivor_id:sid,minimum_similarity:.9,identity_conflicts:[]};
 const plan={groups:[group],threshold:.8,algorithm:'fixture',skipped:[],dismissed_pairs:[]};
 const app=setup(`/sources/${source}`,{
  __saved:{[source]:plan},
  '/api/features':{subject_management:true,source_merges_apply_enabled:true},
  '/api/subjects':()=>({subjects:merged?[{...subject,version:2}]:[subject,second],next_cursor:null}),
  [`/api/sources/${source}/merge-proposals/apply`]:()=>{merged=true;return {outcomes:[{status:'merged',result:{destination:{subject_id:sid}}}]};},
 });
 app.loadSubjects();await settle();
 const selectAll=[...app.document.querySelectorAll('.source-subject-browser button')].find(b=>b.textContent==='Select all');
 selectAll.click();await settle();
 assert.equal(app.document.querySelector('.merge-tools strong').textContent,'2 subjects selected');
 const apply=[...app.document.querySelectorAll('button')].find(b=>b.textContent==='Merge selected groups');
 apply.click();await settle();
 assert.equal(app.document.querySelector('.merge-tools strong').textContent,'0 subjects selected');
 assert.equal(app.document.querySelectorAll('.source-subject-browser input[data-merge-id]').length,1);
 assert.equal(app.document.querySelector('.merge-tools select').children.length,0);
 assert.ok(app.requests.filter(u=>u.pathname==='/api/subjects').length>=3);
});

test('excluded subjects and disabled examples offer restoration and block suggestions',async()=>{
 const app=setup(`/subjects/${sid}`,{
  [`/api/subjects/${sid}`]:{...subject,matching_excluded:true,active_example_count:0,matching_eligible:false},
  [`/api/subjects/${sid}/examples`]:{examples:[{example_id:'disabled',source_id:source,start_ms:null,disabled_reason:'not_face',preview_url:'/disabled-preview'}],next_cursor:null},
 });app.loadSubjects();await settle();
 assert.ok([...app.document.querySelectorAll('button')].some(b=>b.textContent==='Restore matching'));
 assert.equal(app.document.querySelector('.find-potential-matches').disabled,true);
 assert.match(app.document.querySelector('.example-disabled').textContent,/Disabled: not a face/);
 assert.equal(app.document.querySelector('.example-disabled img').getAttribute('src'),'/disabled-preview');
 assert.ok([...app.document.querySelectorAll('.example-disabled button')].some(b=>b.textContent==='Restore example'));
});

test('library forwards Include excluded and active examples expose disabling actions',async()=>{
 const list=setup('/subjects?include_excluded=true');list.loadSubjects();await settle();
 assert.equal(list.requests.find(u=>u.pathname==='/api/subjects').searchParams.get('include_excluded'),'true');
 const app=setup(`/subjects/${sid}`);app.loadSubjects();await settle();
 assert.deepEqual([...app.document.querySelectorAll('.example-status-actions button')].map(b=>b.textContent),['Not a face','Unusable face']);
 const box=app.document.querySelector('.example-row input');box.checked=true;box.dispatchEvent(new app.context.window.Event('change'));
 const disable=[...app.document.querySelectorAll('.example-status-controls button')].find(b=>b.textContent==='Disable selected');
 assert.equal(disable.disabled,false);
});
