// Тонкая обёртка над telegram-web-app.js. Вне Telegram (обычный браузер) всё
// работает без ошибок: нативные кнопки заменяются кнопками на странице.

type HapticStyle = "light" | "medium" | "heavy" | "rigid" | "soft";

interface BottomButton {
  setParams(p: Record<string, unknown>): BottomButton;
  show(): void;
  hide(): void;
  onClick(cb: () => void): void;
  offClick(cb: () => void): void;
}

interface WebApp {
  initData: string;
  initDataUnsafe: { start_param?: string };
  colorScheme: "light" | "dark";
  ready(): void;
  expand(): void;
  isVersionAtLeast(v: string): boolean;
  setHeaderColor(c: string): void;
  setBackgroundColor(c: string): void;
  setBottomBarColor?(c: string): void;
  disableVerticalSwipes?(): void;
  onEvent(event: string, cb: () => void): void;
  BackButton: { show(): void; hide(): void; onClick(cb: () => void): void; offClick(cb: () => void): void };
  MainButton: BottomButton;
  SecondaryButton?: BottomButton;
  HapticFeedback: {
    impactOccurred(s: HapticStyle): void;
    selectionChanged(): void;
    notificationOccurred(t: "success" | "error" | "warning"): void;
  };
  openTelegramLink(url: string): void;
  openLink(url: string): void;
  CloudStorage: {
    getItem(k: string, cb: (err: unknown, v?: string) => void): void;
    setItem(k: string, v: string, cb?: (err: unknown) => void): void;
  };
}

declare global {
  interface Window {
    Telegram?: { WebApp: WebApp };
  }
}

const raw = window.Telegram?.WebApp;
/** Есть только внутри Telegram: вне его скрипт тоже создаёт объект, но без initData. */
export const tg: WebApp | undefined = raw && raw.initData ? raw : undefined;
export const inTelegram = Boolean(tg);

const atLeast = (v: string) => Boolean(tg?.isVersionAtLeast(v));

export function initTelegram(): void {
  if (!tg) return;
  tg.ready();
  tg.expand();
  if (atLeast("7.7")) tg.disableVerticalSwipes?.();
}

export function colorScheme(): "light" | "dark" {
  if (tg) return tg.colorScheme;
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function onThemeChange(cb: () => void): void {
  tg?.onEvent("themeChanged", cb);
  window.matchMedia?.("(prefers-color-scheme: dark)").addEventListener?.("change", cb);
}

export function paintChrome(bg: string): void {
  if (!tg) return;
  try {
    tg.setHeaderColor(bg);
    tg.setBackgroundColor(bg);
    if (atLeast("7.10")) tg.setBottomBarColor?.(bg);
  } catch {
    /* старые клиенты */
  }
}

export const haptic = {
  tap: () => tg?.HapticFeedback.impactOccurred("light"),
  soft: () => tg?.HapticFeedback.impactOccurred("soft"),
  select: () => tg?.HapticFeedback.selectionChanged(),
  success: () => tg?.HapticFeedback.notificationOccurred("success"),
};

export function openUrl(url: string): void {
  if (tg) {
    if (/^https:\/\/t\.me\//.test(url)) tg.openTelegramLink(url);
    else tg.openLink(url);
  } else {
    window.open(url, "_blank", "noopener");
  }
}

export function startParam(): string | null {
  return (
    tg?.initDataUnsafe.start_param ??
    new URLSearchParams(location.search).get("tgWebAppStartParam") ??
    new URLSearchParams(location.search).get("startapp")
  );
}

// ------------------------------------------------------------ хранилище

const hasCloud = () => atLeast("6.9");

export function storageGet(key: string): Promise<string | null> {
  return new Promise((resolve) => {
    if (tg && hasCloud()) {
      tg.CloudStorage.getItem(key, (err, v) => resolve(err ? localGet(key) : v || localGet(key)));
    } else {
      resolve(localGet(key));
    }
  });
}

export function storageSet(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* приватный режим */
  }
  if (tg && hasCloud()) tg.CloudStorage.setItem(key, value);
}

function localGet(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

// ------------------------------------------------------------ нативные кнопки

export interface ButtonSpec {
  text: string;
  onClick: () => void;
}

export function setBackButton(onBack: (() => void) | null): () => void {
  if (!tg) return () => {};
  if (!onBack) {
    tg.BackButton.hide();
    return () => {};
  }
  tg.BackButton.onClick(onBack);
  tg.BackButton.show();
  return () => {
    tg.BackButton.offClick(onBack);
    tg.BackButton.hide();
  };
}

export function setBottomButtons(
  main: ButtonSpec | null,
  secondary: ButtonSpec | null,
  colors: { bg: string; ink: string },
): () => void {
  if (!tg) return () => {};
  const cleanups: (() => void)[] = [];
  const mount = (btn: BottomButton | undefined, spec: ButtonSpec | null, primary: boolean) => {
    if (!btn) return;
    if (!spec) {
      btn.hide();
      return;
    }
    btn.setParams({
      text: spec.text,
      color: primary ? colors.ink : colors.bg,
      text_color: primary ? colors.bg : colors.ink,
      is_visible: true,
      is_active: true,
      ...(primary ? {} : { position: "bottom" }),
    });
    btn.onClick(spec.onClick);
    cleanups.push(() => {
      btn.offClick(spec.onClick);
      btn.hide();
    });
  };
  mount(tg.MainButton, main, true);
  mount(atLeast("7.10") ? tg.SecondaryButton : undefined, secondary, false);
  return () => cleanups.forEach((c) => c());
}
