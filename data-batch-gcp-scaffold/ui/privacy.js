(() => {
  const key = 'thundercloud-private-gallery';
  let hidden = false;
  try { hidden = localStorage.getItem(key) === 'true'; } catch (_) {}
  const apply = () => {
    document.documentElement.dataset.privateGallery = String(hidden);
    const button = document.querySelector('#privacy-toggle');
    if (button) {
      const label = hidden ? 'Reveal gallery images' : 'Hide gallery images';
      button.setAttribute('aria-pressed', String(hidden));
      button.setAttribute('aria-label', label);
      button.title = label;
    }
  };
  apply();
  const bind = () => {
    const button = document.querySelector('#privacy-toggle');
    if (!button) return;
    apply();
    button.addEventListener('click', () => {
      hidden = !hidden;
      apply();
      try { localStorage.setItem(key, String(hidden)); } catch (_) {}
    });
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bind, {once:true});
  else bind();
})();
