(async () => {
  // Optional overrides: window.videoLinkExportOptions = { maxPages: 1000 };
  const options = {
    maxPages: 1000, pauseMs: 700, timeoutMs: 30000, download: true,
    ...window.videoLinkExportOptions
  };
  for (const key of ['maxPages', 'pauseMs', 'timeoutMs']) {
    if (!Number.isSafeInteger(options[key]) || options[key] < (key === 'pauseMs' ? 0 : 1)) {
      throw new Error(`Invalid ${key}.`);
    }
  }
  const start = new URL(location.href);
  if (start.protocol !== 'https:' || start.pathname !== '/search' ||
      !start.searchParams.get('query')?.trim()) {
    throw new Error('Open an HTTPS search results page with a nonempty query first.');
  }
  if (window.videoLinkExport?.running) throw new Error('An export is already running.');

  const controller = new AbortController();
  const state = window.videoLinkExport = {
    running: true, complete: false, pages: 0, urls: [],
    stop: () => controller.abort()
  };
  const queue = [1], scheduled = new Set([1]), videos = new Set();
  const scope = value => {
    const url = new URL(value);
    url.hash = '';
    url.searchParams.delete('page');
    url.searchParams.sort();
    return url.href;
  };
  const wait = ms => new Promise((resolve, reject) => {
    const finish = () => {
      controller.signal.removeEventListener('abort', abort);
      resolve();
    };
    const timer = setTimeout(finish, ms);
    const abort = () => {
      clearTimeout(timer);
      controller.signal.removeEventListener('abort', abort);
      reject(new Error('Stopped by operator.'));
    };
    controller.signal.addEventListener('abort', abort, {once: true});
    if (controller.signal.aborted) abort();
  });

  try {
    while (queue.length && state.pages < options.maxPages) {
      if (controller.signal.aborted) throw new Error('Stopped by operator.');
      const page = queue.shift();
      const pageURL = new URL(start);
      pageURL.hash = '';
      pageURL.searchParams.set('page', page);
      const response = await fetch(pageURL, {
        credentials: 'same-origin', redirect: 'error',
        signal: AbortSignal.any([controller.signal, AbortSignal.timeout(options.timeoutMs)])
      });
      if (!response.ok) throw new Error(`Page ${page}: HTTP ${response.status}`);
      if (!response.headers.get('content-type')?.toLowerCase().includes('text/html')) {
        throw new Error(`Page ${page}: expected HTML.`);
      }
      const html = await response.text();
      if (html.length > 10000000) throw new Error(`Page ${page}: HTML is too large.`);
      const doc = new DOMParser().parseFromString(html, 'text/html');
      if (!doc.querySelector('input[name="query"]')) {
        throw new Error(`Page ${page}: unexpected page or access challenge.`);
      }
      for (const anchor of doc.querySelectorAll('a[href]')) {
        let url;
        try { url = new URL(anchor.getAttribute('href'), pageURL); }
        catch { continue; }
        if (url.origin !== start.origin || url.username || url.password) continue;
        const video = url.pathname.match(/^\/video\/([A-Za-z0-9_-]+)\/?$/);
        if (video) videos.add(`${start.origin}/video/${video[1]}`);
        if (scope(url) === scope(start)) {
          const rawPage = url.searchParams.get('page') || '1';
          const next = Number(rawPage);
          if (/^[1-9]\d*$/.test(rawPage) && Number.isSafeInteger(next) && !scheduled.has(next)) {
            scheduled.add(next);
            queue.push(next);
          }
        }
      }
      state.pages++;
      state.urls = [...videos];
      console.log(`Page ${page}: ${state.urls.length} unique video links collected.`);
      if (queue.length && state.pages < options.maxPages) await wait(options.pauseMs);
    }
    if (controller.signal.aborted) throw new Error('Stopped by operator.');
    if (queue.length) throw new Error(`Stopped at the ${options.maxPages}-page limit.`);
    state.complete = true;
  } catch (error) {
    state.error = controller.signal.aborted ? 'Stopped by operator.' : error.message;
    console.warn('Partial export:', state.error);
  } finally {
    state.running = false;
    state.urls = [...videos];
    if (options.download && state.urls.length) {
      const blob = new Blob([state.urls.join('\n') + '\n'], {type: 'text/plain'});
      const downloadURL = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = downloadURL;
      link.download = `video-links${state.complete ? '' : '-partial'}.txt`;
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(downloadURL), 10000);
    }
    console.log(`${state.complete ? 'Linked pages exhausted' : 'Partial'}: ${state.urls.length} links from ${state.pages} pages.`, state);
  }
  return {complete: state.complete, pages: state.pages, urls: state.urls, error: state.error};
})();
