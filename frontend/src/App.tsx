import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CatalogScreen, ListScreen } from "./Catalog";
import { ItemScreen } from "./Item";
import { findItem, go, useCatalog, useFavorites, useRoute } from "./store";
import { colorScheme, onThemeChange, paintChrome, startParam } from "./telegram";
import type { Item } from "./types";

const THEMES = {
  light: { bg: "#ffffff", ink: "#000000" },
  dark: { bg: "#0b0b0b", ink: "#f2f2f2" },
};

function useTheme() {
  const [scheme, setScheme] = useState(colorScheme);
  useEffect(() => onThemeChange(() => setScheme(colorScheme())), []);
  useEffect(() => {
    document.documentElement.dataset.theme = scheme;
    paintChrome(THEMES[scheme].bg);
  }, [scheme]);
  return THEMES[scheme];
}

type Base = "catalog" | "archive" | "favorites";

export function App() {
  const colors = useTheme();
  const { data, error } = useCatalog();
  const fav = useFavorites();
  const route = useRoute();

  // Экран под карточкой товара: при открытии карточки он остаётся на месте вместе с прокруткой.
  const [base, setBase] = useState<Base>("catalog");
  useEffect(() => {
    if (route.screen !== "item") {
      setBase(route.screen);
      window.scrollTo(0, 0);
    }
  }, [route.screen]);

  // Deep link: startapp=item_<id> или post_<message_id>.
  const handledStart = useRef(false);
  useEffect(() => {
    if (!data || handledStart.current) return;
    handledStart.current = true;
    const sp = startParam();
    let id: number | undefined;
    const mi = sp?.match(/^item_(\d+)$/);
    const mp = sp?.match(/^post_(\d+)$/);
    if (mi) id = Number(mi[1]);
    if (mp) id = data.posts[`${data.meta.channel}/${mp[1]}`]?.[0];
    if (id && findItem(data, id)) go(`#/item/${id}`);
  }, [data]);

  const open = useCallback((id: number) => go(`#/item/${id}`), []);

  const favItems = useMemo<Item[] | null>(
    () => (data ? fav.ids.map((id) => findItem(data, id)).filter((i): i is Item => Boolean(i)) : null),
    [data, fav.ids],
  );

  const current = route.screen === "item" && data ? findItem(data, route.id) : undefined;

  if (error) {
    return (
      <div className="empty full">
        <p className="mono">Не удалось загрузить витрину</p>
        <button className="mono link" onClick={() => location.reload()}>
          Обновить
        </button>
      </div>
    );
  }

  return (
    <>
      {base === "catalog" && <CatalogScreen data={data} favCount={fav.ids.length} onOpen={open} />}
      {base === "archive" && (
        <ListScreen
          title="Архив"
          note={data ? `Продано · ${String(data.archive.length).padStart(2, "0")}` : ""}
          items={data?.archive ?? null}
          empty="Архив пока пуст"
          onOpen={open}
        />
      )}
      {base === "favorites" && (
        <ListScreen
          title="Избранное"
          note={favItems ? String(favItems.length).padStart(2, "0") : ""}
          items={favItems}
          empty="Здесь появятся вещи, отмеченные ♡"
          onOpen={open}
        />
      )}
      {current && (
        <ItemScreen
          key={current.id}
          item={current}
          admin={data!.meta.admin_username}
          fav={fav.has(current.id)}
          onFav={() => fav.toggle(current.id)}
          colors={colors}
        />
      )}
    </>
  );
}
