# Vaultyfly — Pharmacy Inventory & Billing (Django)

Medical store ke liye inventory, purchase, GST billing, batch/expiry (FEFO) aur P&L.

## Setup (local)

```bash
python -m venv venv
source venv/bin/activate            # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env                # .env me DJANGO_DEBUG=1 rehne dein (local development)
python manage.py migrate
python manage.py seed_master        # common Units + Categories (optional)
python manage.py createsuperuser
python manage.py runserver
```

App: http://127.0.0.1:8000/ — Admin: http://127.0.0.1:8000/admin/

## Configuration (environment variables / `.env`)

| Variable | Matlab |
|---|---|
| `DJANGO_DEBUG` | `1` = development. **Default band (0)** hai. |
| `DJANGO_SECRET_KEY` | Production me zaroori (DEBUG band ho to bina iske app start nahi hota). |
| `DJANGO_ALLOWED_HOSTS` | Comma se alag domains. Default: localhost. |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | `https://your-domain` (agar HTTPS par chalate ho). |
| `DJANGO_HTTPS` / `DJANGO_BEHIND_PROXY` | `1` karne par secure cookies + HTTPS redirect. |

Production me static files ke liye: `python manage.py collectstatic` (files `staticfiles/` me banti hain, git me nahi jaati).

## Project structure

- `backend/models.py` — Product, Batch, Purchase, Bill/InvoiceItem, Payment, Sale, Transaction...
- `backend/views.py` — custom UI (list/new/edit/delete ek hi config se: `CONFIG`)
- `backend/reports.py` — Profit & Loss calculation (web page aur admin dono yahin se)
- `backend/admin.py` — Django admin (import/export ke saath)
