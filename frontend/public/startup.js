// Runs before the module graph, so a failed entry/chunk never leaves an empty page.
(() => {
  const panel = document.getElementById("app-startup");
  const root = document.getElementById("root");
  if (!panel || !root) return;
  let complete = false;
  const showFailure = () => {
    if (complete) return;
    panel.hidden = false;
    panel.setAttribute("role", "alert");
  };
  const observer = new MutationObserver(() => {
    if (!root.hasChildNodes()) return;
    complete = true;
    panel.hidden = true;
    observer.disconnect();
  });
  observer.observe(root, { childList: true });
  addEventListener("hc:startup-error", () => showFailure());
  addEventListener("vite:preloadError", () => showFailure());
  addEventListener("unhandledrejection", () => showFailure());
  addEventListener("error", (event) => {
    if (event.target instanceof HTMLScriptElement || event.target instanceof HTMLLinkElement || event instanceof ErrorEvent) {
      showFailure();
    }
  }, true);
})();
