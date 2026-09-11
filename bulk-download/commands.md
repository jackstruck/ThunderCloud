# Browser Console Commands

Open the HeyLink page in your browser, complete any interactive challenge, open the browser developer console, and run one of the commands below.

## Copy links in the downloader's input format

```javascript
const links = [...new Set(
  [...document.querySelectorAll('a[href]')]
    .map(a => {
      try {
        return new URL(a.href, location.href);
      } catch {
        return null;
      }
    })
    .filter(u =>
      u && ['justpaste.it', 'www.justpaste.it'].includes(u.hostname.toLowerCase())
    )
    .map(u => {
      u.hash = '';
      u.search = '';
      return u.href;
    })
)];

const output = links.join('\n');
console.log(output);
copy(output);
```

This produces exactly the expected file contents: one clean JustPaste URL per line, without JSON brackets, quotes, commas, fragments, or tracking query parameters. The `copy(...)` helper is supported by Chromium-based browser developer consoles. If it is unavailable, copy the logged text manually.

## Add the links to the downloader

Paste the copied text directly into `input/justpaste_urls.txt`. For example:

```text
https://justpaste.it/example-one
https://justpaste.it/example-two
```

Because the seeded HeyLink URL currently returns an access challenge, comment it out in `input/heylink_urls.txt` by adding `#` at the beginning of its line when using the direct JustPaste input.

## Run the outstanding backfill

Use [Local JustPaste backfill](../LOCAL_JUSTPASTE_BACKFILL.md) for current queue
counts, resumable discovery, sequential download/upload, and the separate
receipt-based face-processing handoff.
