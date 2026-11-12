import { useCallback, useEffect, useState } from "react";
import type { CatalogData, Item } from "./types";
import { storageGet, storageSet } from "./telegram";

// ------------------------------------------------------------ каталог

export function useCatalog() {
  const [data, setData] = useState<CatalogData | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    // Каталог обновляется раз в несколько минут — кэш сбрасываем поминутно.
    const bust = Math.floor(Date.now() / 60000);
    fetch(`catalog.json?v=${bust}`)
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then(setData)
      .catch(() => setError(true));
  }, []);

  return { data, error };
}

export function findItem(data: CatalogData, id: number): Item | undefined {
  return data.items.find((i) => i.id === id) ?? data.archive.find((i) => i.id === id);
}

// ------------------------------------------------------------ избранное

const FAV_KEY = "fav";

export function useFavorites() {
  const [ids, setIds] = useState<number[]>([]);

  useEffect(() => {
    storageGet(FAV_KEY).then((v) => {
      try {
        const parsed = JSON.parse(v || "[]");
        if (Array.isArray(parsed)) setIds(parsed.filter((x) => typeof x === "number"));
      } catch {
        /* битое значение — начинаем с пустого */
      }
    });
  }, []);

  const toggle = useCallback((id: number) => {
    setIds((prev) => {
      const next = prev.includes(id) ? prev.filter((x) => x !== id) : [id, ...prev];
      storageSet(FAV_KEY, JSON.stringify(next));
      return next;
    });
  }, []);

  return { ids, has: (id: number) => ids.includes(id), toggle };
}

// ------------------------------------------------------------ маршруты (hash)

export type Route =
  | { screen: "catalog" }
  | { screen: "archive" }
  | { screen: "favorites" }
  | { screen: "item"; id: number };

function parse(hash: string): Route {
  const m = hash.match(/^#\/item\/(\d+)/);
  if (m) return { screen: "item", id: Number(m[1]) };
  if (hash.startsWith("#/archive")) return { screen: "archive" };
  if (hash.startsWith("#/favorites")) return { screen: "favorites" };
  return { screen: "catalog" };
}

let depth = 0; // сколько переходов сделано внутри приложения

export function useRoute() {
  const [route, setRoute] = useState<Route>(() => parse(location.hash));
  useEffect(() => {
    const on = () => setRoute(parse(location.hash));
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
}

export function go(path: string, replace = false): void {
  if (replace) {
    location.replace(path);
  } else {
    depth += 1;
    location.hash = path.slice(1);
  }
}

export function back(): void {
  if (depth > 0) {
    depth -= 1;
    history.back();
  } else {
    location.replace("#/");
  }
}

// ------------------------------------------------------------ формат

const nf = new Intl.NumberFormat("ru-RU");
export const price = (n?: number | null) => (n == null ? "Цена по запросу" : `${nf.format(n)} ₽`);
