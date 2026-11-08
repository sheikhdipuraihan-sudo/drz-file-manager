# DRZ File Manager

A modern Flask file manager with folders, instant previews, secure share links, trash recovery, audit logs, admin analytics, and a polished responsive UI.

## Features

- Folders, breadcrumbs, rename/delete, and nested organization
- Drag & drop, paste, and multi-file uploads with progress and quotas
- Instant previews for images, video, audio, PDF, and text/code
- Star/favorites, recent files, search, sort, grid/list views
- Bulk select: ZIP download, move, star, and delete
- Share links with expiry, passwords, download limits, and public pages
- Trash with restore, restore-all, purge, empty, and auto-retention
- Account profile, themes, six accent colors, password change, JSON export
- Admin dashboard, users/quotas, audit log + CSV, live monitor, settings, backup, PIN
- Command palette (`Ctrl+K`), context menus, toasts, mobile UI, PWA assets
- CSRF protection, rate limiting, security headers, and friendly error pages

## Storage

By default uploads are sent to [catbox.moe](https://catbox.moe/tools.php) and only the public URL is stored in the database. This keeps the app working on serverless hosts with ephemeral disks.

> **Note:** catbox.moe may remove files that have been inactive for **2 years**. Download or back up anything important.

Set `DRZ_STORAGE=local` to keep files on disk instead. If a catbox upload fails, the app automatically falls back to local storage.

| Variable | Default | Purpose |
| --- | --- | --- |
| `DRZ_STORAGE` | `catbox` | `catbox` or `local` |
| `CATBOX_API` | `https://catbox.moe/user/api.php` | Upload endpoint |
| `CATBOX_MAX_MB` | `200` | Max file size sent to catbox |
| `SECRET_KEY` | auto-generated | Flask session signing key |
| `DRZ_DATA_DIR` | app folder / `/tmp` on Vercel | SQLite + local uploads |
| `MAX_UPLOAD_MB` | `2048` | Max request size |
| `FORCE_HTTPS_COOKIES` | `0` | Set to `1` behind HTTPS |

## Local run

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python api/index.py
```

The app creates its SQLite database at runtime. Default admin credentials are `admin` / `00000000` — change the password after first login.

## Tests

```bash
python test_app.py
```

## Vercel

The repository includes `vercel.json` and `public/manus-routes.json` for deployment. Uploads use catbox.moe, so file contents do not depend on Vercel's ephemeral filesystem. For fully persistent metadata, point `DRZ_DATA_DIR` at a writable persistent volume or external database.
