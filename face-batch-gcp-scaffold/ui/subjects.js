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
    const id = el('code', subject.subject_id, 'subject-id'), idRow = el('div', null, 'subject-id-row');
    const copy = button('⧉', async () => { try { await navigator.clipboard.writeText(subject.subject_id); copy.textContent = '✓'; copy.setAttribute('aria-label', 'ID copied'); } catch (_) { copy.title = 'Could not copy. Select the ID to copy manually.'; } }, 'link copy-id');
    copy.setAttribute('aria-label', 'Copy ID'); copy.title = 'Copy ID'; idRow.append(id, copy); root.append(idRow);
    root.append(el('p', `${subject.source_count} sources · ${subject.example_count ?? subject.observation_count} examples`, 'hint'));
    if (subject.resolved_from) root.append(el('p', `Combined from ${subject.resolved_from}`, 'hint'));
    if (pick) root.append(button('Choose subject', () => pick(subject)));
    else { const a = el('a', 'Open subject'); a.href = `/subjects/${subject.subject_id}`; a.dataset.subjectRoute = 'true'; root.append(a); }
    if (!pick && subject.source_count) {
      const actions = el('div', null, 'subject-card-actions'); actions.append(root.querySelector('a[data-subject-route]')); root.append(actions);
      const links = el('div', null, 'source-card-links'); actions.append(links);
      api(`/api/subjects/${subject.subject_id}/sources`).then(({ body }) => {
        body.sources.forEach(source => {
          const link = sourceLink(source.source_id);
          link.textContent = `Open Source (${source.subject_count} subjects)`;
          link.title = source.page_url || source.source_id;
          const line = el('p'); line.append(link); links.append(line);
        });
      }).catch(() => { links.append(button('Reload source links', () => { const replacement = subjectCard(subject, pick); root.replaceWith(replacement); })); });
    }
    return root;
  }

  function rangeSelection(selection, changed, message, limit = 50) {
    let anchor = null;
    const rows = [];
    const sync = () => rows.forEach(({ checkbox, subject }) => { checkbox.checked = selection.has(subject.subject_id); });
    const apply = (subjects, checked) => {
      const size = new Set([...selection.keys(), ...subjects.map(s => s.subject_id)]).size;
      if (checked && size > limit) { errorAt(message, new Error(`Select at most ${limit} subjects. Narrow your search or clear the selection.`)); sync(); return; }
      subjects.forEach(s => checked ? selection.set(s.subject_id, s) : selection.delete(s.subject_id));
      sync(); changed?.();
    };
    return { apply, add(checkbox, subject) {
      const index = rows.length; rows.push({ checkbox, subject });
      checkbox.addEventListener('click', event => {
        const range = event.shiftKey && anchor !== null ? rows.slice(Math.min(anchor, index), Math.max(anchor, index) + 1) : [rows[index]];
        apply(range.map(r => r.subject), checkbox.checked); anchor = index;
      });
    } };
  }

  function lookup(root, pick, options = {}) {
    root.replaceChildren();
    const label = el('label', 'Subject ID or display name');
    const input = el('input'); input.type = 'search'; input.placeholder = 'Paste a subject UUID or enter a display name'; input.value = options.query || ''; label.append(input);
    const filter = el('label', 'Multiple sources', 'subject-filter-inline'), multiple = el('input'); multiple.type = 'checkbox'; multiple.checked = !!options.multipleSources; filter.prepend(multiple);
    const sharedFilter = el('label', 'Multiple Subjects per Source', 'subject-filter-inline'), shared = el('input'); shared.type = 'checkbox'; shared.checked = !!options.sharedSource; sharedFilter.prepend(shared);
    const order = el('select');
    order.id = `subject-sort-${++lookupId}`;
    const orderText = el('label', 'Sort subjects'); orderText.htmlFor = order.id;
    // Separate label and select let the control shrink without wrapping its label.
    const orderControl = el('div', null, 'subject-sort-inline'); orderControl.append(orderText, order);
    [['id', 'Subject ID'], ['sources', 'Most sources first']].forEach(([value, text]) => { const option = el('option', text); option.value = value; order.append(option); });
    order.value = options.sort || 'id';
    const message = el('p', '', 'inline-message'); const cards = el('div', null, 'subject-grid'); const pages = el('div', null, 'pagination');
    let next = null, prior = [], cursor = options.cursor || null, serial = 0;
    let appliedParams = null;
    const selectAll = button('Select all', async () => {
      if (!appliedParams) return;
      const selectionSerial = serial; selectAll.disabled = true;
      try {
        const params = new URLSearchParams(appliedParams); params.delete('cursor'); params.set('limit', '100');
        const data = (await api(`/api/subjects?${params}`)).body;
        if (selectionSerial !== serial || !root.isConnected) return;
        const subjects = data.subjects.filter(s => s.subject_id !== options.exclude);
        const limit = options.exclude ? 49 : 50;
        if (data.next_cursor || new Set([...options.selection.keys(), ...subjects.map(s => s.subject_id)]).size > limit) throw new Error(`Select at most ${limit} subjects. Narrow your search or clear the selection.`);
        subjects.forEach(s => options.selection.set(s.subject_id, s));
        cards.querySelectorAll('input[data-merge-id]').forEach(c => { c.checked = options.selection.has(c.dataset.mergeId); }); options.onSelection?.();
      } catch (e) { if (selectionSerial === serial) errorAt(message, e); }
      finally { if (selectionSerial === serial) selectAll.disabled = false; }
    }); selectAll.disabled = true;
    async function search(reset = true) {
      const requestId = ++serial;
      if (reset) { cursor = null; prior = []; }
      const params = new URLSearchParams({ q: input.value.trim(), limit: '12' });
      if (options.filters) { params.set('multiple_sources', String(multiple.checked)); params.set('shared_source', String(shared.checked)); params.set('sort', order.value); } if (cursor) params.set('cursor', cursor);
      if (options.sourceId) params.set('source_id', options.sourceId);
      appliedParams = null; selectAll.disabled = true;
      cards.replaceChildren(); pages.replaceChildren(); message.textContent = 'Loading subjects…';
      try {
        const data = (await api(`/api/subjects?${params}`)).body;
        if (requestId !== serial) return;
        cards.replaceChildren(); next = data.next_cursor; appliedParams = params; selectAll.disabled = !data.subjects.length;
        const range = rangeSelection(options.selection, options.onSelection, message, options.exclude ? 49 : 50);
        data.subjects.filter(s => s.subject_id !== options.exclude).forEach(s => {
          const card = subjectCard(s, pick);
          if (options.selection) {
            const label = el('label', 'Select for merge', 'subject-filter-inline'), checkbox = el('input'); checkbox.type = 'checkbox'; checkbox.dataset.mergeId = s.subject_id; checkbox.checked = options.selection.has(s.subject_id);
            range.add(checkbox, s); label.prepend(checkbox); card.prepend(label);
          }
          cards.append(card); options.onCard?.(card, s);
        });
        message.textContent = cards.children.length ? '' : 'No subjects found.';
        pages.replaceChildren();
        if (prior.length || cursor) pages.append(button('Previous', () => { cursor = prior.pop() || null; search(false); }));
        if (next) pages.append(button('Next', () => { prior.push(cursor); cursor = next; search(false); }));
        options.onSearch?.(input.value.trim(), cursor, multiple.checked, order.value, shared.checked);
      } catch (e) { if (requestId === serial) { cards.replaceChildren(); errorAt(message, e); } }
    }
    input.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); search(); } });
    root.append(label);
    if (options.filters) { root.append(filter, sharedFilter, orderControl); shared.addEventListener('change', () => search()); multiple.addEventListener('change', () => search()); order.addEventListener('change', () => search()); }
    root.append(button('Search Subjects', () => search(), 'subject-search-button'));
    if (options.selection) {
      const tools = options.selectionTools || el('div', null, 'merge-tools');
      tools.append(selectAll, el('span', 'Select all matching results · Shift-click to select a range on this page.', 'hint selection-hint')); root.append(tools);
    }
    root.append(message, cards, pages);
    if (options.load) search(false);
    return { search, input };
  }

  function sourceLink(sourceId) {
    const link = el('a', 'Open Source');
    link.href = `/sources/${sourceId}`; return link;
  }

  async function reviewBulkMerge(subjects, defaultId, message, contextSource = null) {
    if (subjects.length < 2 || subjects.length > 50) { errorAt(message, new Error('Select between 2 and 50 subjects.')); return; }
    try {
      const sources = await Promise.all(subjects.map(s => api(`/api/subjects/${s.subject_id}/sources`).then(r => r.body)));
      if (sources.some((s, i) => s.subject_id !== subjects[i].subject_id || s.version !== subjects[i].version)) throw new Error('A selected subject changed. Refresh the selection before merging.');
      const contents = el('div'), field = el('label', 'Keep this subject and its identity details'), survivor = el('select');
      subjects.forEach(s => { const option = el('option', `${name(s)} · ${s.subject_id}`); option.value = s.subject_id; survivor.append(option); });
      survivor.value = defaultId; field.append(survivor); contents.append(field);
      const allSources = new Set(sources.flatMap(s => s.sources.map(r => r.source_id)));
      contents.append(el('p', `${subjects.length} subjects selected. All ${subjects.reduce((n, s) => n + s.example_count, 0)} examples from ${allSources.size} distinct sources will be merged.`));
      if (contextSource) contents.append(el('p', `This merges whole subjects, including ${[...allSources].filter(id => id !== contextSource).length} other sources.`));
      const cards = el('div', null, 'subject-grid'); subjects.forEach(s => cards.append(subjectCard(s))); contents.append(cards);
      if (!await confirmation('Merge selected subjects?', contents, 'Merge entire subjects')) return;
      const destination = subjects.find(s => s.subject_id === survivor.value);
      await mergeSubjects(subjects, destination);
    } catch (error) { errorAt(message, error); }
  }

  async function mergeSubjects(subjects, destination) {
      const result = (await mutate(`/api/subjects/${destination.subject_id}/bulk-combine`, 'POST', {
        version: destination.version,
        subjects: subjects.filter(s => s !== destination).map(s => ({ subject_id: s.subject_id, version: s.version })),
      })).body;
      await navigate(`/subjects/${result.destination.subject_id}`);
  }

  function bulkMergePanel(root, subject, initial = null) {
    const panel = el('section', null, 'correction-panel'), chosen = new Map();
    if (initial) chosen.set(initial.subject_id, initial);
    panel.append(el('h3', 'Select subjects to merge'), el('p', 'Search or browse, select matching subjects, then review one merge.'));
    const tray = el('div', null, 'selection-tools'), message = el('p', '', 'inline-message'), browser = el('div');
    function renderSelection() {
      tray.replaceChildren(el('strong', `${chosen.size} subjects selected to merge with this subject`));
      chosen.forEach(s => tray.append(button(`Remove ${name(s)} · ${s.subject_id.slice(0, 8)}`, () => { chosen.delete(s.subject_id); renderSelection(); browser.querySelectorAll('input[data-merge-id]').forEach(c => { c.checked = chosen.has(c.dataset.mergeId); }); })));
    }
    const review = button('Review selected merge', async () => {
      review.disabled = true;
      try { await reviewBulkMerge([subject, ...chosen.values()], subject.subject_id, message); }
      finally { review.disabled = false; }
    }, 'primary');
    panel.append(tray, browser, message, review, button('Close', () => panel.remove())); root.append(panel);
    lookup(browser, null, { load: true, exclude: subject.subject_id, selection: chosen, onSelection: renderSelection });
    renderSelection(); panel.scrollIntoView({ block: 'start', behavior: 'smooth' });
  }

  async function loadSources() {
    const requestId = ++detailRequest; show('subject');
    const root = document.querySelector('#subject-detail');
    root.replaceChildren(el('p', 'Library', 'eyebrow'), el('h1', 'Sources'), el('p', 'Browse videos and open a source to review or merge its subjects.', 'hint'));
    const params = new URLSearchParams(location.search);
    const label = el('label', 'Source ID or page URL'), input = el('input'); input.type = 'search'; input.value = params.get('q') || ''; label.append(input);
    const filter = el('label', 'Multiple subjects', 'subject-filter-inline'), multiple = el('input'); multiple.type = 'checkbox'; multiple.checked = params.get('multiple_subjects') === 'true'; filter.prepend(multiple);
    const cards = el('div', null, 'subject-grid'), message = el('p', '', 'inline-message'), pages = el('div', null, 'pagination');
    let cursor = params.get('cursor'), prior = [], serial = 0;
    async function search(reset = true) {
      const current = ++serial; if (reset) { cursor = null; prior = []; }
      cards.replaceChildren(); pages.replaceChildren(); message.textContent = 'Loading sources…';
      const query = new URLSearchParams(); if (input.value.trim()) query.set('q', input.value.trim()); if (multiple.checked) query.set('multiple_subjects', 'true'); if (cursor) query.set('cursor', cursor);
      try {
        const data = (await api(`/api/sources?${query}`)).body;
        if (current !== serial || requestId !== detailRequest) return;
        history.replaceState({}, '', '/sources' + (query.size ? `?${query}` : ''));
        data.sources.forEach(source => {
          const card = el('article', null, 'subject-card');
          card.append(el('code', source.source_id, 'subject-id'), el('strong', `${source.subject_count} subjects · ${source.example_count} examples`));
          if (source.page_url) { const pages = sourceLinks([source.page_url]); const link = pages.querySelector('a'); if (link) { link.textContent = source.page_url; link.style.overflowWrap = 'anywhere'; } card.append(pages); }
          const open = sourceLink(source.source_id); open.textContent = `Open Source (${source.subject_count} subjects)`; card.append(open); cards.append(card);
        });
        message.textContent = data.sources.length ? '' : 'No sources found.';
        if (cursor) pages.append(button('Previous', () => { cursor = prior.pop() || null; search(false); }));
        if (data.next_cursor) pages.append(button('Next', () => { prior.push(cursor); cursor = data.next_cursor; search(false); }));
      } catch (e) { if (current === serial && requestId === detailRequest) errorAt(message, e); }
    }
    multiple.addEventListener('change', () => search()); input.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); search(); } });
    root.append(label, filter, button('Search Sources', () => search(), 'subject-search-button'), message, cards, pages); await search(false);
  }

  async function loadSource(sourceId) {
    const requestId = ++detailRequest; show('subject');
    const root = document.querySelector('#subject-detail'); root.replaceChildren(el('p', 'Source', 'eyebrow'), el('h1', 'Subjects in this source'), el('code', sourceId, 'subject-id'));
    const selected = new Map(), tools = el('div', null, 'merge-tools'), count = el('strong', '0 subjects selected'), message = el('p', '', 'inline-message'), browser = el('div');
    const keepField = el('label', 'Keep subject'), survivor = el('select'); keepField.className = 'merge-destination'; keepField.append(survivor); survivor.setAttribute('aria-label', 'Keep subject'); keepField.hidden = true;
    const merge = button('Merge', async () => {
      const subjects = [...selected.values()], destination = selected.get(survivor.value);
      if (subjects.length < 2 || !destination) return;
      merge.disabled = true;
      try { await mergeSubjects(subjects, destination); }
      catch (error) { errorAt(message, error); }
      finally { merge.disabled = selected.size < 2; }
    }, 'primary'); merge.disabled = true;
    tools.append(count, keepField, merge, button('Clear selection', () => { selected.clear(); selectionChanged(); browser.querySelectorAll('input[data-merge-id]').forEach(c => { c.checked = false; }); }));
    tools.append(el('span', 'Merge combines all examples of the selected subjects, including other sources.', 'hint selection-hint'));
    function selectionChanged() {
      count.textContent = `${selected.size} subjects selected`; merge.disabled = selected.size < 2;
      const previous = survivor.value; survivor.replaceChildren();
      selected.forEach(subject => { const option = el('option', `${name(subject)} · ${subject.subject_id}`); option.value = subject.subject_id; survivor.append(option); });
      if (selected.has(previous)) survivor.value = previous; keepField.hidden = selected.size < 2;
    }
    root.append(browser, message);
    browser.classList.add('source-subject-browser');
    lookup(browser, null, { load: true, sourceId, selection: selected, selectionTools: tools, onSelection: selectionChanged, onCard: async (card, subject) => {
      try {
        const data = (await api(`/api/subjects/${subject.subject_id}/examples?source_id=${sourceId}&with_previews=true&limit=3`)).body;
        if (requestId !== detailRequest || !card.isConnected) return;
        const span = data.source_time_range;
        if (span?.start_ms !== null && span?.start_ms !== undefined && span.end_ms !== null) card.append(el('p', `${(span.start_ms / 1000).toFixed(1)}–${(span.end_ms / 1000).toFixed(1)} seconds`, 'hint source-time-range'));
        const pictures = card.querySelector('.representatives'); pictures.replaceChildren();
        data.examples.filter(e => e.preview_url).forEach(e => { const img = el('img'); img.src = e.preview_url; img.alt = `Example from this source of ${name(subject)}`; pictures.append(img); });
        if (!pictures.children.length) pictures.append(el('p', 'No preview available for this source.', 'hint'));
        const page = data.examples.find(e => e.page_url)?.page_url;
        if (page && !root.querySelector('.source-page-link')) { const link = sourceLinks([page]); link.classList.add('source-page-link'); browser.before(link); }
      } catch (_) { /* The subject remains available when source previews expire. */ }
    } });
  }

  function mergeRecovery(root, subject) {
    const panel = el('section', null, 'merge-recovery'), message = el('p', '', 'inline-message'), members = el('div');
    const open = button('Separate a previous merge', async () => {
      open.disabled = true;
      try {
        const data = (await api(`/api/subjects/${subject.subject_id}/merge-members`)).body;
        if (!panel.isConnected) return;
        if (data.version !== subject.version) throw new Error('This subject changed. Refresh before separating.');
        members.replaceChildren(); message.textContent = data.members.length ? 'Members from the latest 20 merges. You can also select examples below to separate them manually.' : 'No previous merge members available.';
        data.members.forEach(member => {
          const row = el('div', null, 'source-review-row');
          row.append(el('span', `${member.display_name || 'Unnamed subject'} · ${member.subject_id} · ${member.example_count} examples`));
          const separate = button('Separate back out', async () => {
            separate.disabled = true;
            try {
              const detail = el('p', `Restore these ${member.example_count} examples to their previous subject, keeping their original source links.`);
              if (!await confirmation('Separate this merge member?', detail, 'Separate subject')) return;
              const result = (await mutate(`/api/subjects/${subject.subject_id}/separate-merge`, 'POST', { version: subject.version, merge_operation_id: member.operation_id, member_subject_id: member.subject_id })).body;
              await navigate(`/subjects/${result.destination.subject_id}`);
            } catch (error) { errorAt(message, error); } finally { separate.disabled = false; }
          });
          separate.disabled = !member.can_separate; row.append(separate);
          if (!member.can_separate) row.append(el('p', 'Membership has changed; select examples below to separate manually.', 'hint'));
          members.append(row);
        });
      } catch (error) { errorAt(message, error); } finally { open.disabled = false; }
    });
    panel.append(open, message, members); root.append(panel);
  }

  function sourcePreview(subjectId, row) {
    const line = el('div', null, 'source-review-row');
    line.append(el('code', row.source_id, 'subject-id'), el('span', `${row.example_count} examples`), sourceLink(row.source_id));
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
      lookup(document.querySelector('#subject-browser'), null, { load: true, filters: true, multipleSources: params.get('multiple_sources') === 'true', sharedSource: params.get('shared_source') === 'true', sort: params.get('sort') || 'id', query: params.get('q') || '', cursor: params.get('cursor'), onSearch: (q, cursor, multiple, sort, shared) => {
        if (location.pathname !== '/subjects') return;
        const p = new URLSearchParams(); if (q) p.set('q', q); if (cursor) p.set('cursor', cursor); if (multiple) p.set('multiple_sources', 'true'); if (shared) p.set('shared_source', 'true'); if (sort !== 'id') p.set('sort', sort);
        history.replaceState({}, '', '/subjects' + (p.size ? `?${p}` : ''));
      } });
    } else if (location.pathname === '/sources') {
      await loadSources();
    } else {
      const match = location.pathname.match(/^\/subjects\/([0-9a-f-]{36})$/i);
      if (match) await loadSubject(match[1]);
      else if (/^\/sources\/[0-9a-f-]{36}$/i.test(location.pathname)) await loadSource(location.pathname.split('/')[2]);
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
    const heading = el('div', null, 'subject-heading'); heading.append(el('h1', name(subject)));
    root.append(el('p', 'Subject', 'eyebrow'), heading, subjectCard(subject));
    // The detail card already represents the open subject.
    root.querySelector('.subject-card a')?.remove();
    const message = el('p', '', 'inline-message'); root.append(message);
    if (subject.resolved_from) root.append(el('p', `The requested subject ${subject.resolved_from} was combined into ${subject.subject_id}.`, 'hint'));
    const form = el('form', null, 'subject-edit'); form.hidden = true; form.id = 'subject-identity-edit';
    const edit = button('✎', () => { form.hidden = !form.hidden; edit.setAttribute('aria-expanded', String(!form.hidden)); if (!form.hidden) form.querySelector('input').focus(); }, 'link edit-identity');
    edit.setAttribute('aria-label', 'Edit identity'); edit.setAttribute('aria-expanded', 'false'); edit.setAttribute('aria-controls', form.id); edit.title = 'Edit identity'; heading.append(edit);
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
    actions.append(button('Merge subjects', () => chooseCorrection('combine')));
    mergeRecovery(root, subject);
    const selected = new Set(), exampleRows = [], examplesRoot = el('div', null, 'subject-examples');
    const move = button('Move selected examples', () => chooseCorrection('move')); move.disabled = true; actions.append(move);
    const separate = button('Separate selected into new subject', () => chooseCorrection('move', null, true)); separate.disabled = true; actions.append(separate);
    const suggestions = el('section', null, 'potential-matches');
    suggestions.append(el('h2', 'Potential matches'), el('p', 'Similarity helps you choose subjects to review. It is not an identity probability.', 'hint'));
    const suggestionControls = el('div', null, 'management-actions');
    const suggestionStatus = el('p', '', 'inline-message'); suggestionStatus.setAttribute('aria-live', 'polite');
    const suggestionCards = el('div', null, 'subject-grid');
    let showingDismissed = false, suggestionRequest = 0;
    const mergeCandidates = new Map();
    const mergeMatches = button('Merge selected matches', async () => { mergeMatches.disabled = true; try { await reviewBulkMerge([subject, ...mergeCandidates.values()], subject.subject_id, suggestionStatus); } finally { mergeMatches.disabled = !mergeCandidates.size; } }); mergeMatches.disabled = true;
    const findMatches = button('Find potential matches', () => { showingDismissed = false; fetchSuggestions(); }, 'find-potential-matches');
    const showDismissed = button('Show dismissed', () => { showingDismissed = !showingDismissed; fetchSuggestions(); });
    suggestionControls.append(findMatches, showDismissed, mergeMatches);
    suggestions.append(suggestionControls, suggestionStatus, suggestionCards); root.append(suggestions);
    async function refreshComparison() {
      if (!suggestions.isConnected) return;
      await loadSubject(subject.subject_id);
      root.querySelector('.find-potential-matches')?.click();
    }
    async function fetchSuggestions() {
      const request = ++suggestionRequest;
      suggestionCards.replaceChildren(); mergeCandidates.clear(); mergeMatches.disabled = true; suggestionStatus.textContent = 'Loading potential matches…';
      findMatches.disabled = true; showDismissed.disabled = true;
      showDismissed.textContent = showingDismissed ? 'Show potential matches' : 'Show dismissed';
      try {
        const result = (await api(`/api/subjects/${subject.subject_id}/potential-matches?dismissed=${showingDismissed}`)).body;
        if (request !== suggestionRequest || !suggestions.isConnected) return;
        if (result.version !== subject.version) { await refreshComparison(); return; }
        suggestionStatus.textContent = result.candidates.length ? (showingDismissed ? 'Dismissed pairs' : 'Up to ten candidates, ordered by similarity') : (showingDismissed ? 'No dismissed pairs at the current subject versions.' : 'No potential matches found.');
        const range = rangeSelection(mergeCandidates, () => { mergeMatches.disabled = !mergeCandidates.size; }, suggestionStatus, 49);
        if (result.candidates.length) suggestionCards.append(button('Select all', () => range.apply(result.candidates, true)));
        for (const candidate of result.candidates) {
          const card = subjectCard(candidate);
          card.append(el('p', `Similarity: ${candidate.similarity.toFixed(4)}`));
          card.append(button('Review merge', () => chooseCorrection('combine', candidate)));
          const selectLabel = el('label', 'Select for merge', 'subject-filter-inline'), select = el('input'); select.type = 'checkbox';
          range.add(select, candidate); selectLabel.prepend(select); card.append(selectLabel);
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
      move.disabled = !selected.size; separate.disabled = !selected.size;
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
        const section = el('section', null, 'example-source'); const openSource = sourceLink(source); openSource.className = 'button-link secondary'; section.append(el('h3', `Source ${source}`));
        const controls = el('div', null, 'example-source-actions');
        controls.append(openSource, button("Select this source's examples", e => selectSource(source, e.currentTarget)), button('Clear source selection', () => { rows.forEach(r => selected.delete(r.example_id)); selectionChanged(); renderExamples(); })); section.append(controls);
        rows.forEach(row => {
          const label = el('label', null, 'example-row'); const checkbox = el('input'); checkbox.type = 'checkbox'; checkbox.value = row.example_id; checkbox.checked = selected.has(row.example_id);
          checkbox.addEventListener('change', () => { checkbox.checked ? selected.add(row.example_id) : selected.delete(row.example_id); selectionChanged(); });
          label.append(checkbox); if (row.preview_url) { const img = el('img'); img.src = row.preview_url; img.alt = 'Enrolled example'; img.loading = 'lazy'; label.append(img); }
          const text = el('div'); text.append(el('code', row.example_id, 'subject-id'));
          if (row.start_ms !== null) text.append(el('p', `${(row.start_ms / 1000).toFixed(1)}–${(row.end_ms / 1000).toFixed(1)} seconds`, 'hint'));
          if (row.run_id) { const a = el('a', `Run ${row.run_id}`); a.href = `/runs/${row.run_id}`; text.append(a); }
          if (row.processing_job_id) text.append(el('p', `Processing job ${row.processing_job_id}`, 'hint'));
          if (row.page_url) text.append(sourceLinks([row.page_url])); label.append(text); section.append(label);
        }); examplesRoot.append(section);
      });
    }
    async function chooseCorrection(action, candidate = null, separateNew = false) {
      if (action === 'combine') { if (candidate) await reviewBulkMerge([subject, candidate], subject.subject_id, message); else bulkMergePanel(root, subject); return; }
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
      if (separateNew) await review(null);
      else if (candidate) await review(candidate);
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
  features.then(enabled => {
    if (!enabled.subject_management) return;
    const nav = document.querySelector('nav');
    [['/subjects', 'Subjects'], ['/sources', 'Sources']].forEach(([href, text]) => { const link = el('a', text, 'link'); link.href = href; nav.append(link); });
  });
  document.addEventListener('click', e => {
    const link = e.target.closest('a[href="/subjects"], a[href="/sources"], a[href^="/subjects/"], a[href^="/sources/"]');
    if (!link || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey || e.button) return;
    e.preventDefault(); navigate(link.getAttribute('href'));
  });
  window.addEventListener('popstate', () => navigate(location.pathname + location.search, false));
  if (location.pathname.startsWith('/subjects') || location.pathname.startsWith('/sources')) navigate(location.pathname + location.search, false);
})();
