# MedSync — Pharmacy Inventory & Billing (Django)

Inventory, purchase, GST billing, batch/expiry (FEFO) and P&L for a medical store.

## Setup (local)

```bash
python -m venv venv
source venv/bin/activate            # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env                # keep DJANGO_DEBUG=1 in .env (local development)
python manage.py migrate
python manage.py seed_master        # common Units + Categories (optional)
python manage.py createsuperuser
python manage.py runserver
```

App: http://127.0.0.1:8000/ — Admin: http://127.0.0.1:8000/admin/

## Configuration (environment variables / `.env`)

| Variable | Meaning |
|---|---|
| `DJANGO_DEBUG` | `1` = development. **Off (0) by default.** |
| `DJANGO_SECRET_KEY` | Required in production (with DEBUG off, the app will not start without it). |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated domains. Default: localhost. |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | `https://your-domain` (if you serve over HTTPS). |
| `DJANGO_HTTPS` / `DJANGO_BEHIND_PROXY` | Set to `1` for secure cookies + HTTPS redirect. |

For static files in production: `python manage.py collectstatic` (files are created in `staticfiles/` and are not committed to git).

## Project structure

- `backend/models.py` — Product, Batch, Purchase, Bill/InvoiceItem, Payment, Sale, Transaction...
- `backend/views.py` — custom UI (list/new/edit/delete all driven by a single config: `CONFIG`)
- `backend/reports.py` — Profit & Loss calculation (used by both the web page and the admin)
- `backend/admin.py` — Django admin (with import/export)

## Features

- **Customers & credit**: Customer master, credit limit, payment mode (Cash/UPI/Card/Credit), partial payment, customer ledger (`/customers/<id>/ledger/`), customer payments.
- **Returns**: Sales return (credit note) from the bill list, purchase return (debit note) from the purchase list, expired/damaged stock write-off from the batch list. Stock, batches, supplier/customer balances and P&L adjust automatically. Returns are create-only; delete reverses them.
- **GST reports** (`/gst/`): GSTR-1 (B2B/B2C/credit notes), GSTR-3B summary, HSN summary, Excel export.
- **Drug register** (`/drug_register/`): Schedule H/H1/X sales with doctor/patient details, Excel export. Billing enforces these fields for scheduled drugs.
- **Barcode billing**: scan box on the bill form (barcode / name search, `/api/products/lookup/`).
- **Alerts**: Dashboard buttons for WhatsApp / email digest. For automatic daily email run
  `python manage.py send_alerts` from cron (`--to a@b.com`, `--print` to preview). Needs `EMAIL_*` and `ALERT_EMAILS` in `.env`.
- **Supplier Prices** (`/supplier_prices/`): for every product, compare what each supplier charged (price per base unit and per pack, incl. GST), who is cheapest, the price trend and the full purchase price history, with Excel/CSV export. It is calculated from existing purchases, so no migration is needed. Also reachable from the Products list (⚖️ Prices).
  - **Stock Entry hint**: on the Purchases form, each product row shows the last price from the selected supplier, the cheapest supplier now, and how the price you type compares (live).
  - **Price-increase alert**: the Dashboard lists supplier prices that rose 10% or more in the last 60 days, with a cheaper supplier if one exists.
  - **Selling price on purchase entry**: each purchase item has an optional *Selling price (MRP incl. GST)* for 1 purchase unit (e.g. per Strip). It is prefilled from the product's current MRP, shows the margin live, warns if it is below cost, and updates the product MRP (stored per base unit with 4 decimals). Billing then fills the rate by itself: rate = MRP ÷ (1 + GST%) for the selected unit, so the customer pays the MRP; a rate typed by the user is never overwritten. Also available as the optional `selling_price` column in Stock Entry import. Needs migration `0006`.
  - **Auto Purchase Order**: the supplier is the product's default supplier if set; otherwise the supplier with the lowest current price; otherwise the last purchase's supplier.
- **Purchase Orders**: print (use browser "Save as PDF"), email, WhatsApp.
- **Less typing, fewer mistakes** (no migration needed):
  - *Purchase warnings*: a duplicate invoice (same supplier and number, any letter case) is rejected with a clear message. Odd entries (price 40% above/below the usual, a quantity 10x the usual, price 0, selling price below cost, missing/expired/short EXP, same product twice) show a warning list with a **Save anyway** button, so nothing is blocked by mistake.
  - *Repeat order*: **Copy items from this supplier's last invoice** fills product, qty and unit (prices stay empty on purpose). Choosing a product also fills the unit used last time and its rack/row/location/composition.
  - *Billing*: **F2** search, **Enter** next field, **F9** (or Ctrl+Enter) save, cursor starts in the scan box; a barcode scan keeps the cursor in the scan box, a typed name jumps to the quantity. Typing a customer's phone number picks the customer and shows the credit due / limit.
  - *Reorder Suggestions* (`/reorder/`): from the real sales of the last 15-90 days it shows how many days stock will last and how much to order (rounded to whole packs, expired stock and stock already on order excluded). Tick products and create purchase orders per supplier (default supplier, else cheapest, else last supplier).
  - *Daily tasks*: `python manage.py daily_tasks` takes the backup and emails the daily summary (sales by payment mode, credit given/collected, purchases, expenses, top sellers, stock alerts); `--print` shows it on screen. The dashboard warns the owner if no backup was taken for 3 days and has a **Today's summary** WhatsApp button.
- **Dashboard**: 14-day sales chart, top selling, dead stock. **Stock audit**: physical count with variance applied to stock.

After pulling: `python manage.py migrate` (migrations 0003, 0004).

## Bulk import / export

Menu: **Master Data → Import / Export** (`/import/`).

- Import: **Products, Customers, Suppliers, Stock Entry (purchases / opening stock), Categories, Brands, Units** — Excel (.xlsx) or CSV.
  Download the template → fill it in → upload → **preview** (nothing is saved) → confirm.
- Rows with errors are skipped and the reason is shown. In Stock Entry, if any row of an invoice is invalid the whole invoice is skipped; the same supplier invoice number cannot be entered twice.
- Update mode: when "Update records that already exist" is ticked, rows are matched by name / barcode / phone / company and only the filled-in columns are changed.
- Every list page has **⬇ Excel / CSV** (the whole list, matching the current search). Exports of Products / Customers / Suppliers can be imported back.
- Recommended order for Stock Entry: Suppliers → Products → Stock Entry.
- Limits: 5 MB, 3000 rows (Stock Entry 1500). Preview data is kept in the server's temp folder (cleaned within 24 hours).

## Backup, Audit Log, Login lock

- **Backup**: Master Data → Backup (staff). Creates a compressed SQLite backup; the latest 14 are kept (change the folder with `DJANGO_BACKUP_DIR`).
  For an automatic daily backup, add this in PythonAnywhere **Tasks**: `~/.virtualenvs/inv/bin/python ~/Inventory/manage.py daily_tasks` (backup + daily summary email; use `--keep 30` to retain more backups, or `backup_db` for the backup only).
  Restore: unzip the `.gz` file and place it where `db.sqlite3` is.
- **Audit Log**: Master Data → Audit Log (staff). A per-user log of record creates, edits, deletes, bill cancellations, returns, write-offs, stock audits, imports and backups. Run migration `0005`.
- **Login lock**: Login is blocked for 15 minutes after 5 failed attempts (per IP + username). The counter is kept in a shared file cache (change the folder with `DJANGO_CACHE_DIR`). The lock does not apply to the `/admin/` login.
- **WhatsApp**: credit reminders (dashboard and customer ledger) and invoice sharing. These are wa.me links; no API is needed.
- **Import**: Expenses, Categories, Brands and Units can also be imported.
