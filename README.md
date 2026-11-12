# wardrobe-app

Telegram Mini App — витрина наличия для канала архивной дизайнерской одежды
[nasonchikh wardrobe](https://t.me/nasonchikh_wardrobe).

Админ публикует пост с `#наличие`, `#едет` или `#продано` — витрина обновляется сама.

- `backend/` — парсер постов, каталог, синхронизация с ботом (Python, тесты: `pytest`)
- `frontend/` — мини-приложение (React + Vite), дизайн «Бирка»
- `.github/workflows/sync.yml` — синхронизация каждые 5 минут и публикация на GitHub Pages
- `docs/SETUP.md` — как подключить бота и запустить
- `PLAN.md` — план и принятые решения

## Локально

```bash
cd backend && python -m venv .venv && .venv/Scripts/pip install -r requirements.txt pytest && .venv/Scripts/python -m pytest
cd frontend && npm install && npm run dev   # демо-каталог из frontend/demo
```
