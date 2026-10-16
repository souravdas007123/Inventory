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
  - *New product from the purchase screen*: **➕ New product** opens a small window (name, composition, category, brand, GST, base unit, optional pack unit and size, schedule, barcode). Category, brand, GST, units and pack are suggested from a similar product (same first word of the name), else the last product; the drug schedule is never copied. Duplicate names/barcodes are refused with a **Use existing product** button; new category/brand names are created on the fly. The product is created at once, appears in every row's product list and is selected in the next empty row.
- **User roles / permissions**: the owner is a superuser (or a staff user who may change Shop Settings) and sees everything. Any other user sees and can do only what the permissions given in the admin panel allow: for each model *add / change / delete / view* (as in the admin, *change* also lets the user open the list; a user with only *add* is taken straight to the new-entry form). The menu, buttons, pages, lookups and downloads follow these permissions; owner-only pages are Profit & Loss, GST, Backup, Import, Audit log, Stock audit and Shop Settings. The Dashboard needs *view sales*; a user without it is sent to the first page they may use. For a purchase clerk give *Purchase: add + change* (add *Product: add* to allow the **New product** window, *Purchase order: add* for reorder suggestions) and leave **Staff status** and **Superuser** unticked: the clerk only needs the normal app login, not the admin panel.
- **Employees, salaries and fixed expenses** (needs migration `0007`): *Employees* (`/employees/`) keep an automatic Employee ID (EMP-001 ...), designation, phone, joining/leaving date, monthly salary and pay day. *Fixed Expenses* (`/fixed_expenses/`) hold monthly costs such as rent: enter them once with a day of the month. From then on an Expenses entry is added automatically for every month whose day has arrived (a salary entry per working employee, a rent entry per fixed expense), marked **Automatic**. It happens when the dashboard / expense pages are opened and in `daily_tasks`, catches up missed months (up to 24 at a time), never adds the same month twice, and never re-adds an entry you deleted. A month with a different amount is fixed by editing that Expenses entry. The first salary is added on the first pay day after the joining date (add a part-month salary by hand with **+ Add salary**). *Salary Sheet* (`/salary/`) shows, for any month, who is paid, part paid or not paid, with Excel export. Expenses also show employee and note, and the list shows this month's total split by type. Permissions follow the models (Employee, Fixed expense, Expense).
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

## Billing: batch hint (FEFO)

When a product is picked on the billing screen, a line under the row shows the batch that will be sold first (earliest EXP,
batches without a date last, expired ones skipped): batch number, EXP, rack / row / location and quantity left. The next
batches are shown in grey. If the entered quantity needs more than one batch, the split is shown ("20 from B-101 (Rack A) + 25 from B-102").
A red note appears when the quantity is more than the valid stock, and when the product has expired batches on the shelf.
The hint is for information only; the sale itself still deducts FEFO exactly as before (`InvoiceItem._deduct_from_batches`).

## WhatsApp alerts and refill reminders

- **💬 Alerts WhatsApp** (Dashboard) now builds a short message only when pressed (the dashboard loads faster): expired and
  soon-to-expire batches with batch no., qty, **rack / row** and **supplier**, "money at risk" (cost of expired / expiring stock),
  expiring in 31-90 days (still returnable to the supplier), out of stock and low stock. A few lines per section, "+N more (see the app)"
  and a size limit, so the WhatsApp link never becomes too long. The email digest keeps the longer list.
- **Shop Settings -> Alerts WhatsApp number**: the chat the alert opens in (manager / staff). Empty = shop phone, else WhatsApp lets you pick a contact.
  The owner's money summary (Today's summary) still goes to the shop phone. Needs `python manage.py migrate` (migration 0008).
- **Refill Reminders** (Customers menu): regular customers whose medicine is about to finish, from their own buying pattern
  (same medicine bought on 2+ different days, usually 10-75 days apart; listed 3 days before the next refill is due, kept up to 30 days when late).
  Nothing is stored: a customer leaves the list by buying again. Only customers selected on the bill (with a phone number) are included.
  Logic: `backend/refill.py`.

## Products list (no side scroll)

The Products list shows 6 compact columns (Product with composition / brand / category / schedule, Stock with status and reorder level, Price with GST,
Supplier, Place with the batch to sell first, Other with HSN / SKU / alternative) instead of 15 narrow ones.
The Supplier column shows the cheapest current supplier (the one an automatic order would use) with its latest price per unit and date, the Default Supplier if it differs, and how many suppliers the product has had. The Excel / CSV export still contains every column.
A list can opt in with `list_cols` in `CONFIG` (see `products` in `backend/views.py`).

The Billing list uses the same compact layout (Invoice + date, Customer + order / mode, Items, Amount with Taxable and GST below, Payment, Status);
its Excel / CSV export keeps CGST, SGST and IGST as separate columns.

## Automatic reorder level and automatic purchase orders

- **Reorder level is automatic and cannot be edited** (not in the Products form, not in the admin, not by import for existing products).
  It is about **10 days of average sales** of the last 30 days (never below 5, rounded up to whole packs). Products with no sales in
  the last 30 days keep their current level. It is refreshed when a product is sold, once a day for all products (first visit of the day
  to the Dashboard / Products / Reorder Suggestions, and `daily_tasks`). The Products list shows it as "Reorder at N".
  A new product starts at 5 (an import file may give a starting value for NEW products only). Logic: `backend/levels.py`, `reorder.auto_level`.
- **Automatic purchase order** (when a sale takes the stock to the reorder level or below):
  - **Supplier** = the supplier whose *latest* price for that product is the lowest (a price not bought for 180 days does not count while
    others are current); if the product was never purchased: its Default Supplier; else the supplier of the last purchase.
    The Reorder Suggestions page uses the same rule (`supplier_prices.pick_supplier`).
  - **Quantity** = enough for 30 days of sales minus the sellable stock and what is already on order, rounded up to whole packs
    (same rule as the Reorder Suggestions page, `reorder.order_qty`). Without sales: twice the reorder level.
  - No new PO for a product that is already on a Pending / Sent PO.
- Needs `python manage.py migrate` (migration 0009).

## Purchases without a separate Products import

You no longer need to import Products before entering a purchase.

- **Purchase screen:** type a medicine name (or barcode) in the search box and press Enter. An existing product is added to the invoice; adding the same product again just moves the cursor to its qty. If the name is unknown, the small "New product" window opens with the name filled in (GST, unit and pack are suggested), and the product is created and added to the invoice. When there are no products at all, a hint says so.
- **Stock Entry import:** the option "Create new products automatically" (ticked by default) creates a medicine that is not in Products from the row itself. Fill `gst_rate` and `base_unit` (and optionally `pack_unit` + `pack_size`, `composition`, `category`, `brand`, `hsn_code`). The qty unit may be the base unit or the pack unit. A value that looks like a barcode is never turned into a product name. Products are created inside the invoice's transaction, so an invoice that fails leaves no product behind.
- The search endpoint is `api/product_find/?q=` (needs the add/change purchase permission); creating a product still needs the add product permission.

### Supplier's CSV / Excel as the only entry

A supplier file that already has the product columns can go straight into **Purchases → Import** (Stock Entry); no Products entry is needed. New medicines are created from these optional columns: `gst_rate`, `base_unit`, `pack_unit`, `pack_size`, `hsn_code`, `composition`, `category`, `brand`, `barcode`, `schedule`. The supplier's MRP/selling price column sets the product MRP. A barcode that already belongs to a product finds that product (a different name is flagged as an error); a file with only a barcode column still works for existing products.

The **+ New Product** button is removed from the Products page (edit stays), so every new product comes from a purchase.

## Faster purchase entry (price auto-fill, PO receive, remembered file layouts)

- **Price auto-fill:** on the purchase screen, once the supplier, product and qty are set, the empty "Price" is filled with this supplier's last rate x qty (also when the unit is a Strip/Box). The field is highlighted and a note says "check it against the bill". It follows qty/unit changes until you type the real bill price, and the next price is never replaced once typed. When you press Save with auto-filled prices that you did not touch, you are asked once to confirm that they match the bill. Prices are filled only from the same supplier's history; a product with no history is left empty.
- **PO Receive:** "Receive" on a purchase order opens the purchase with the supplier, products and quantities filled in and a banner naming the order; prices come from the auto-fill above. After saving, the order is marked received; if some lines came short a warning lists them (ordered vs received) so you can make a new order.
- **Remembered file layouts:** if an import file's column names are not ours (for example a supplier's own export), a "Match columns" screen appears once. Each column of the file is matched to one of ours (or ignored) and the choice is saved by the set of column names ("Remember" is ticked by default), so the next file with the same columns is read automatically. "Match columns" / "Change column matching" on the preview opens it again. Saved layouts can be deleted in the admin ("Import file layouts"). Needs `python manage.py migrate` (migration 0010).

## Expiry Returns (Suppliers → Expiry Returns)

Lists the batches that are expired or expire within 30/60/90/180 days, grouped by the supplier they were bought from (and by purchase invoice). Expired batches are ticked for you; tick near-expiry ones only after the supplier has agreed. **Return ticked batches** books a purchase return (debit note) for each original invoice: stock and the batch are reduced and the supplier balance goes down. It is all-or-nothing: if any line cannot be returned (for example it was sold meanwhile) nothing is booked. Returns are in whole purchase units (full strips/boxes); loose tablets are shown but cannot be returned. **WhatsApp supplier** opens a ready message listing the items, to ask for a credit note first. Needs the "add purchase return" permission. No migration.

## Speed and safety on save

- **Database waits instead of failing:** SQLite now waits up to 20 s for a busy database and (Django 5.1+) starts write transactions up front, so two users saving at the same moment queue up instead of one getting "database is locked". WAL mode (faster with several users) is available but **off by default**; set `DJANGO_SQLITE_WAL=1` only on a host with a normal local disk.
- **Indexes (migration 0011):** product active/stock, batch product+expiry (FEFO), invoice item product+bill, purchase item product+purchase. They speed up low-stock, reorder, expiry and price-history screens; no data changes.
- **Double-click protection:** after a form is sent its buttons lock for a moment ("Saving..."), so a double click cannot save a bill or purchase twice. Forms that are cancelled by a confirmation or a validation are not locked, and the lock is released after 8 s or when coming back with the Back button.
- **Ctrl+S** (Cmd+S) saves the open form.

## Moving between pages feels smoother

- **The menu stays as you left it:** its scroll position and the groups you opened or closed are remembered while you move around (the group of the current page is always open).
- **Progress bar:** a thin bar appears at the top as soon as a link or form is used, so a click gives instant feedback while the next page loads.
- **No white flash:** browsers that support cross-page view transitions (Chrome / Edge) fade between pages and keep the menu still; others simply behave as before.
- **Smaller pages:** HTML is gzip-compressed (`GZipMiddleware`), which helps most on mobile data.

## Bill preview (Billing list → 👁 Preview)

Each bill in the Billing list has a **👁 Preview** button that opens a window with the number of items, every item (medicine, batch / expiry, qty, rate, discount, GST, amount) and the totals (taxable value, GST, discount given, round off, total; for a credit bill also paid and due), plus the patient / doctor when entered. **Open invoice** goes to the printable invoice. The preview data of the bills on the page is embedded in the list page itself (`backend/billview.py`, loaded with the list in a few queries), so the window opens instantly with no waiting; `api/bill_preview/<id>/` (needs permission to view bills) stays as a fallback. Medicine names are shown as plain text. No migration.

## Purchase preview (Purchases list → 👁 Preview)

Like the Billing list, each purchase invoice has a **👁 Preview** button that shows everything bought in it: supplier, invoice no. / date, every item (medicine, composition, batch, expiry, qty with unit, rate per unit, MRP, GST %, amount) and the totals (taxable, GST, total). An item that expires within 90 days is marked **soon** (amber) and an already expired one **expired** (red), with a "⚠ n short-expiry" badge at the top, so short-expiry stock is noticed the moment it is purchased. The window has **Open / Edit** and **↩ Return** buttons (shown only if the user has that permission). The data is embedded in the list page (`backend/billview.py`), so it opens instantly; `api/purchase_preview/<id>/` is the fallback. No migration.

## Automatic tests

`python manage.py test` runs about 100 checks of the important logic, on a temporary database (your real data is never touched):
- **Purchases:** stock, batch and supplier balance after a purchase, edit and delete; GST split; MRP update; unit conversion; duplicate invoice numbers.
- **Billing:** stock taken from the right batch (earliest expiry first, expired stock never sold), GST / IGST / discounts, invoice numbers, cancel, credit limit, customer balance and receipts.
- **Returns:** sales returns (credit notes) and purchase returns (debit notes), partial and full, with no paise lost, and the "cannot edit / cannot return what was sold" guards.
- **Import:** stock entry rows, new products, whole-invoice skipping, duplicates, column names.
- **Pages:** every list / form / report page opens, exports work, the Billing and Purchases preview data, access rules (login, owner only), login lock after 5 wrong passwords, backup path safety.

Run it after every update: `cd ~/Inventory; workon inv; python manage.py test`. A line `OK` at the end means all is well; if something fails, send the failure text.

## Admin panel serial numbers

All admin panel sections now carry a serial number (01-26; 12 is not used), so they stay in a fixed order: 17 Customers, 18 Customer Payments, 19 Sales Returns, 20 Purchase Returns, 21 Stock Write-offs, 22 Stock Audits, 23 Employees, 24 Fixed Expenses, 25 Shop Profile, 26 Import file layouts (01-16 as before). Migration `0012` only changes the names; run `python manage.py migrate` as usual.
