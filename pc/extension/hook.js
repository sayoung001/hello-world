// Runs in the page's own JS world. Mirrors every /api/news/list and
// /api/news/top-stories response the page receives to content.js via
// postMessage; the page itself is untouched.
(() => {
  const MARK = "__saveticker_collector__";
  const isNewsList = (url) => typeof url === "string" &&
    (url.includes("/api/news/list") || url.includes("/api/news/top-stories"));
  const emit = (url, data) => {
    if (data && Array.isArray(data.news_list)) {
      window.postMessage({ [MARK]: true, url, items: data.news_list }, location.origin);
    }
  };

  const origFetch = window.fetch;
  window.fetch = async function (input, init) {
    const res = await origFetch.apply(this, arguments);
    try {
      const url = typeof input === "string" ? input : input && input.url;
      if (isNewsList(url) && res.ok) res.clone().json().then((d) => emit(url, d)).catch(() => {});
    } catch (_) {}
    return res;
  };

  const origOpen = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function (method, url) {
    if (isNewsList(String(url))) {
      this.addEventListener("load", () => {
        try {
          if (this.status >= 200 && this.status < 300) {
            const d = this.responseType === "json" ? this.response : JSON.parse(this.responseText);
            emit(String(url), d);
          }
        } catch (_) {}
      });
    }
    return origOpen.apply(this, arguments);
  };
})();
