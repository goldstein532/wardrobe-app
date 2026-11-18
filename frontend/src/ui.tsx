import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import type { Item, PhotoRef } from "./types";
import { price } from "./store";

/** Цена; при скидке — старая зачёркнута, рядом новая. */
export function Price({ item }: { item: Item }) {
  if (!item.old_price) return <>{price(item.price)}</>;
  return (
    <>
      <s className="old">{price(item.old_price)}</s> <span className="sale">{price(item.price)}</span>
    </>
  );
}
import { haptic, inTelegram, setBackButton, setBottomButtons, type ButtonSpec } from "./telegram";

// ------------------------------------------------------------ фото

export function Img({ photo, full, eager }: { photo?: PhotoRef; full?: boolean; eager?: boolean }) {
  const [loaded, setLoaded] = useState(false);
  if (!photo) return null;
  return (
    <img
      src={full ? photo.full : photo.thumb}
      width={photo.w || undefined}
      height={photo.h || undefined}
      loading={eager ? "eager" : "lazy"}
      decoding="async"
      alt=""
      draggable={false}
      className={loaded ? "img on" : "img"}
      onLoad={() => setLoaded(true)}
    />
  );
}

// ------------------------------------------------------------ появление при прокрутке

/** true, когда элемент впервые показался на экране. */
export function useReveal<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [shown, setShown] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el || shown) return;
    if (!("IntersectionObserver" in window)) {
      setShown(true);
      return;
    }
    const io = new IntersectionObserver(
      ([e]) => {
        if (e.isIntersecting) {
          setShown(true);
          io.disconnect();
        }
      },
      { rootMargin: "0px 0px -6% 0px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [shown]);
  return [ref, shown] as const;
}

// ------------------------------------------------------------ плитка-бирка

export function Tile({
  item,
  onOpen,
  muted,
  index = 0,
}: {
  item: Item;
  onOpen: (id: number) => void;
  muted?: boolean;
  index?: number;
}) {
  const transit = item.status === "transit";
  const sold = item.status === "sold";
  const [ref, shown] = useReveal<HTMLButtonElement>();
  // Лесенка: соседние колонки появляются с небольшим сдвигом, первые ряды — сверху вниз.
  const delay = (index % 2) * 90 + (index < 6 ? Math.floor(index / 2) * 110 : 0);
  return (
    <button
      ref={ref}
      className={`tile${muted || sold ? " muted" : ""}${shown ? " in" : ""}`}
      style={{ "--d": `${delay}ms` } as CSSProperties}
      data-tile={item.id}
      onClick={() => {
        haptic.tap();
        onOpen(item.id);
      }}
    >
      <div className="ph">
        <Img photo={item.photos[0]} />
      </div>
      <div className="label">
        <div className="n mono">
          <span>N° {item.no}</span>
          <span>{sold ? "Продано" : transit ? "— В пути" : item.status === "reserved" ? "— Бронь" : item.size}</span>
        </div>
        <div className="t">
          {item.brand && <b>{item.brand}</b>}
          {item.name ?? item.title}
        </div>
        {!sold && (
          <div className="p">
            <Price item={item} />
            {(transit || item.status === "reserved") && item.size ? ` · ${item.size}` : ""}
          </div>
        )}
      </div>
    </button>
  );
}

export function Grid({ children }: { children: ReactNode }) {
  return <div className="grid">{children}</div>;
}

export function SkeletonGrid() {
  return (
    <div className="grid" aria-hidden>
      {Array.from({ length: 6 }, (_, i) => (
        <div className="tile skel" key={i}>
          <div className="ph shimmer" />
          <div className="label">
            <div className="bar shimmer" style={{ width: "40%" }} />
            <div className="bar shimmer" style={{ width: "80%" }} />
            <div className="bar shimmer" style={{ width: "55%" }} />
          </div>
        </div>
      ))}
    </div>
  );
}

// ------------------------------------------------------------ шторка выбора

export interface SheetOption {
  value: string | null;
  label: string;
  count?: number;
}

export function Sheet({
  title,
  options,
  value,
  onPick,
  onClose,
}: {
  title: string;
  options: SheetOption[];
  value: string | null;
  onPick: (v: string | null) => void;
  onClose: () => void;
}) {
  useEffect(() => setBackButton(onClose), [onClose]);
  return (
    <div className="sheet-wrap" onClick={onClose}>
      <div className="sheet" onClick={(e) => e.stopPropagation()}>
        <div className="sheet-head mono">
          <span>{title}</span>
          <button className="mono" onClick={onClose}>Закрыть</button>
        </div>
        <div className="sheet-list">
          {options.map((o, i) => (
            <button
              key={o.value ?? "__all"}
              className={o.value === value ? "opt on" : "opt"}
              style={{ "--i": Math.min(i, 12) } as CSSProperties}
              onClick={() => {
                haptic.select();
                onPick(o.value);
                onClose();
              }}
            >
              <span>{o.label}</span>
              <span className="mono">{o.value === value ? "●" : o.count ?? ""}</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

// ------------------------------------------------------------ системные кнопки

/** Назад: в Telegram — нативная кнопка в шапке, в браузере — кнопка на странице. */
export function BackControl({ onBack }: { onBack: () => void }) {
  useEffect(() => setBackButton(onBack), [onBack]);
  if (inTelegram) return null;
  return (
    <button className="web-back mono" onClick={onBack}>
      ‹ Назад
    </button>
  );
}

/** Нижние кнопки: в Telegram — MainButton/SecondaryButton, в браузере — их имитация. */
export function BottomButtons({
  main,
  secondary,
  colors,
}: {
  main: ButtonSpec | null;
  secondary?: ButtonSpec | null;
  colors: { bg: string; ink: string };
}) {
  useEffect(
    () => setBottomButtons(main, secondary ?? null, colors),
    [main?.text, main?.onClick, secondary?.text, secondary?.onClick, colors.bg, colors.ink],
  );
  if (inTelegram || (!main && !secondary)) return null;
  return (
    <div className="web-bottom">
      {secondary && (
        <button className="btn ghost" onClick={secondary.onClick}>
          {secondary.text}
        </button>
      )}
      {main && (
        <button className="btn" onClick={main.onClick}>
          {main.text}
        </button>
      )}
    </div>
  );
}
