import { useCallback, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { Item } from "./types";
import { back, price } from "./store";
import { haptic, openUrl } from "./telegram";
import { BackControl, BottomButtons, Img } from "./ui";

const EASE = "cubic-bezier(.2,.8,.2,1)";
const reduced = () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

function tileRect(id: number): DOMRect | null {
  const el = document.querySelector(`[data-tile="${id}"] .ph`);
  if (!el) return null;
  const r = el.getBoundingClientRect();
  return r.bottom > 0 && r.top < window.innerHeight && r.width > 0 ? r : null;
}

function flip(from: DOMRect, to: DOMRect) {
  const s = from.width / to.width;
  return `translate(${from.left - to.left}px, ${from.top - to.top}px) scale(${s})`;
}

function buyLink(item: Item, admin: string): string {
  const base = item.buy_url || `https://t.me/${admin}`;
  const m = base.match(/^https:\/\/t\.me\/([A-Za-z0-9_]{5,})\/?$/);
  if (!m) return base;
  const what = [item.brand, item.name].filter(Boolean).join(" ");
  const text = `Здравствуйте! Интересует N° ${item.no} — ${what}${item.size ? `, размер ${item.size}` : ""}`;
  return `https://t.me/${m[1]}?text=${encodeURIComponent(text)}`;
}

export function ItemScreen({
  item,
  admin,
  fav,
  onFav,
  colors,
}: {
  item: Item;
  admin: string;
  fav: boolean;
  onFav: () => void;
  colors: { bg: string; ink: string };
}) {
  const root = useRef<HTMLDivElement>(null);
  const hero = useRef<HTMLDivElement>(null);
  const body = useRef<HTMLDivElement>(null);
  const strip = useRef<HTMLDivElement>(null);
  const closing = useRef(false);
  const [index, setIndex] = useState(0);
  const [more, setMore] = useState(false);

  const photos = useMemo(() => [...item.photos, ...(item.extra_photos ?? [])], [item]);
  const mainCount = item.photos.length;
  const sold = item.status === "sold";

  // Открытие: фото «вылетает» из плитки, остальное проявляется.
  useLayoutEffect(() => {
    root.current?.scrollTo(0, 0);
    document.body.classList.add("locked");
    const from = tileRect(item.id);
    if (!reduced()) {
      root.current?.animate([{ backgroundColor: "transparent" }, {}], { duration: 300, easing: EASE });
      if (from && hero.current) {
        const to = hero.current.getBoundingClientRect();
        hero.current.animate([{ transform: flip(from, to) }, { transform: "none" }], { duration: 440, easing: EASE });
      } else {
        hero.current?.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 260 });
      }
      body.current?.animate(
        [{ opacity: 0, transform: "translateY(28px)" }, { opacity: 1, transform: "none" }],
        { duration: 420, delay: 90, easing: EASE, fill: "backwards" },
      );
    }
    return () => document.body.classList.remove("locked");
  }, [item.id]);

  const close = useCallback(() => {
    if (closing.current) return;
    closing.current = true;
    const from = tileRect(item.id);
    const h = hero.current;
    if (reduced() || !h || !root.current) return back();
    root.current.style.overflow = "hidden";
    const anims: Animation[] = [
      root.current.animate([{}, { backgroundColor: "transparent" }], { duration: 320, easing: EASE, fill: "forwards" }),
      ...(body.current
        ? [body.current.animate([{}, { opacity: 0, transform: "translateY(28px)" }], { duration: 200, fill: "forwards" })]
        : []),
      from && index === 0 && (root.current.scrollTop ?? 0) < 40
        ? h.animate([{}, { transform: flip(from, h.getBoundingClientRect()) }], { duration: 380, easing: EASE, fill: "forwards" })
        : h.animate([{}, { opacity: 0 }], { duration: 220, fill: "forwards" }),
    ];
    Promise.all(anims.map((a) => a.finished)).then(back, back);
  }, [item.id, index]);

  const goTo = (i: number) => {
    haptic.select();
    const el = strip.current;
    if (el) el.scrollTo({ left: i * el.clientWidth, behavior: "smooth" });
  };

  const buy = useCallback(() => {
    haptic.tap();
    openUrl(buyLink(item, admin));
  }, [item, admin]);
  const openPost = useCallback(() => {
    if (item.post_url) openUrl(item.post_url);
  }, [item.post_url]);

  const rows: [string, string | null | undefined][] = [
    ["Размер", item.size && (item.size_note ? `${item.size} (${item.size_note.replace("-", "–")})` : item.size)],
    ["Состояние", [item.condition, item.condition_note].filter(Boolean).join(" · ")],
    ["Сезон", item.season],
    ["Подлинность", item.authentic ? "Оригинал, любые проверки" : null],
    ["Получение", item.delivery ? item.delivery.replace(/\s*\/\s*/g, " / ").replace("СПБ", "СПб").replace("ДОСТАВКА", "доставка") : null],
    ["Категория", item.category],
  ];
  const desc = item.description ?? "";
  const longDesc = desc.length > 260;

  return (
    <div className="item-layer" ref={root}>
      <BackControl onBack={close} />
      <div className="hero" ref={hero}>
        <div
          className="strip"
          ref={strip}
          onScroll={(e) => {
            const el = e.currentTarget;
            const i = Math.round(el.scrollLeft / el.clientWidth);
            if (i !== index) {
              setIndex(i);
              haptic.select();
            }
          }}
        >
          {photos.map((p, i) => (
            <div className="slide" key={`${p.full}-${i}`}>
              <Img photo={p} full eager={i === 0} />
            </div>
          ))}
        </div>
        {photos.length > 1 && (
          <>
            <div className="dots">
              {photos.map((_, i) => (
                <i key={i} className={i === index ? "on" : ""} />
              ))}
            </div>
            <div className="count mono">
              {index >= mainCount ? "Детали · " : ""}
              {index + 1} / {photos.length}
            </div>
          </>
        )}
      </div>

      <div className="item-body" ref={body}>
        {photos.length > 1 && (
          <div className="thumbs">
            {photos.map((p, i) => (
              <button key={i} className={i === index ? "on" : ""} onClick={() => goTo(i)}>
                <Img photo={p} />
              </button>
            ))}
          </div>
        )}

        <div className="info">
          <div className="mono row">
            <span>N° {item.no}</span>
            <span>
              {sold ? "Продано" : item.status === "transit" ? "— В пути" : item.status === "reserved" ? "— Бронь" : "В наличии"}
            </span>
          </div>
          {item.brand && <h3>{item.brand}</h3>}
          <h1 className="name">{item.name ?? item.title}</h1>
          {!sold && <div className="price">{price(item.price)}</div>}
          {item.status === "transit" && (
            <p className="note mono">Вещь едет в магазин. Можно забронировать заранее.</p>
          )}
          {item.status === "reserved" && (
            <p className="note mono">Вещь забронирована. Напишите, чтобы встать в очередь — если бронь снимут, она ваша.</p>
          )}

          {!sold && (
            <div className="spec">
              {rows
                .filter(([, v]) => v)
                .map(([k, v]) => (
                  <div key={k}>
                    <span className="mono">{k}</span>
                    <span>{v}</span>
                  </div>
                ))}
            </div>
          )}

          {desc && <p className={more || !longDesc ? "desc" : "desc clamp"}>{desc}</p>}
          {desc && longDesc && !more && (
            <button className="mono link more" onClick={() => setMore(true)}>
              Читать полностью
            </button>
          )}

          <div className="links mono">
            <button
              className={fav ? "on" : ""}
              onClick={() => {
                haptic.soft();
                onFav();
              }}
            >
              {fav ? "♥ В избранном" : "♡ В избранное"}
            </button>
            {item.post_url && <button onClick={openPost}>Пост в канале ↗</button>}
          </div>
        </div>
      </div>

      <BottomButtons
        main={
          sold
            ? null
            : {
                text:
                  item.status === "transit"
                    ? "Забронировать / вопрос"
                    : item.status === "reserved"
                      ? "Встать в очередь"
                      : "Купить / задать вопрос",
                onClick: buy,
              }
        }
        colors={colors}
      />
    </div>
  );
}
