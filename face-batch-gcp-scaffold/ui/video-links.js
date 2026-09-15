function parseVideoLinks(text) {
  const links = new Set();
  for (const [index, raw] of text.split(/\r?\n/).entries()) {
    const line = raw.trim();
    if (!line || line.startsWith('#')) continue;
    let url;
    try { url = new URL(line); } catch (_) { throw new Error(`Line ${index + 1}: invalid URL.`); }
    if (url.protocol !== 'https:' || url.username || url.password || url.hash || url.port || /\s/.test(line)) {
      throw new Error(`Line ${index + 1}: use an HTTPS URL without credentials, fragments, or custom ports.`);
    }
    links.add(url.href);
    if (links.size > 10000) throw new Error('Use a list with at most 10,000 links.');
  }
  if (!links.size) throw new Error('The file contains no video links.');
  return [...links];
}

if (typeof module !== 'undefined') module.exports = {parseVideoLinks};
if (typeof document !== 'undefined') {
  const fileInput = document.querySelector('#video-links-file');
  const choice = document.querySelector('#video-links-choice');
  const select = document.querySelector('#video-links-select');
  const message = document.querySelector('#video-links-message');
  const source = document.querySelector('#source-url');
  let importVersion = 0;
  select.addEventListener('change', () => { source.value = select.value; });
  fileInput.addEventListener('change', async () => {
    const version = ++importVersion;
    choice.hidden = true;
    select.replaceChildren();
    message.textContent = '';
    const file = fileInput.files[0];
    if (!file) return;
    try {
      if (file.size > 2 * 1024 * 1024) throw new Error('Use a text file smaller than 2 MiB.');
      const links = parseVideoLinks(await file.text());
      if (version !== importVersion) return;
      links.forEach((url, index) => {
        const option = document.createElement('option');
        option.value = url;
        option.textContent = `${index + 1}. ${url}`;
        select.append(option);
      });
      source.value = links[0];
      choice.hidden = false;
      message.textContent = `${links.length} unique links loaded. Choose one to process.`;
    } catch (error) {
      if (version === importVersion) message.textContent = error.message;
    }
  });
}
