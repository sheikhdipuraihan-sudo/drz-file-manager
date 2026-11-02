# DRZ File Manager

A Flask-based file manager with authentication, uploads, previews, sharing, trash, audit logs, admin tools, and monitoring.

## Local run

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python api/index.py
```

The app creates its local SQLite database and upload directory at runtime. The default admin credentials in the original app are `admin` / `00000000`; change the password after first login.

## Vercel

The repository includes `vercel.json` and `public/manus-routes.json` for deployment. For production persistence, connect an external database and object storage because Vercel function filesystems are ephemeral.
