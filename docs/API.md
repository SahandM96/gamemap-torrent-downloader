# API (Tauri `desk_api`)

همهٔ درخواست‌ها از UI با `invoke('desk_api', { req: { path, method, body } })`.  
پاسخ موفق: `{ "success": true, "data": … }`. خطا: `Err(String)` در invoke (پیام فارسی/انگلیسی).

`path` می‌تواند با `/api/` شروع شود. `body` برای POST یک شیء JSON است (نه رشته).

| Method | path | Notes |
|--------|------|-------|
| GET | `/api/health` | aria2 up/version/binary، default_dir |
| GET | `/api/sources` | لیست منابع |
| GET | `/api/downloads` | `{ aria2, items[] }` |
| GET | `/api/partials?root=` | اسکن `*.aria2` |
| GET | `/api/settings` | prefs، recent_dirs، paths |
| POST | `/api/search` | `{ query, sources?, limit? }` |
| POST | `/api/sources/probe` | latency per mirror |
| POST | `/api/download` | `{ magnet \| torrent_url, dir, … }` — **dir مطلق** |
| POST | `/api/downloads/{id}/pause` \| `resume` \| `stop` \| `reveal` |
| POST | `/api/downloads/{id}/redir` | `{ dir, create_dir? }` |
| POST | `/api/downloads/{id}/delete` | `{ remove_files? }` |
| POST | `/api/partials/delete` | `{ control, remove_target? }` |
| POST | `/api/settings` | patch prefs |
| POST | `/api/daemon` | `{ action: start\|stop\|save\|purge\|log }` |
| POST | `/api/mkdir` | `{ parent, name }` |

دستورات جدا: `pick_folder`, `open_url` (فقط `https://`), `donate_info`, `create_donation({ amountIrr, description? })` → `{ payment_url, amount_irr }` via `https://api.gamemap.ir/api/v1/donations/create`.

**حذف‌شده نسبت به Python:** `GET /api/browse` — انتخاب پوشه با دیالوگ native Tauri.
