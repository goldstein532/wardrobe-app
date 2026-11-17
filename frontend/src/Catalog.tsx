import { useCallback, useMemo, useState } from "react";
import type { CatalogData, Item } from "./types";
import { back, go } from "./store";
import { haptic } from "./telegram";
import { BackControl, Grid, Sheet, SkeletonGrid, Tile, type SheetOption } from "./ui";

type Sort = "new" | "cheap" | "expensive";
type SheetKind = "category" | "brand" | "size" | "sort" | null;

const SORTS: SheetOption[] = [
  { value: "new", label: "Сначала новые" },
  { value: "cheap", label: "Сначала дешевле" },
  { value: "expensive", label: "Сначала дороже" },
];

function options(items: Item[], key: "category" | "brand" | "size", all: string): SheetOption[] {
  const counts = new Map<string, number>();
  for (const i of items) {
    const v = i[key];
    if (v) counts.set(v, (counts.get(v) ?? 0) + 1);
  }
  const sorted = [...counts.entries()].sort((a, b) =>
    key === "size" ? sizeOrder(a[0]) - sizeOrder(b[0]) || a[0].localeCompare(b[0]) : a[0].localeCompare(b[0]),
  );
  return [{ value: null, label: all }, ...sorted.map(([value, count]) => ({ value, label: value, count }))];
}

const LETTER = ["XXS", "XS", "S", "M", "L", "XL", "XXL", "XXXL"];
function sizeOrder(s: string): number {
  const n = parseFloat(s);
  if (!Number.isNaN(n)) return 100 + n;
  const i = LETTER.indexOf(s.toUpperCase());
  return i >= 0 ? i : 1000;
}

export function CatalogScreen({
  data,
  favCount,
  onOpen,
}: {
  data: CatalogData | null;
  favCount: number;
  onOpen: (id: number) => void;
}) {
  const [category, setCategory] = useState<string | null>(null);
  const [brand, setBrand] = useState<string | null>(null);
  const [size, setSize] = useState<string | null>(null);
  const [sort, setSort] = useState<Sort>("new");
  const [query, setQuery] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const [sheet, setSheet] = useState<SheetKind>(null);
  const closeSheet = useCallback(() => setSheet(null), []);

  // В витрине — только вещи в наличии (в пути, бронь и проданное не показываем).
  const items = useMemo(() => (data?.items ?? []).filter((i) => i.status === "available"), [data]);

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    const list = items.filter(
      (i) =>
        (!category || i.category === category) &&
        (!brand || i.brand === brand) &&
        (!size || i.size === size) &&
        (!q ||
          [i.brand, i.name, i.title, i.size, i.no, i.category, i.season]
            .filter(Boolean)
            .some((f) => String(f).toLowerCase().includes(q))),
    );
    if (sort === "cheap") list.sort((a, b) => (a.price ?? Infinity) - (b.price ?? Infinity));
    if (sort === "expensive") list.sort((a, b) => (b.price ?? -1) - (a.price ?? -1));
    return list;
  }, [items, category, brand, size, sort, query]);

  const filtered = Boolean(category || brand || size || query);
  const reset = () => {
    setCategory(null);
    setBrand(null);
    setSize(null);
    setQuery("");
  };

  return (
    <div className="screen">
      <header className="brandbar">
        <b>Nasonchikh</b>
        <div className="actions mono">
          <button
            onClick={() => {
              haptic.select();
              setSearchOpen((v) => !v);
            }}
            aria-label="Поиск"
          >
            Поиск
          </button>
          <button onClick={() => go("#/favorites")} aria-label="Избранное">
            ♡{favCount ? ` ${favCount}` : ""}
          </button>
        </div>
      </header>

      {searchOpen && (
        <div className="search">
          <input
            autoFocus
            type="search"
            placeholder="Бренд, вещь, размер, N°"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            enterKeyHint="search"
          />
        </div>
      )}

      <div className="stock mono">
        <span>В наличии</span>
        <span>{data ? String(items.length).padStart(2, "0") : "··"}</span>
      </div>

      <nav className="filters mono">
        <button className={category ? "on" : ""} onClick={() => setSheet("category")}>
          {category ?? "Категория"}
        </button>
        <button className={brand ? "on" : ""} onClick={() => setSheet("brand")}>
          {brand ?? "Бренд"}
        </button>
        <button className={size ? "on" : ""} onClick={() => setSheet("size")}>
          {size ? `Размер ${size}` : "Размер"}
        </button>
        <button onClick={() => setSheet("sort")}>{SORTS.find((s) => s.value === sort)?.label}</button>
      </nav>

      {!data ? (
        <SkeletonGrid />
      ) : shown.length ? (
        <Grid>
          {shown.map((i) => (
            <Tile key={i.id} item={i} onOpen={onOpen} />
          ))}
        </Grid>
      ) : (
        <div className="empty">
          <p className="mono">Ничего не найдено</p>
          {filtered && (
            <button className="mono link" onClick={reset}>
              Сбросить фильтры
            </button>
          )}
        </div>
      )}

      <footer className="foot mono">
        <span>Оригинал · любые проверки</span>
        <span>СПб / доставка</span>
      </footer>

      {sheet === "category" && (
        <Sheet title="Категория" options={options(items, "category", "Все категории")} value={category} onPick={setCategory} onClose={closeSheet} />
      )}
      {sheet === "brand" && (
        <Sheet title="Бренд" options={options(items, "brand", "Все бренды")} value={brand} onPick={setBrand} onClose={closeSheet} />
      )}
      {sheet === "size" && (
        <Sheet title="Размер" options={options(items, "size", "Все размеры")} value={size} onPick={setSize} onClose={closeSheet} />
      )}
      {sheet === "sort" && (
        <Sheet title="Сортировка" options={SORTS} value={sort} onPick={(v) => setSort((v as Sort) ?? "new")} onClose={closeSheet} />
      )}
    </div>
  );
}

export function ListScreen({
  title,
  note,
  items,
  empty,
  onOpen,
}: {
  title: string;
  note: string;
  items: Item[] | null;
  empty: string;
  onOpen: (id: number) => void;
}) {
  return (
    <div className="screen">
      <BackControl onBack={back} />
      <header className="brandbar">
        <b>{title}</b>
        <span className="mono sub">{note}</span>
      </header>
      {!items ? (
        <SkeletonGrid />
      ) : items.length ? (
        <Grid>
          {items.map((i) => (
            <Tile key={i.id} item={i} onOpen={onOpen} />
          ))}
        </Grid>
      ) : (
        <div className="empty">
          <p className="mono">{empty}</p>
          <button className="mono link" onClick={() => go("#/", true)}>
            В каталог
          </button>
        </div>
      )}
    </div>
  );
}
