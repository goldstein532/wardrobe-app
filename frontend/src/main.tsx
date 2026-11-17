import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@fontsource/inter-tight/300.css";
import "@fontsource/inter-tight/400.css";
import "@fontsource/inter-tight/500.css";
import "@fontsource/ibm-plex-mono/400.css";
import "@fontsource/ibm-plex-mono/500.css";
import "./styles.css";
import { App } from "./App";
import { initTelegram } from "./telegram";

initTelegram();
checkForNewVersion();

/**
 * GitHub Pages и Telegram держат старую страницу в кэше до 10 минут и дольше.
 * При запуске сверяем, какой код опубликован сейчас, и если он новее — перезагружаемся один раз.
 */
function checkForNewVersion(): void {
  if (import.meta.env.DEV) return;
  const current = document.querySelector<HTMLScriptElement>('script[type="module"][src*="assets/index-"]')?.src;
  if (!current) return;
  fetch(`./?v=${Date.now()}`, { cache: "no-store" })
    .then((r) => r.text())
    .then((html) => {
      const latest = html.match(/assets\/index-[^"']+\.js/)?.[0];
      if (!latest || current.endsWith(latest)) return;
      let tried: string | null = null;
      try {
        tried = sessionStorage.getItem("reloaded-for");
        sessionStorage.setItem("reloaded-for", latest);
      } catch {
        /* без sessionStorage просто не перезагружаемся повторно в этой вкладке */
      }
      if (tried !== latest) location.reload();
    })
    .catch(() => {});
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
