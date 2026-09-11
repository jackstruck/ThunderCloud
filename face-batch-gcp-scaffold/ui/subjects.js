/* Subject management shares the existing console's authenticated API and theme. */
(() => {
  const el = (tag, text, cls) => { const n = document.createElement(tag); if (text !== null && text !== undefined) n.textContent = text; if (cls) n.className = cls; return n; };
  const button = (text, onClick, cls = 'secondary') => { const n = el('button', text, cls); n.type = 'button'; n.addEventListener('click', onClick); return n; };
  const errorAt = (root, error) => { root.textContent = error.message; root.setAttribute('role', 'alert'); };
  const name = s => s.display_name || 'Unnamed subject';
  const features = api('/api/features').then(r => r.body).catch(() => ({ subject_management: false, enrollment_grouping: false }));
  let detailRequest = 0, lookupId = 0;
  const operations = new Map();
  async function mutate(url, method, body) {
    delete body.operation_id;
    const key = JSON.stringify([url, method, body]);
    if (!operations.has(key)) operations.set(key, crypto.randomUUID());
    const controls = [...document.querySelectorAll('#subject-detail button, #subject-detail input, #subject-detail select')].filter(n => !n.disabled);
    controls.forEach(n => { n.disabled = true; });
    try { const response = await api(url, { method, body: JSON.stringify({ ...body, operation_id: operations.get(key) }) }); operations.delete(key); return response; }
    finally { controls.forEach(n => { if (n.isConnected) n.disabled = false; }); }
  }

  function gallery(subject) {
    const root = el('div', null, 'representatives');
    (subject.representative_faces || []).forEach(f => { const img = el('img'); img.src = f.url; img.alt = `Representative of ${name(subject)}`; img.loading = 'lazy'; root.append(img); });
    if (!root.children.length) root.append(el('p', 'No gallery images available.', 'hint'));
    return root;
  }
  function subjectCard(subject, pick) {
    const root = el('article', null, 'subject-card');
    root.append(gallery(subject), el('strong', name(subject)));
    const id = el('code', subject.subject_id, 'subject-id'); root.append(id);
    root.append(button('Copy ID', async e => { const control = e.currentTarget; try { await navigator.clipboard.writeText(subject.subject_id); control.textContent = 'Copied'; } catch (_) { id.focus(); } }, 'link'));
    root.append(el('p', `${subject.source_count} sources · ${subject.example_count ?? subject.observation_count} examples`, 'hint'));
    if (subject.resolved_from) root.append(el('p', `Combined from ${subject.resolved_from}`, 'hint'));
    if (pick) root.append(button('Choose subject', () => pick(subject)));
    else { const a = el('a', 'Open subject'); a.href = `/subjects/${subject.subject_id}`; a.dataset.subjectRoute = 'true'; root.append(a); }
    return root;
  }

  function lookup(root, pick, options = {}) {
    root.replaceChildren();
    const label = el('label', 'Subject ID or display name');
    const input = el('input'); input.type = 'search'; input.placeholder = 'Paste a subject UUID or enter a display name'; input.value = options.query || ''; label.append(input);
    const filter = el('label', 'Multiple sources', 'subject-filter-inline'), multiple = el('input'); multiple.type = 'checkbox'; multiple.checked = !!options.multipleSources; filter.prepend(multiple);
    const order = el('select');
    order.id = `subject-sort-${++lookupId}`;
    const orderText = el('label', 'Sort subjects'); orderText.htmlFor = order.id;
    // Separate label and select let the control shrink without wrapping its label.
    const orderControl = el('div', null, 'subject-sort-inline'); orderControl.append(orderText, order);
    [['id', 'Subject ID'], ['sources', 'Most sources first']].forEach(([value, text]) => { const option = el('option', text); option.value = value; order.append(option); });
    order.value = options.sort || 'id';
    const message = el('p', '', 'inline-message'); const cards = el('div', null, 'subject-grid'); const pages = el('div', null, 'selection-tools');
    let next = null, prior = [], cursor = options.cursor || null, serial = 0;
    async function search(reset = true) {
      const requestId = ++serial;
      if (reset) { cursor = null; prior = []; }
      const params = new URLSearchParams({ q: input.value.trim(), limit: '12' });
      if (options.filters) { params.set('multiple_sources', String(multiple.checked)); params.set('sort', order.value); } if (cursor) params.set('cursor', cursor);
      message.textContent = 'Loading subjects…';
      try {
        const data = (await api(`/api/subjects?${params}`)).body;
        if (requestId !== serial) return;
        cards.replaceChildren(); next = data.next_cursor;
        data.subjects.filter(s => s.subject_id !== options.exclude).forEach(s => cards.append(subjectCard(s, pick)));
        message.textContent = cards.children.length ? '' : 'No subjects found.';
        pages.replaceChildren();
        if (prior.length || cursor) pages.append(button('Previous', () => { cursor = prior.pop() || null; search(false); }));
        if (next) pages.append(button('Next', () => { prior.push(cursor); cursor = next; search(false); }));
        options.onSearch?.(input.value.trim(), cursor, multiple.checked, order.value);
      } catch (e) { if (requestId === serial) { cards.replaceChildren(); errorAt(message, e); } }
    }
    input.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); search(); } });
    root.append(label);
    if (options.filters) { root.append(filter, orderControl); multiple.addEventListener('change', () => search()); order.addEventListener('change', () => search()); }
    root.append(button('Search Subjects', () => search(), 'subject-search-button'), message, cards, pages);
    if (options.load) search(false);
    return { search, input };
  }

  function sourcePreview(subjectId, row) {
    const line = el('div', null, 'source-review-row');
    line.append(el('code', row.source_id, 'subject-id'), el('span', `${row.example_count} examples`));
    if (row.page_url) line.append(sourceLinks([row.page_url]));
    const examples = el('div'), message = el('p', '', 'hint'); let cursor = null;
    const load = button('Review examples', async () => {
      load.disabled = true; message.textContent = 'Loading examples…';
      try {
        const params = new URLSearchParams({ source_id: row.source_id, limit: '20' });
        if (cursor) params.set('cursor', cursor);
        const data = (await api(`/api/subjects/${subjectId}/examples?${params}`)).body;
        data.examples.forEach(example => {
          const item = el('div', null, 'review-example');
          if (example.preview_url) { const img = el('img'); img.src = example.preview_url; img.alt = 'Source example'; img.loading = 'lazy'; item.append(img); }
          item.append(el('code', example.example_id, 'subject-id'));
          if (example.start_ms !== null) item.append(el('span', `${(example.start_ms / 1000).toFixed(1)}–${(example.end_ms / 1000).toFixed(1)} seconds`));
          examples.append(item);
        });
        cursor = data.next_cursor; load.hidden = !cursor; load.textContent = 'Load more source examples'; message.textContent = '';
      } catch (e) { errorAt(message, e); } finally { load.disabled = false; }
    });
    line.append(load, message, examples); return line;
  }

  function confirmation(title, contents, accept = 'Confirm') {
    return new Promise(resolve => {
      const dialog = el('dialog', null, 'subject-dialog');
      dialog.append(el('h2', title), contents);
      const actions = el('div', null, 'dialog-actions'); let confirmed = false;
      actions.append(button('Cancel', () => dialog.close()), button(accept, () => { confirmed = true; dialog.close(); }, 'primary'));
      dialog.append(actions); document.body.append(dialog);
      dialog.addEventListener('close', () => { dialog.remove(); resolve(confirmed); }, { once: true });
      dialog.showModal();
    });
  }
  async function navigate(path, push = true) {
    clearTimeout(state.poll);
    ++detailRequest;
    if (push) history.pushState({}, '', path);
    if (location.pathname === '/subjects') {
      show('subjects');
      const params = new URLSearchParams(location.search);
      lookup(document.querySelector('#subject-browser'), null, { load: true, filters: true, multipleSources: params.get('multiple_sources') === 'true', sort: params.get('sort') || 'id', query: params.get('q') || '', cursor: params.get('cursor'), onSearch: (q, cursor, multiple, sort) => {
        if (location.pathname !== '/subjects') return;
        const p = new URLSearchParams(); if (q) p.set('q', q); if (cursor) p.set('cursor', cursor); if (multiple) p.set('multiple_sources', 'true'); if (sort !== 'id') p.set('sort', sort);
        history.replaceState({}, '', '/subjects' + (p.size ? `?${p}` : ''));
      } });
    } else {
      const match = location.pathname.match(/^\/subjects\/([0-9a-f-]{36})$/i);
      if (match) await loadSubject(match[1]);
      else if (location.pathname.startsWith('/runs/')) { openRun(location.pathname.split('/')[2]); }
      else { show(new URLSearchParams(location.search).get('view') === 'check' ? 'check' : 'submit'); }
    }
  }

  async function loadSubject(id) {
    const requestId = ++detailRequest;
    show('subject'); const root = document.querySelector('#subject-detail'); root.replaceChildren(el('p', 'Loading subject…'));
    try {
      const s = (await api(`/api/subjects/${id}`)).body;
      if (requestId !== detailRequest) return;
      if ((await features).subject_management) renderSubject(root, s);
      else root.replaceChildren(subjectCard(s));
    } catch (e) { if (requestId === detailRequest) { root.replaceChildren(); const p = el('p'); errorAt(p, e); root.append(p); } }
  }

  function renderSubject(root, subject) {
    root.replaceChildren();
    root.append(el('p', 'Subject', 'eyebrow'), el('h1', name(subject)), subjectCard(subject));
    // The detail card already represents the open subject.
    root.querySelector('.subject-card a')?.remove();
    const message = el('p', '', 'inline-message'); root.append(message);
    if (subject.resolved_from) root.append(el('p', `The requested subject ${subject.resolved_from} was combined into ${subject.subject_id}.`, 'hint'));
    const form = el('form', null, 'subject-edit');
    const fields = {};
    [['display_name', 'Display name'], ['external_identity_ref', 'External identity reference']].forEach(([key, text]) => {
      const label = el('label', text); const input = el('input'); input.name = key; input.maxLength = 200; input.value = subject[key] || ''; label.append(input); form.append(label); fields[key] = input;
    });
    const save = el('button', 'Save details'); save.type = 'submit'; form.append(save);
    form.addEventListener('submit', async e => {
      e.preventDefault(); save.disabled = true;
      try {
        if (subject.shared_identity_subject_ids.length > 1) {
          const contents = el('div'); contents.append(el('p', 'These identity details are shared by the following subjects:'));
          const list = el('ul'); subject.shared_identity_subject_ids.forEach(sid => list.append(el('li', sid))); contents.append(list);
          if (!await confirmation('Update shared identity details?', contents, 'Save for these subjects')) return;
        }
        const body = { operation_id: crypto.randomUUID(), version: subject.version, identity_version: subject.identity_version, display_name: fields.display_name.value, external_identity_ref: fields.external_identity_ref.value };
        const result = (await mutate(`/api/subjects/${subject.subject_id}`, 'PATCH', body)).body;
        renderSubject(root, result);
      } catch (e) { errorAt(message, e); if (e.status === 409) message.append(button('Refresh subject', () => loadSubject(subject.subject_id))); } finally { save.disabled = false; }
    });
    root.append(form);
    const actions = el('div', null, 'management-actions'); root.append(actions);
    actions.append(button('Combine with another subject', () => chooseCorrection('combine')));
    const selected = new Set(), exampleRows = [], examplesRoot = el('div', null, 'subject-examples');
    const move = button('Move selected examples', () => chooseCorrection('move')); move.disabled = true; actions.append(move);
    const suggestions = el('section', null, 'potential-matches');
    suggestions.append(el('h2', 'Potential matches'), el('p', 'Similarity helps you choose subjects to review. It is not an identity probability.', 'hint'));
    const suggestionControls = el('div', null, 'management-actions');
    const suggestionStatus = el('p', '', 'inline-message'); suggestionStatus.setAttribute('aria-live', 'polite');
    const suggestionCards = el('div', null, 'subject-grid');
    let showingDismissed = false, suggestionRequest = 0;
    const findMatches = button('Find potential matches', () => { showingDismissed = false; fetchSuggestions(); }, 'find-potential-matches');
    const showDismissed = button('Show dismissed', () => { showingDismissed = !showingDismissed; fetchSuggestions(); });
    suggestionControls.append(findMatches, showDismissed);
    suggestions.append(suggestionControls, suggestionStatus, suggestionCards); root.append(suggestions);
    async function refreshComparison() {
      if (!suggestions.isConnected) return;
      await loadSubject(subject.subject_id);
      root.querySelector('.find-potential-matches')?.click();
    }
    async function fetchSuggestions() {
      const request = ++suggestionRequest;
      suggestionCards.replaceChildren(); suggestionStatus.textContent = 'Loading potential matches…';
      findMatches.disabled = true; showDismissed.disabled = true;
      showDismissed.textContent = showingDismissed ? 'Show potential matches' : 'Show dismissed';
      try {
        const result = (await api(`/api/subjects/${subject.subject_id}/potential-matches?dismissed=${showingDismissed}`)).body;
        if (request !== suggestionRequest || !suggestions.isConnected) return;
        if (result.version !== subject.version) { await refreshComparison(); return; }
        suggestionStatus.textContent = result.candidates.length ? (showingDismissed ? 'Dismissed pairs' : 'Up to ten candidates, ordered by similarity') : (showingDismissed ? 'No dismissed pairs at the current subject versions.' : 'No potential matches found.');
        for (const candidate of result.candidates) {
          const card = subjectCard(candidate);
          card.append(el('p', `Similarity: ${candidate.similarity.toFixed(4)}`));
          card.append(button('Review merge', () => chooseCorrection('combine', candidate)));
          const dismiss = button(showingDismissed ? 'Restore' : 'Dismiss', async () => {
            try {
              await mutate(`/api/subjects/${subject.subject_id}/potential-matches/${candidate.subject_id}/dismissal`, showingDismissed ? 'DELETE' : 'PUT', { version: result.version, target_version: candidate.version });
              if (suggestions.isConnected) await fetchSuggestions();
            } catch (error) {
              if (!suggestions.isConnected) return;
              if (error.status === 409) { await refreshComparison(); return; }
              errorAt(suggestionStatus, error);
            }
          });
          card.append(dismiss); suggestionCards.append(card);
        }
      } catch (error) {
        if (request !== suggestionRequest || !suggestions.isConnected) return;
        errorAt(suggestionStatus, error);
        suggestionStatus.append(button('Retry', fetchSuggestions));
      } finally {
        if (request === suggestionRequest && suggestions.isConnected) { findMatches.disabled = false; showDismissed.disabled = false; }
      }
    }
    root.append(el('h2', 'Enrolled examples'), examplesRoot);
    const more = button('Load more examples', () => fetchExamples(next)); root.append(more); let next = null;
    async function fetchExamples(cursor = null) {
      more.disabled = true;
      try {
        const params = new URLSearchParams({ limit: '30' }); if (cursor) params.set('cursor', cursor);
        const data = (await api(`/api/subjects/${subject.subject_id}/examples?${params}`)).body;
        const known = new Set(exampleRows.map(r => r.example_id)); exampleRows.push(...data.examples.filter(r => !known.has(r.example_id))); next = data.next_cursor; renderExamples(); more.hidden = !next;
      } catch (e) { errorAt(message, e); } finally { more.disabled = false; }
    }
    function selectionChanged() {
      move.disabled = !selected.size;
      move.textContent = selected.size ? `Move ${selected.size} selected examples` : 'Move selected examples';
    }
    async function selectSource(source, control) {
      control.disabled = true;
      try {
        const rows = []; let cursor = null;
        do {
          const params = new URLSearchParams({ limit: '100', source_id: source });
          if (cursor) params.set('cursor', cursor);
          const data = (await api(`/api/subjects/${subject.subject_id}/examples?${params}`)).body;
          rows.push(...data.examples); cursor = data.next_cursor;
          if (rows.length > 1000) throw new Error('This source has more than 1,000 examples. Select a smaller set of tracks.');
        } while (cursor);
        const current = (await api(`/api/subjects/${subject.subject_id}/sources`)).body;
        if (current.version !== subject.version) throw new Error('This subject changed. Refresh it before selecting this source.');
        const known = new Set(exampleRows.map(r => r.example_id));
        rows.forEach(row => { if (!known.has(row.example_id)) { exampleRows.push(row); known.add(row.example_id); } selected.add(row.example_id); });
        selectionChanged(); renderExamples();
      } catch (e) { errorAt(message, e); } finally { control.disabled = false; }
    }
    function renderExamples() {
      examplesRoot.replaceChildren(); const sources = new Map();
      exampleRows.forEach(row => { if (!sources.has(row.source_id)) sources.set(row.source_id, []); sources.get(row.source_id).push(row); });
      if (!sources.size) examplesRoot.append(el('p', 'This subject has no enrolled examples.', 'hint'));
      sources.forEach((rows, source) => {
        const section = el('section', null, 'example-source'); section.append(el('h3', `Source ${source}`));
        section.append(button("Select this source's examples", e => selectSource(source, e.currentTarget)), button('Clear source selection', () => { rows.forEach(r => selected.delete(r.example_id)); selectionChanged(); renderExamples(); }));
        rows.forEach(row => {
          const label = el('label', null, 'example-row'); const checkbox = el('input'); checkbox.type = 'checkbox'; checkbox.value = row.example_id; checkbox.checked = selected.has(row.example_id);
          checkbox.addEventListener('change', () => { checkbox.checked ? selected.add(row.example_id) : selected.delete(row.example_id); selectionChanged(); });
          label.append(checkbox); if (row.preview_url) { const img = el('img'); img.src = row.preview_url; img.alt = 'Enrolled example'; img.loading = 'lazy'; label.append(img); }
          const text = el('div'); text.append(el('code', row.example_id, 'subject-id'), el('p', row.model_version, 'hint'));
          if (row.start_ms !== null) text.append(el('p', `${(row.start_ms / 1000).toFixed(1)}–${(row.end_ms / 1000).toFixed(1)} seconds`, 'hint'));
          if (row.run_id) { const a = el('a', `Run ${row.run_id}`); a.href = `/runs/${row.run_id}`; text.append(a); }
          if (row.processing_job_id) text.append(el('p', `Processing job ${row.processing_job_id}`, 'hint'));
          if (row.page_url) text.append(sourceLinks([row.page_url])); label.append(text); section.append(label);
        }); examplesRoot.append(section);
      });
    }
    async function chooseCorrection(action, candidate = null) {
      const panel = el('div', null, 'correction-panel'); const title = action === 'combine' ? 'Choose the other subject' : 'Choose where to move these examples'; panel.append(el('h3', title));
      const result = el('div'); panel.append(result); actions.after(panel);
      const cancel = button('Close', () => panel.remove()); panel.append(cancel);
      let chosen = null, submitted = false;
      async function review(target) {
        chosen = target;
        let sourceInfo, targetInfo;
        try {
          [sourceInfo, targetInfo] = await Promise.all([
            api(`/api/subjects/${subject.subject_id}/sources`).then(r => r.body),
            target ? api(`/api/subjects/${target.subject_id}/sources`).then(r => r.body) : Promise.resolve(null),
          ]);
          if (sourceInfo.version !== subject.version || (target && targetInfo.version !== target.version)) {
            throw new Error('A subject changed. Refresh and review the current records before correcting them.');
          }
        } catch (e) { errorAt(message, e); panel.remove(); message.append(button('Refresh subject', () => loadSubject(subject.subject_id))); return; }
        const contents = el('div'); contents.append(el('p', action === 'combine' ? 'Review the subjects and choose which identity details to keep.' : `${selected.size} examples will move to the chosen subject.`), subjectCard(subject, () => {})); contents.querySelectorAll('button').forEach(b => b.remove());
        let survivor = subject;
        if (target) {
          const card = subjectCard(target, () => {}); card.querySelectorAll('button').forEach(b => b.remove()); contents.append(card);
          if (action === 'combine') {
            const field = el('label', 'Keep this subject and its identity details'); const select = el('select');
            [subject, target].forEach(s => { const option = el('option', `${name(s)} · ${s.subject_id} · ${s.external_identity_ref || 'No external reference'}`); option.value = s.subject_id; select.append(option); });
            select.addEventListener('change', () => { survivor = select.value === subject.subject_id ? subject : target; }); field.append(select); contents.append(field);
          }
        } else contents.append(el('p', 'A new subject will be created for the selected examples.'));
        const scope = el('section', null, 'correction-scope');
        const sourceUnion = new Set([...sourceInfo.sources, ...(targetInfo?.sources || [])].map(r => r.source_id));
        if (action === 'combine') {
          scope.append(el('strong', `All ${subject.example_count + target.example_count} examples from ${sourceUnion.size} distinct sources will be merged.`));
          scope.append(el('p', 'This includes every source in both subjects, including media outside the video you may be reviewing.'));
          [sourceInfo, targetInfo].forEach(info => {
            const details = el('details'); details.append(el('summary', `${info.subject_id}: all ${info.sources.length} sources`));
            info.sources.forEach(row => details.append(sourcePreview(info.subject_id, row)));
            scope.append(details);
          });
        } else {
          const selectedRows = exampleRows.filter(row => selected.has(row.example_id));
          const movingSources = new Map(); selectedRows.forEach(row => movingSources.set(row.source_id, (movingSources.get(row.source_id) || 0) + 1));
          if (selectedRows.length !== selected.size || selected.size > 1000) { errorAt(message, new Error('Select between 1 and 1,000 available examples.')); return; }
          const remainingSources = sourceInfo.sources.filter(row => row.example_count > (movingSources.get(row.source_id) || 0)).length;
          const destinationSources = new Set([...(targetInfo?.sources || []).map(r => r.source_id), ...movingSources.keys()]).size;
          scope.append(el('strong', `Only ${selected.size} selected examples from ${movingSources.size} sources will move.`));
          scope.append(el('p', `Original subject after move: ${subject.example_count - selected.size} examples, ${remainingSources} sources.`));
          scope.append(el('p', `Destination after move: ${(target?.example_count || 0) + selected.size} examples, ${destinationSources} sources.`));
          const details = el('details'); details.append(el('summary', 'Exact selected examples'));
          selectedRows.forEach(row => details.append(el('p', `${row.example_id} · source ${row.source_id}`, 'subject-id'))); scope.append(details);
        }
        contents.append(scope);
        if (!await confirmation(action === 'combine' ? 'Merge entire subjects?' : 'Move selected examples?', contents, action === 'combine' ? 'Merge entire subjects' : 'Move examples')) return;
        if (submitted) return; submitted = true;
        try {
          let url, body;
          if (action === 'combine') {
            const other = survivor.subject_id === subject.subject_id ? chosen : subject;
            url = `/api/subjects/${survivor.subject_id}/combine`; body = { operation_id: crypto.randomUUID(), version: survivor.version, other_subject_id: other.subject_id, target_version: other.version };
          } else {
            url = `/api/subjects/${subject.subject_id}/move-examples`; body = { operation_id: crypto.randomUUID(), version: subject.version, example_ids: [...selected], target_subject_id: target?.subject_id || null, target_version: target?.version || null };
          }
          const response = (await mutate(url, 'POST', body)).body;
          panel.remove(); await navigate(`/subjects/${response.destination.subject_id}`);
        } catch (e) { submitted = false; errorAt(message, e); panel.remove(); if (e.status === 409) message.append(button('Refresh subject', () => loadSubject(subject.subject_id))); }
      }
      if (action === 'move') panel.prepend(button('Create a new subject', () => review(null)));
      if (candidate) await review(candidate);
      else lookup(result, review, { exclude: subject.subject_id });
    }
    fetchExamples();
  }

  function selectedTracks() { return [...document.querySelectorAll('#faces input:checked')].map(n => n.value); }
  window.setupEnrollmentAssignments = async () => {
    const root = document.querySelector('#enrollment-assignments'); const enabled = await features;
    root.hidden = !enabled.enrollment_grouping || state.handlingPolicy === 'search_then_discard'; if (root.hidden) return;
    document.querySelector('#selection-submit').textContent = 'Review and enroll selected faces';
    root.replaceChildren(el('h3', 'Enrollment groups'), el('p', 'Group tracks from this submission as one person. Each group creates one new subject; each ungrouped selected track creates a separate new subject.', 'hint'));
    const message = el('p', '', 'inline-message'), editor = el('div'), list = el('div'); root.append(message);
    function addGroup(existing = null) {
      const members = new Set(existing ? existing.group_ids : selectedTracks());
      const occupied = new Set(state.assignments.filter(a => a !== existing).flatMap(a => a.group_ids));
      if (!members.size) { errorAt(message, new Error('Select at least one face track.')); return; }
      if ([...members].some(id => occupied.has(id))) { errorAt(message, new Error('Some selected tracks are already grouped. Edit that group or select other tracks.')); return; }
      editor.replaceChildren(el('p', 'Choose the tracks that share one subject.'));
      const membership = el('div', null, 'group-members');
      document.querySelectorAll('#faces input').forEach((source, index) => {
        if (occupied.has(source.value)) return;
        const label = el('label', `Face track ${index + 1}`), checkbox = el('input'); checkbox.type = 'checkbox'; checkbox.checked = members.has(source.value);
        checkbox.addEventListener('change', () => checkbox.checked ? members.add(source.value) : members.delete(source.value));
        label.prepend(checkbox); membership.append(label);
      });
      editor.append(membership);
      const apply = () => {
        if (!members.size) { errorAt(message, new Error('Choose at least one track for this group.')); return; }
        const updated = { assignment_id: existing?.assignment_id || crypto.randomUUID(), group_ids: [...members] };
        if (existing) state.assignments = state.assignments.map(a => a === existing ? updated : a);
        else state.assignments.push(updated);
        document.querySelectorAll('#faces input').forEach(n => { if (members.has(n.value)) n.checked = true; });
        editor.replaceChildren(); renderList();
      };
      editor.append(button('Create one new subject', () => apply()));
      editor.append(button('Cancel group changes', () => editor.replaceChildren()));
    }
    root.append(button('Group selected as one person', () => addGroup()), editor, list);
    function renderList() {
      list.replaceChildren(); message.textContent = '';
      state.assignments.forEach((a, i) => {
        const line = el('div', null, 'assignment-row');
        line.append(el('p', `Group ${i + 1}: ${a.group_ids.length} tracks → One new subject`));
        line.append(button('Edit group', () => addGroup(a)), button('Remove grouping', () => { state.assignments = state.assignments.filter(x => x !== a); renderList(); }));
        list.append(line);
      });
    }
    renderList();
  };
  window.enrollmentSelection = async group_ids => {
    const enabled = await features;
    if (!enabled.enrollment_grouping || state.handlingPolicy === 'search_then_discard') return { group_ids };
    const assignments = state.assignments.map(a => ({ assignment_id: a.assignment_id, group_ids: [...a.group_ids] }));
    if (assignments.some(a => a.group_ids.some(id => !group_ids.includes(id)))) throw new Error('A grouped track was deselected. Edit or remove its grouping before continuing.');
    const summary = el('div'); const assigned = new Set(assignments.flatMap(a => a.group_ids));
    state.assignments.forEach((a, i) => summary.append(el('p', `Group ${i + 1}: ${a.group_ids.length} tracks → One new subject`)));
    const singletons = group_ids.length - assigned.size;
    summary.append(el('p', `${singletons} ungrouped tracks → ${singletons} new subjects`));
    const total = assignments.length + singletons;
    summary.prepend(el('strong', `${group_ids.length} tracks → ${total} new ${total === 1 ? 'subject' : 'subjects'}`));
    if (!await confirmation('Review enrollment', summary, 'Start enrollment')) return null;
    return { group_ids, assignments };
  };
  document.addEventListener('click', e => {
    const link = e.target.closest('a[href^="/subjects/"]');
    if (!link || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey || e.button) return;
    e.preventDefault(); navigate(link.getAttribute('href'));
  });
  window.addEventListener('popstate', () => navigate(location.pathname + location.search, false));
  if (location.pathname.startsWith('/subjects')) navigate(location.pathname + location.search, false);
})();
