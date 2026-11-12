export type Status = "available" | "transit" | "sold";

export interface PhotoRef {
  thumb: string;
  full: string;
  w: number;
  h: number;
}

export interface Item {
  id: number;
  no: string;
  status: Status;
  brand: string | null;
  name: string | null;
  title: string | null;
  size: string | null;
  category: string | null;
  photos: PhotoRef[];
  post_url: string | null;
  // только у товаров в наличии / в пути
  season?: string | null;
  description?: string | null;
  size_note?: string | null;
  condition?: string | null;
  condition_note?: string | null;
  price?: number | null;
  currency?: string;
  authentic?: boolean;
  delivery?: string | null;
  buy_url?: string | null;
  extra_photos?: PhotoRef[];
  created_at?: number;
  status_changed_at?: number;
}

export interface CatalogData {
  items: Item[];
  archive: Item[];
  posts: Record<string, number[]>;
  meta: { channel: string; admin_username: string; generated_at: number };
}
