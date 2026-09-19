# Lazy gallery images

All gallery image creation paths now use setGalleryImage in app.js. An
IntersectionObserver assigns the image URL within 200px of the viewport, with
native lazy loading and asynchronous decoding. This covers selection previews,
run candidates/enrollments, subject cards, source previews and enrolled examples.
Detached pending images are unobserved to avoid retaining discarded page content.

Matching data and processing are unchanged: no change to detector input,
embeddings, candidates, scores, thresholds, enrollment or scan budgets. UI image
fetching is independent of matching. Less image traffic may ease shared request
contention but does not remove the 10-second synchronous source-scan budget.

A deterministic browser check rendered 50 candidates: fewer than ten images loaded
initially, the final image loaded after scrolling, all candidates and scores
remained present, and removing the page released pending observations.
