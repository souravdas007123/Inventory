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

- **GSTIN check**: Shop Settings, Suppliers, Customers and the Excel imports check a GSTIN for its shape (state code, PAN, entity number, `Z`) and its last check character (calculated from the other 14, so a typing mistake or a random number is caught). It cannot tell whether the number is registered on the GST portal. A number already saved is not blocked until it is edited. Code: `backend/gstin.py`, tests: `backend/tests/test_gstin.py`.
- **Phone, HSN, licence and MRP checks**: a phone number must be a 10-digit Indian mobile number (starting 6-9); `+91`, `91` or `0` in front, spaces and dashes are accepted and the number is saved as 10 digits. Suppliers and the shop phone may also be a landline with its STD code; the WhatsApp alert number must be a mobile. An **HSN code** must be 4, 6 or 8 digits. The **Drug Licence No.** (Shop Settings) only has obvious mistakes caught (odd characters, no digit, too short), because the format differs from state to state; several numbers can be separated by commas. A bill row cannot have a **rate above the MRP** of its unit (the rate is before GST, the MRP includes it; one paisa of rounding is allowed), and a medicine without an MRP is not checked. The same checks run in Shop Settings, the forms and the Excel imports. Values already saved (and old bill rows whose rate is not changed) are not blocked until they are edited. A purchase row whose selling price is below the cost still only shows a warning. Code: `backend/validators.py`, tests: `backend/tests/test_validations.py`.
- **PostgreSQL (optional)**: SQLite is the default. To use PostgreSQL, put `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT` in `.env` (see `.env.example`; the password never goes into `settings.py` or git) then `python manage.py migrate`. To move existing data: `dumpdata --natural-foreign --natural-primary --exclude contenttypes --exclude auth.permission --exclude sessions --exclude admin.logentry -o data.json` on SQLite, then `loaddata data.json` on PostgreSQL. Backups on PostgreSQL use `pg_dump` (found on the PATH, in the usual install folders, or from `PG_DUMP` in `.env`) and are saved as `.pgdump` files; restore with `pg_restore`. For the tests the database user needs the CREATEDB right. Not tried on PostgreSQL in the build environment: run `python manage.py test` on a PostgreSQL database before relying on it.
- **Reset Data** (owner only): Master Data → Reset Data removes the shop's data in one step so fresh data can be entered. Tick what to remove: bills, purchases and money records; medicines and master data (units Tablet, Strip, Box... and categories Tablet, Syrup, Injection... are added back); suppliers and customers; employees and fixed expenses. Anything other than the first group also removes the bills and purchases, because they depend on it. The owner must type `RESET` and the account password. A backup is made first (if it fails, nothing is removed) and everything runs in one database transaction. Shop Settings, user logins and the Audit Log are never removed. Code: `backend/reset.py`, tests: `backend/tests/test_reset.py`.
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
- **The sweep for low stock** (`levels.sweep`): a sale only checks the product it sold, so a product that became low another way
  (write-off, expiry or purchase return, stock count, edited purchase) used to wait for its next sale. Now every product that is low
  **and has sold in the last 30 days** is added to a PENDING order the same way (one order per supplier), at most once an hour (on a visit
  to the Dashboard / Products / Reorder Suggestions) and in the nightly `daily_tasks`. Products with no recent sales are not ordered
  automatically (a new shop's imported products with no stock yet must not flood the orders); they appear in **Reorder Suggestions** as
  "Low stock (no recent sales)", where you pick and order them. Tests: `backend/tests/test_auto_po.py`.
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

## Admin panel look
The admin (`/admin/`) now has the MedSync look (teal, rounded, light/dark), a "← Back to MedSync" link, models grouped in sections (Master data, Purchases, Sales, Stock, Accounts, Settings, Users & access) and summary cards (sales today, low stock, expiring, expired, supplier balance) shown only to users who may view that data. Code: `backend/admin_site.py`, `templates/admin/`. It is cosmetic only and falls back to the normal admin on any error. No migration needed.

## Product preview
The Products list has a **👁 Preview** button on every row: details (brand, GST, MRP, place, supplier), how the units convert (1 Strip = 15 Tablet), stock in packs, how much can be sold / is expired, and every batch in stock (sell-first on top, short expiry flagged), with Edit and Prices buttons. The data is embedded in the page, so it opens instantly (fallback: `/api/product_preview/<id>/`). No migration needed.

## Billing: same-salt alternatives and Hold bill
**Alternatives.** When a medicine in a bill is out of stock (or the quantity is more than what can be sold), the row shows the other medicines with the **same salt** that can be sold now, cheapest first, with stock and MRP. One click replaces the medicine (or adds it as a new row when only part is short). "Same salt" ignores case, spaces, punctuation and the order of the salts ("Paracetamol 500mg" = "paracetamol 500 mg"); expired batches are not counted as stock. The search buttons also say "OUT OF STOCK". The Products list uses the same logic.

**Hold bill.** On a new bill, **⏸ Hold bill** parks what has been typed (no stock is used, no invoice number is taken). Held bills are listed above the form; **▶** opens one again, **✕** discards it. Saving a resumed bill removes it from the list. At most 30 bills are held at a time and a held bill is deleted after 7 days. Needs `python manage.py migrate` (migration 0013, one new table).

## CA / Accountant Export
Accounts > **CA / Accountant Export** (owner only): choose the dates (or press This month / Last month / This financial year / Last financial year) and **Download Excel**. One file with the sheets Summary (net sales, net purchases, GST payable), Sales Register, Sales by GST rate, Credit Notes, Purchase Register, Purchases by GST rate, Debit Notes, Expenses, Supplier Payments and Customer Receipts. Only final bills are counted (cancelled ones are only counted in the Summary). Totals at the bottom of each register are Excel formulas. A range over about 2 years, or with more than 100,000 entries, is refused instead of being cut off. Code: `backend/ca_export.py`. No migration needed. (A Tally XML import file is not included: it needs the ledger names of your Tally company.)

## Health score

Owner only. Shown on the admin home (a ring with the score) and in full on **Health Score** (`/health/`).

* Four sections, all from real measurements: **Data** (40) – stock vs batches, negative batches; **Safety** (25) – backup age, whether the latest backup really opens, database integrity, debug/https; **Speed** (20) – timed queries on this server; **Shop** (15) – expired stock, low stock, missing MRP / HSN.
* Every lost point is listed with its reason. A check that cannot run is left out of the score, never counted as good.
* `daily_tasks` refreshes it after the backup; the page re-measures when older than an hour, or with **Measure again**. The last 120 measurements are kept (migration `0014`).
* Tests: `backend/tests/test_health.py`.

## Public home page (landing page)

Visitors who are **not logged in** see a fast, SEO-ready page at `/` (title, description, canonical, Open Graph, JSON-LD, `robots.txt`, `sitemap.xml`; no JavaScript, no external fonts or requests, about 5 KB gzipped). Logged-in users still land on the dashboard at `/`, and the login is at `/login/`. The only database use is a small "From the blog" teaser, wrapped so that it can never break the page.

* Set `DJANGO_WHATSAPP=919876543210` (country code + number) in `.env` or the PythonAnywhere environment to turn the "Get a demo" buttons into WhatsApp links. Without it the buttons simply lead to the login.
* The canonical address, share image and sitemap use the domain the visitor came on, so there is nothing else to configure. After deploying, add the site to Google Search Console and submit `/sitemap.xml`.
* `robots.txt` lets search engines index only the home page; the app itself stays out of search results.
* The pictures on the page are labelled illustrations with sample data. Replace them with real screenshots when you have them.
* Files: `backend/landing.py`, `templates/app/landing.html`, `backend/landing_assets/`.

## Blog (`/blog/`)

Public articles for SEO, written in the admin under **Website → Blog Articles**.

* Fields: title, address (slug), summary (also the Google description), body, *Published* tick and date. A draft or future-dated article is hidden from visitors; the owner can open it at its address to preview it (marked noindex).
* Body is plain text: blank line = new paragraph, `## Heading`, `### Smaller heading`, `- ` bullets, `1. ` numbers, `**bold**`, `[text](https://...)`. Everything is escaped, so an article cannot inject HTML or scripts.
* Each article page has its own title, description, canonical, Open Graph and BlogPosting data; `/sitemap.xml` lists the blog and every published article, and `robots.txt` allows `/blog/`.
* The home page links to the blog and shows the latest 3 articles.
* **Pictures:** every article can show a ready-made illustration (pick one under *Cover*: Expiry, GST, Drug register, FEFO, Supplier prices, Excel import; drawn in code, so nothing to upload or host) or your own picture: put its address (`https://...` or `/...`) in *Image url* and a short description in *Image alt*. Your picture replaces the illustration and is also used when the article is shared. Inside the text, a line like `![Billing screen](https://...)` adds a picture. Only `https://` and site-relative addresses are accepted. Migration `0017` adds the fields and gives the first 6 articles their illustrations.
* Migration `0015` creates the table; `0016` adds 6 first articles once (an article that already exists is never overwritten, so your edits are safe). Files: `backend/blog.py`, `backend/blog_seed.py`, `templates/app/blog_*.html`, tests in `backend/tests/test_blog.py`.


## Supplier payments: note, date and mode

* **Note** (optional, up to 255 characters): the UTR or cheque number, or which invoice the money pays. It shows in the Payments list (and its search), in the supplier's ledger next to the payment (`Payment (UPI) · UTR 4231987`), in the Excel/CSV list export and in the accountant's file (sheet "Supplier Payments", column *Note*). The note of an old payment can be corrected at any time without touching the balance.
* **Payment date** is now chosen on the form (today is filled in). Left empty it means today. It cannot be in the future. Back-dated payments take their place in the ledger by date; the stored balance does not depend on the date.
* **Mode**: Cash, Bank, **UPI** and **Cheque**.
* **Closed period** (Shop Settings, *books closed up to*): a payment cannot be added to, moved into or out of, or deleted from a closed period, and its amount, supplier, date or mode cannot be changed there; only its note can. The message says so in plain words.
* The accountant's file also guards the notes of expenses and customer receipts against Excel formulas (a note starting with `=` is shown as text).
* Migration `0023` adds the note and makes the date editable (existing payments keep their dates). Tests: `backend/tests/test_payment_note.py`.

## Server health (platform panel)

`/superadmin/server/` ("Server health" in the platform menu) shows how busy the server is: CPU load (as `load 0.8 of 4 CPUs`), memory, disk, database size (total and per shop), database connections, how long the application has been running (since the last restart), the server's own uptime and the age of the last backup. Each figure has a plain status (OK / Busy / Low / Almost full ...) and the page lists what needs attention. The shop list (`/superadmin/`) shows a short warning when something does.

* Read-only, and cheap: it reads Linux's `/proc` files, `shutil.disk_usage` and two small PostgreSQL queries. The figures are cached for 30 seconds (the per-shop sizes for 10 minutes) in the shared cache, so refreshing the page costs the server nothing, and no other page of the app is touched. "Measure again now" re-reads everything.
* A figure that cannot be read (for example on a server without `/proc`) shows "Not available" and never breaks the page.
* Warning limits (in `tenants/serverstats.py`): disk 85% / 95% used, memory 15% / 7% available, load per CPU 1.0 / 2.0, database connections 80% / 95%, backup 36 / 72 hours old.
* It looks at the server from the inside, so it cannot warn you when the whole server is down: also put an outside monitor (for example UptimeRobot) on `/healthz/`.
* Files: `tenants/serverstats.py`, `tenants/server_admin.py`, `templates/app/platform_server.html`, tests in `tenants/test_serverstats.py`.

## Website content (FAQ, policy pages, home page text)

Like the blog, these are edited in the admin (`/admin/`, section **Website**), on the main site only. A shop's own admin does not show them.

* **FAQ Questions** - the "Questions shop owners ask" box on the home page. Add, edit, hide (untick *Active*) or reorder (change *Position*, smaller first). The Google FAQ markup on the page is made from the same questions, so it always matches. If every question is hidden the box disappears; if the table has no rows at all (or cannot be read) the standard questions are shown.
* **Policy Pages** - Terms of use, Privacy policy, Refund and cancellation. Three fixed pages (they cannot be added or deleted). The text uses the same simple marks as the blog (`## Heading`, `- ` bullets, `**bold**`, `[text](/privacy/)`), is escaped so it cannot inject HTML, and you set the *Last updated* date yourself. An empty page falls back to the built-in text.
* **Home Page Text** - the main headline, the paragraph under it, and the heading and text of the last box. One record only; an empty box keeps the standard text. In the last box write `{days}` where the number of free-trial days should appear.
* **Social Media Links** - the round icons in the footer (*Follow us*). Add one row per page (Facebook, Instagram, X, YouTube, LinkedIn, WhatsApp, Telegram, Threads, Pinterest or any website), paste the full `https://` link, change *Position* to reorder, untick *Active* to hide. With no rows the *Follow us* line is not shown. The *Share MedSync* links (which let visitors share the site) stay as they were. Icons are inline SVG in `backend/social_icons.py` (no external request); migration `0024` creates the table.
* Prices, WhatsApp, contact e-mail and the notice bar stay where they were (Platform panel, Website settings).
* Migration `0021` creates the tables; `0022` copies the first FAQ and the three policy texts once, on the main site only (existing rows are never overwritten, so edits are safe). Files: `backend/sitecontent.py`, `backend/site_seed.py`, `templates/app/legal_saved.html`, tests in `backend/tests/test_site_content.py`.

## Paying for a plan (Razorpay autopay + UPI with approval)

Prices are **Rs 500 a month or Rs 5000 a year, GST included** (`PLAN_PRICES` in settings; the platform owner can change them under *Website settings*, which wins over the settings file). Shops pay from their **Plan & Payments** page (`/plan/`, owner only). Both ways keep working after a plan has ended (the pages `/plan/pay/...` stay open for a locked shop).

**Before every payment a popup shows the details**: plan, the days it covers (from - to; it starts after the current plan when one is still running), the price without GST, **GST 18%** (`GST_PERCENT` in settings; the two always add up to the price), the total, and whether it renews. The same split is in the receipt e-mail.

**1. Autopay with Razorpay Subscriptions** (renews every month / year, like any subscription): the shop presses *Subscribe (autopay)*, sees the popup, and Razorpay's window opens where it sets up UPI autopay or a card. The first charge extends the plan at once; every later charge arrives by **webhook** and extends it again (receipt e-mailed each time). The shop can **Stop autopay** on the same page; the days already paid stay valid. If a charge keeps failing Razorpay stops the subscription and the shop gets an e-mail. The amount always comes from the server price; the Razorpay plan is checked against it (same amount and period) before every subscription, and an odd plan is refused. A payment can extend a shop **only once** (each Razorpay payment id is recorded once, whether the browser or the webhook tells us first).
* Setup in the Razorpay dashboard (start in **Test mode**):
  1. *Subscriptions -> Plans -> Create plan*: **MedSync Monthly**: billing every **1 month**, amount **500**; **MedSync Yearly**: every **1 year**, amount **5000**. Copy each **Plan Id** (`plan_xxxx`). If you change a price in *Website settings*, make a new plan with the same amount and put its id in `.env`.
  2. *Account & Settings -> API keys* gives **Key Id** and **Key Secret**.
  3. *Webhooks -> Add*: URL `https://medsync.socialflylive.com/razorpay/webhook/`, a secret of your choice, tick **subscription.charged, subscription.activated, subscription.halted, subscription.cancelled, subscription.completed**.
  4. Put the values in the server `.env` and restart (`systemctl restart medsync` as root):
     ```
     RAZORPAY_KEY_ID=rzp_test_xxxxxxxx
     RAZORPAY_KEY_SECRET=xxxxxxxx
     RAZORPAY_WEBHOOK_SECRET=the-secret-you-typed-in-the-webhook
     RAZORPAY_PLAN_MONTHLY=plan_xxxxxxxx
     RAZORPAY_PLAN_YEARLY=plan_xxxxxxxx
     ```
  A plan's *Subscribe* button is shown only when its plan id is set. Test with Razorpay's test UPI/cards (nothing real is charged), then replace everything with the **live** keys and live plans.

**2. UPI with approval** (no gateway, no fee, one time, does not renew): set your **UPI ID** and the name shown to the payer under *Website settings*. A shop presses *Pay by UPI*, sees the popup, then a QR code (made by `backend/qr.py`, no extra package) with the amount fixed, a reference like `MS7K2Q9XAB` in the payment note and a button for phones. After paying it types the 12-digit **UPI reference number (UTR)**. You get an e-mail, see it under **Payments** in the platform panel (and a bar on the dashboard), check your bank, and press **Approve** (plan extended, receipt e-mailed) or **Reject** (reason e-mailed). The same UTR cannot be used twice.

* Every payment is also a normal row in the shop's *Payments received* list (the old *Save payment* button still works for cash or bank transfers).
* Files: `tenants/billing.py` (the one place that moves a shop's paid-until date; `settle()` and `charge()` lock the rows), `tenants/razorpay.py`, `tenants/upi.py`, `tenants/payshop.py` (shop side), `tenants/payadmin.py` (Payments page + webhook), `templates/app/plan.html`, `plan_upi.html`, `platform_payments.html`, migrations `tenants/0010` and `0011`. Run `python manage.py migrate_schemas --shared` once.
* Tests: `tenants/test_pay.py` (the parts that need no database run anywhere; the rest needs MULTI_SHOP=1), `backend/tests/test_qr.py`. The real Razorpay calls are stubbed in tests; try the test mode once for real.

## Sign in / sign up with Google

Only on the main site and only while `MULTI_SHOP=1`. Without the two keys below no Google button is shown and nothing changes.

**One-time setup (Google Cloud Console, free):**
1. Open https://console.cloud.google.com/ -> create a project (for example *MedSync*).
2. *APIs & Services* -> *OAuth consent screen* -> User type *External* -> app name *MedSync*, your support e-mail, your logo (optional) -> Save. Scopes: only `openid`, `email`, `profile` (nothing more is asked). Press *Publish app* so that everybody (not only test users) can use it.
3. *APIs & Services* -> *Credentials* -> *Create credentials* -> *OAuth client ID* -> Application type *Web application*.
   * *Authorised redirect URIs*: `https://medsync.socialflylive.com/auth/google/callback/` (use your own main address; add `http://localhost:8000/auth/google/callback/` too if you want to test on your PC).
4. Copy the *Client ID* and *Client secret* into the server `.env` (never into git) and restart:
   ```
   GOOGLE_CLIENT_ID=xxxxxxxx.apps.googleusercontent.com
   GOOGLE_CLIENT_SECRET=xxxxxxxx
   ```
   `sudo systemctl restart medsync` (as root).

**How it behaves**
* *Sign in*: Google must say the e-mail is verified, and it must be the e-mail of a shop's owner. He is passed to his own shop with the same one-time pass as the password sign-in (so 2-step sign-in still applies). Staff keep using username and password.
* *Sign up*: Google confirms the e-mail, then the usual shop form opens without e-mail and password boxes (the e-mail cannot be changed). The shop is made at once and he is signed in. His account has no password; he can set one any time with *Forgot your password?*.
* Safety: random `state` and `nonce` per attempt (used once, valid 10 minutes), the code is exchanged server-to-server with Google, the token's audience, issuer, expiry and nonce are checked, at most 20 attempts per address per 10 minutes. An e-mail Google did not verify is never used.
* Files: `tenants/google.py` (+ routes in `tenants/urls.py`), `tenants/mainlogin.py` (`pass_to_shop`), `tenants/views.py` (`_signup_with_google`), buttons in `templates/app/login.html` and `signup.html`. Tests: `tenants/test_google.py` (any setup) and `tenants/test_google_flow.py` (needs MULTI_SHOP=1 and PostgreSQL).

## Phone layout

On a phone every list becomes cards. Empty cells ("-") are hidden, the Edit / Delete / Preview buttons sit on one tidy row with a comfortable touch size, the page title no longer gets squeezed by the Back button, and the 14-day chart shows every second date so the dates stay readable. Rules: `templates/app/base.html` (`table.t.stack ...`); helper `stack()` in `backend/views.py` drops a lone `-`. Tests: `backend/tests/test_mobile_lists.py`.

## Dashboard: expenses and net profit

Owner only (in the **Money** section, with a breakdown below it): expenses today and this month, **net profit this month** and expenses by type. Net profit = gross profit - expenses - stock write-offs, worked out exactly as on the Profit & Loss page (a test checks that both pages agree). If this part ever fails, the rest of the dashboard still opens. Migration `0018` only changes the wording of one write-off reason ("Damaged / broken"); the database is not touched.

## Deleting records safely

A purchase that was already sold from, or that has a return or write-off linked, cannot be deleted. The site and the admin now say why in a message (for example "5 (base unit) of this purchase has already been sold/used/returned, so it cannot be deleted. If there is a mistake, create a Purchase Return.") instead of showing a Server Error (500). `Purchase.delete_problem()` answers the question without changing anything. In the admin, "delete selected" skips a record that cannot go and deletes the others. Any business-rule error (`ValidationError`) raised while saving or deleting in the admin becomes a message, and the database work is rolled back. Tests: `backend/tests/test_purchase_delete.py`.

## Live amount preview (bill and purchase screens)
- While a bill is typed, a **Bill preview** under the items shows, for each medicine, the discount, taxable value, GST and amount, and below it the sub total, discount, CGST/SGST (or IGST), round-off and **grand total**. For a credit bill it also shows "Paid now" and "Credit due". The **Invoice preview** on the purchase screen shows taxable value, GST and the invoice total.
- It only shows numbers; it never changes a field and never saves anything. A note appears under it when a row is incomplete (no quantity, no rate, both Discount % and Flat, discount above 100%).
- The maths runs in the browser (`templates/app/live_calc.js`), so typing is instant and no request is sent per key. Only the GST % of the chosen medicines is fetched, once (`/api/gst_rates/`). Amounts are calculated in whole paise and rounded exactly as the server does (half-even per line, total rounded half-up to the rupee), so the preview equals the saved bill. A saved purchase keeps the GST rate it was saved with.
- `backend/tests/test_live_preview.py` checks this against the real models when Node.js is installed (skipped otherwise).

## Billing: units of a medicine
- When a medicine is picked on the bill (by search, barcode or the list), its **Unit** is set to the **base unit** (for example Tablet) and the unit list of that row shows only the units of that medicine, e.g. "Tablet (base unit)" and "Strip (1 = 10 Tablet)".
- Right under the Unit box there are round buttons for the units of that medicine (for example **Tablet** and **Strip ×10**). The chosen unit is filled; tap another to switch (the rate follows). The Unit box stays and always shows the same choice. A medicine with only one unit shows no buttons.
- A line under the row shows the base unit, the pack sizes, the stock in packs and the MRP of each unit. The quantity box says "Qty in Tablet" / "Qty in Strip".
- A held bill that is resumed keeps the units it was saved with. Picking another medicine in a row starts again with that medicine's base unit.
- The old rule is unchanged: a bill row with no unit would use the base unit; now it is simply shown and chosen.


## Duplicate invoices and safe purchase edits
- The same **supplier + invoice number** cannot be entered twice: the purchase screen refuses it with a clear message (upper/lower case and extra spaces are ignored), the Excel import already did, and the database has a unique rule as the last guard. The same number from another supplier is fine.
- A purchase row **cannot be edited below what is already sold, used or written off** (before, the stock was silently set to 0 and went out of step with the batches). The message tells the number; for goods taken back by the supplier use a Purchase Return. After every purchase save a safety check makes sure no batch is negative, otherwise the whole change is rolled back.
- Bills were already safe: a bill edit that needs more than the available stock is refused, and cancelling or deleting a bill only gives the stock back. Tests: `backend/tests/test_stock_guards.py`.

## GSTR-2B Check (Accounts → GSTR-2B Check, owner only)
- You can claim Input Tax Credit (ITC) only for purchase bills that show in your **GSTR-2B**. Upload the file from the GST portal (Returns → GSTR-2B → Download, the **JSON** file; a plain Excel / CSV list with GSTIN, Invoice number, Invoice date, Taxable value and the taxes also works) and the page compares it with your purchases. The file is only read; nothing is stored.
- Bills are matched by **supplier GSTIN + invoice number** (case, spaces, `/` `-` and leading zeros are ignored, so `INV/2026/001` = `inv-2026-1`). The result groups: **Amount differs** (taxable or tax differs by more than ₹1), **In your books, not in GSTR-2B** (do not claim that ITC yet; ask the supplier to file it, it may show in a later month), **In GSTR-2B, not in your books** (a purchase you did not enter), **Invoice number differs** and **Supplier GSTIN differs** (probably the same bill, with a hint), and **Matched**. ITC marked "not available" by the portal, reverse charge, credit / debit notes and purchases from suppliers without a GSTIN are shown separately.
- The month is taken from the file unless you pick one. An invoice dated in an earlier month (up to 6 months back) is still matched; a bill of an earlier month is never listed as missing. "Check and download Excel" gives the same result as a sheet for your CA.
- Not compared: amendments (B2BA), ISD and imports of goods; the page tells you when the file has them. It uses only what is in the file and in your purchases; it cannot check the supplier's GSTIN on the portal. Code: `backend/gstr2b.py`, tests: `backend/tests/test_gstr2b.py`.

## Sign-in history and the Users page (Master Data → Users & Sign-ins, owner only)
- Every **sign-in, sign-out and failed sign-in** (with the name that was tried and the IP address, never the password) is added to the Audit Log. The **Users & Sign-ins** page shows, for every user, the last sign-in, the last action, the actions of the last 7 days and the failed sign-ins of the last 7 days. If the log cannot be written, the sign-in still works. Code: `backend/login_audit.py`, tests: `backend/tests/test_login_audit.py`.

## Month lock (Shop Settings → Books closed up to)
- After a month is finished and your CA has the figures, the owner can set **Books closed up to** (for example the last day of last month, never a future day). Bills and purchases dated on or before that day can then no longer be **added, edited, cancelled or deleted** (also not through the admin panel, the Excel import or by moving a bill to another date). Clearing the date reopens the period. Each change of the date is written to the Audit Log (action LOCK).
- Only bills and purchases (with their items) are locked. Returns, payments and expenses are not. Nothing is checked while no date is set. Code: `backend/locks.py`, tests: `backend/tests/test_month_lock.py`. A new migration (`0019_books_closed_until`) adds the field, so run `migrate`.

## Popups instead of browser alert boxes
- Every message and question inside the app (delete, cancel a bill, apply a stock audit, return expired batches, discard a held bill, the automatic-price check on a purchase, reset data, "could not load / hold") appears in a popup in the app's own style, never in the browser's plain alert / confirm box. The popup has a clear title and buttons ("Yes, delete" / "Cancel"), Esc and a click outside close it as Cancel.
- For developers: a form or button with `data-confirm="Question?"` (and optionally `data-confirm-yes="Yes, delete"`) asks first; from scripts use `appAlert(text)` and `await appConfirm(text, {danger: true})`. The helper lives once in `templates/app/base.html`; `backend/tests/test_popups.py` fails if a template uses the native `alert(` / `confirm(` again.

## Search result text (SEO)
- The home page title is about 60 characters and its meta description about 147 characters, so Google shows them whole (a description over about 155 characters / 1000 pixels is cut off). A blog article's description is its summary, cut at 155 characters on the page (the summary itself can stay longer). `backend/tests/test_landing.py` and `test_blog.py` check these lengths.
- The home page has an Apple touch icon (`/apple-touch-icon.png`, 180x180, linked on the home page and blog pages). It uses at most 33 bold tags and no bold text is repeated, because SEO tools warn about many or repeated bold tags. Use the plain `w7` class for heavier text instead of adding more bold tags; the tests check both.
- The home page footer has plain share links (WhatsApp, Facebook, X, LinkedIn, Telegram). They are ordinary links, so no script or tracker is loaded. The page keeps about one heading per 80+ words, every link has its own text (the footer uses different words from the top menu) and no link is empty; `test_landing.py` and `test_blog.py` check this.
- Every page (home, blog, login, the app, printouts) declares the icon: `/favicon.svg` for modern browsers, `/favicon.ico` (16, 32 and 48 px) for older browsers and for tools that ask for it by default, and `/apple-touch-icon.png` for phones. Browsers keep the old icon in a cache, so after an update press Ctrl+F5 once.
- `robots.txt` allows search engines to fetch the icons (`/favicon.ico`, `/favicon.svg`, `/apple-touch-icon.png`) and the share picture, otherwise Google cannot show the icon in search results.

## Speed check
`python manage.py profile_pages` (run it on a database with real data, locally or on the server) opens the main pages as the owner, using GET requests only, and prints for each page the number of database queries, the time in milliseconds, the slowest single query and any query that repeats 5 or more times. A page with many queries or a repeated query is where the next speed-up is. You can also name pages: `python manage.py profile_pages /gst/ /expiry/`. The dashboard's low-stock list loads its units together with the products (no extra query per row).

## Many shops in one install (work in progress, OFF by default)
Goal: every shop that signs up gets its own PostgreSQL schema (its own tables, users and data) and its own address such as `sharma.medsync.socialflylive.com`, and the platform owner follows all shops from one super admin. It uses `django-tenants`.
- **Off by default.** Nothing changes unless `MULTI_SHOP=1` is in `.env`. It needs PostgreSQL (`DB_NAME` in `.env`).
- **The first shop stays where it is.** The main site (schema `public`) keeps the existing shop's data, so no data is moved. New shops get new schemas.
- **Backups are per shop.** A shop's backup holds only that shop's tables (`pg_dump -n <schema>`, file `medsync_<schema>_...pgdump`) and a shop can only list or download its own files. The platform's backup is the whole database.
- **Shops pages in the admin** (`/admin/` on the main site) are hidden on every other shop, so a shop owner never sees other shops.
- Local try-out (PostgreSQL only): add `MULTI_SHOP=1` and `SHOP_BASE_DOMAIN=localhost` to `.env`, then `python manage.py migrate_schemas --shared`, `python manage.py init_multishop`, `python manage.py runserver`. To go back, remove `MULTI_SHOP=1`.
- **Sign-up** (`/signup/` on the main site): shop name, owner, email, mobile, web address name, password. An email with a link is sent; only when the owner opens the link and presses the button are the shop's schema, web address, owner login (the email is the username) and 30-day trial made. Nothing is made for an unconfirmed email. A hidden field and 5 sign-ups per hour per IP stop robots. **The live server needs working email (`DJANGO_EMAIL_HOST` and the other `DJANGO_EMAIL_*` values in `.env`), otherwise nobody receives the link.** For local testing only, `SIGNUP_SKIP_EMAIL=1` skips the check; with the default console email the link is printed in the terminal.
- **Trial and plans:** a shop is `trial` (30 days), `active` (paid until a date), `locked` (trial or plan over: the owner can sign in and look, but nothing can be saved) or `paused` (switched off). Days left show as a line at the top of every page of a shop. The platform owner changes plan and dates in the admin (Shops).
- **A shop's own address** shows its login instead of the marketing home page, and has no blog, sitemap or sign-up (the main site has them).
- **Platform panel** (`/superadmin/` on the main site; any other address gives a 404): every shop with status (free trial, paid, locked, paused), plan, days left, bills, products, last sign-in and joined date, with search and a status filter; totals for new shops, estimated monthly income and money received. A shop's page has the buttons: **Save payment** (monthly or yearly, amount blank = plan price; the paid-until date moves forward by one month or one year, from the old date while the shop has not run out), **Extend trial**, **Switch the shop off / on** and **notes**. Payments are kept as a list per shop. Nothing is ever deleted from here. Who may open it: every superuser of the main site, or only the usernames in `PLATFORM_ADMINS` (comma separated, in `.env`). The blog is edited at `/admin/backend/blogpost/` on the main site (the panel has a link).
- **Website settings** (`/superadmin/site/`, linked in the panel): WhatsApp number, contact e-mail, a notice bar for the top of the home page and the monthly and yearly price. A blank price means the standard one (`PLAN_PRICES`). The prices also flow into the sign-up page and the payment form. The home page shows a *Simple pricing* section (only while `MULTI_SHOP=1`) and reads these settings with a 60-second cache, so a change appears within a minute. Run `python manage.py migrate_schemas --shared` once for the new table.
- **Unknown shop address:** an address like `nobody.<SHOP_BASE_DOMAIN>` where no shop exists answers 404 "Shop not found" with a button to the main site (the main domain and `www.` still open the main site).
- Prices: free trial 30 days, then Rs 500 a month or Rs 5000 a year (GST included) (`PLAN_PRICES`, `TRIAL_DAYS` in settings).
- **Plan and payments page** (`/plan/`, menu *Master Data → Plan & Payments*, owner only, only on a shop's own address): the status (free trial, paid, locked), days left, prices, WhatsApp / e-mail buttons from the website settings, and the list of payments recorded for the shop. The banner at the top of a shop's pages links to it.
- **Plan reminders (e-mail):** the owner's e-mail (given at sign-up) gets one mail 2 to 7 days before the trial or paid plan ends, one in the last 2 days, and one after it has ended (only within 14 days). Each is sent once per end date (`Shop.last_reminder`, migration `tenants.0005`); a renewal starts afresh. A failed send is tried again the next night. Shops that are switched off get none. `python manage.py send_plan_reminders --dry-run` shows who would get one.
- **Nightly job for every shop:** `python manage.py shops_daily` takes a backup of every shop (also locked ones), runs `daily_tasks` (reorder levels, fixed expenses, health score and the summary e-mail) for shops on a trial or a paid plan, and sends the reminders. A failure in one shop never stops the others; the command exits with an error that lists them, so cron mail shows it. `--only s_name` (one shop), `--skip-backup`, `--skip-email`, `--skip-reminders`, `--keep N` are available. The main shop's backup (`medsync_<date>.pgdump`) is the **whole database**, all shops included, so keep it safe. The platform panel shows each shop's last backup and a red warning when a shop has none for 2 days.
- **Daily summary and stock-alert mails of a shop go only to that shop's own e-mail.** `DJANGO_ALERT_EMAILS` is used by the main shop only.
- **Cron on the server** (site user, `crontab -e`): `30 2 * * * cd ~/htdocs/medsync.socialflylive.com && ./venv/bin/python manage.py shops_daily >> ~/shops_daily.log 2>&1`. Use this line instead of an older `daily_tasks` line.
- **Restore one shop** (do it on the server, with the site stopped, and try it first on a copy): `pg_restore -h localhost -U <db user> -d <database> --clean --if-exists --no-owner <medsync_s_name_...pgdump>`. The file holds only that shop's schema, so other shops are not touched. To only look inside a backup, restore it into a new empty database instead.


## Forgot password

The sign-in page has a **Forgot your password?** link (`/forgot/`). The person types the email address of the account and
gets an email with a link to choose a new password (`/reset-password/...`). The link works for 24 hours and only once.

- It works the same on the main site and on every shop (users are separate per shop), and a shop whose plan has ended can still use it.
- The answer is identical whether or not the address exists, so nobody can find out who has an account.
- One address gets at most 3 mails an hour and one visitor at most 10 requests an hour.
- New passwords follow the normal password rules (at least 8 characters, not only numbers, not a common password).
- Emails go out through the same SMTP settings as the other mails (`DJANGO_EMAIL_*` in `.env`).
- Accounts without an email address cannot use this. For those: `python manage.py changepassword <username>`
  (for one shop: `python manage.py tenant_command changepassword --schema=s_<name>`).

## One sign-in page for every shop (main site)

With many shops on, everybody can sign in at the main site's `/login/` — nobody has to remember a shop address.

- **Owner:** types the email used at sign-up and the password. We find the shop by that email, check the password inside the shop,
  then send the person to his shop's own address, already signed in.
- **Staff:** types his username and password and opens "Staff member? Enter your shop address" to give the shop name
  (`sharma-medical`, the full address or a link all work). Usernames are only unique inside one shop, so staff need the shop name.
- **Main shop users** (and platform admins) sign in as before and stay on the main site.
- The shop's own address (`<name>.<base domain>/login/`) still works exactly as before.
- How the hand-over works: the main site and each shop are different web addresses, so a sign-in cannot be shared. The main site
  makes a one-time pass (table `tenants_logintoken`, valid for 1 minute, works once and only for that shop) and opens
  `/sso/<pass>/` on the shop, which then signs the person in.
- Wrong details always give the same message, and the same lock applies (5 wrong tries, then 15 minutes per address and name).
- "Forgot your password?" on the main site also reaches shop owners: the email link opens on the owner's own shop address.
- Needs `python manage.py migrate_schemas --shared` once (adds `tenants.0006_logintoken`).

## Deleting a shop (for tests and for shops that left)

Open the shop in the platform panel (`/superadmin/`, then the shop) and use **Delete this shop** at the bottom.

- You must type the first part of the shop's web address (shown on the page). Capital letters do not matter.
- First a last backup of that shop is saved in the backup folder (`medsync_s_<name>_<date>.pgdump`). If it cannot be saved, nothing is deleted.
- Then its data (products, bills, users), payments and web address are removed for good, together with any unconfirmed sign-up of the same email. The address and the email can be used to sign up again straight away.
- The main shop cannot be deleted. Only platform admins see this button.
- The last backup of a deleted shop is not cleaned up automatically. Delete the file by hand when you no longer need it.
- To restore such a shop, see "Restore one shop".

## One command update (`deploy.sh`)

On the server, as the site user:

```
~/htdocs/medsync.socialflylive.com/deploy.sh
```

It pulls the latest code, installs packages, runs `check`, updates the database of the main shop and every shop
(`migrate_schemas`), collects static files, restarts the app and checks `/healthz/`. It stops at the first problem.

To let the site user restart the app without a password, run this once **as root** (change the user name if yours differs):

```
echo 'socialflylive-medsync ALL=(root) NOPASSWD: /usr/bin/systemctl restart medsync' > /etc/sudoers.d/medsync-restart
chmod 440 /etc/sudoers.d/medsync-restart
visudo -c
```

Without this the script does everything except the restart and tells you to run `systemctl restart medsync` as root.

## Uptime check and crash e-mails

- `https://<your site>/healthz/` answers `{"status": "ok"}` (200) when the app and the database work, and 503 when not. Add it to
  a free uptime monitor (for example UptimeRobot) so you get a message when the site is down.
- A crash (HTTP 500) is e-mailed to `DJANGO_ERROR_EMAILS` in `.env` (comma separated). If that is not set, `DJANGO_ALERT_EMAILS` is used.
  The mail never contains submitted form values or cookies.

## Help page and the "Get your shop ready" card

- Every signed-in user has **Help and support** in the menu (`/help/`): how-to answers, and the WhatsApp or email contact from
  Website settings (many shops) or `LANDING_WHATSAPP` (one shop).
- The owner's dashboard shows a four-step card (shop details, products, stock, first bill). It ticks itself from the data, can be
  hidden for the session, and disappears when all four are done.

## Download all my data (Backup page, owner only)
`/backup/export/` (button "Download my data (zip)" on the Backup page) gives one zip with a CSV for every table of the shop plus a `README.txt` with the row counts. Text that starts with `=`, `+`, `-` or `@` gets an apostrophe in front so Excel never runs it as a formula. The export is written to the Audit log (action `EXPORT`), and one download per minute is allowed. The blog, import layouts, audit log and sign-in history are not included.

## Platform panel tools (`/superadmin/`)
* **Growth** numbers on the Shops page: new shops in 30 days, trials ending in 7 days, share of shops that have paid, shops with no sign-in for 14 days.
* **Notice inside every shop**: `/superadmin/site/` has a "Notice inside every shop" box (200 characters). It appears at the top of every page of every shop (cached up to one minute). Leave it blank to hide it.
* There is deliberately **no** way for the platform owner to sign in to a shop's panel: a shop's data is private to the shop. To help a shop, ask its owner to share his screen, or use the shop's own backup file. (An earlier "sign in for support" button was removed; migration `tenants 0008` drops its column.)

## Support tickets (shop to platform owner, no WhatsApp needed)
* A shop opens **Support tickets** in its menu (or the button on the Help page), picks a category and writes a subject and message (up to 4000 characters). Only what the shop types is sent, plus the page it was on (from the Referer header, same site only). A shop can send 5 new tickets and 20 messages per hour. A shop whose plan has ended can still write to support.
* The platform owner sees every ticket at `/superadmin/support/` (filters Open / Answered / Closed, search by subject, shop or text; the menu shows how many are open) and on each shop's page. Reply, Close and Reopen are on the ticket page. A reply sets the ticket to *Answered*; a message from the shop sets it back to *Open*.
* E-mails: a new ticket or reply goes to `DJANGO_ALERT_EMAILS` (or the contact e-mail in Website settings); a platform reply goes to the shop's e-mail. The shop also sees a "new reply" bar inside the app until it opens the ticket.
* A shop can open only its own tickets. Texts are shown as plain text. Needs `migrate_schemas --shared` (migration `tenants 0009`). Tickets exist only while many shops is on.

## Supplier ledger (Suppliers list, Ledger button)
Each supplier has a 📒 Ledger page: every purchase (+), purchase return (−) and payment (−) in date order with a running "You owe" balance, built from the records themselves. It is compared with the balance saved on the Suppliers page; if they ever differ, a red bar says so and the owner can press "Correct the saved balance" (written to the Audit log as `FIX`). The Suppliers list also has a "+ Payment" button that opens the payment form with that supplier already chosen. The rule behind the numbers: a purchase adds its **total price including GST** (the "Price (total incl. GST)" box, not the price of one strip), a payment subtracts what was paid, and "Due after" on the Payments page is the balance right after that payment.

## WhatsApp bill sharing
The 💬 WhatsApp button on every Final bill (in the Billing list, and on the invoice page) opens WhatsApp with the bill written out: shop name, bill number and date, a greeting for a named customer, up to 12 item lines (the rest are summed in one line), GST included, the total, how it was paid, and for a credit bill what was paid now and what is due. It is addressed to the customer's saved number (10 digits get +91); a cash bill opens WhatsApp so a contact can be picked. No WhatsApp API or cost: it uses a `wa.me` link, so the staff member presses Send. Cancelled bills cannot be shared.

## 2-step sign-in (optional)

Each person can turn it on under **Sign-in security** in the menu. After the password, a 6-digit code is e-mailed (valid 10 minutes, 5 tries, resend every 30 s). Turning it on needs a test code first, so it can never be switched on with a broken e-mail. 8 one-time **recovery codes** are shown once (only hashes are stored). "Remember this browser" skips the code for 30 days and stops working when the password changes.

If someone is locked out: the shop owner deletes the row under *Admin → Two factors*; the platform owner uses **Switch off 2-step** on the shop page (owner of that shop); on the server run `python manage.py two_step_off <username>`. The Django admin login goes through the normal login, so it cannot skip the code. Migration `backend 0020` is needed (`migrate_schemas --shared`).

## Sign-up e-mails

Two e-mails, both plain text + a neat HTML version (`tenants/mails.py`):
- **Confirmation** (at sign-up): a button and the link on its own line, valid 24 hours, "ignore this if it was not you".
- **Welcome letter** (right after the owner confirms and the shop is made): shop name, web address, sign-in e-mail, free-trial end date, five first steps (the first points to the Setup guide), how to get help, a 2-step and phone tip. It never contains the password. If it cannot be sent, the shop is still created. Everything a person typed is escaped in the HTML. Nothing to migrate.

## Starting Units and Categories

A new shop now starts with the usual Units (Tablet, Strip, Box, Bottle ...) and Categories (Tablet, Syrup, Injection ...). One list lives in `backend/defaults.py` and is shared by new shops, the data reset and `seed_master` (matched by name, no duplicates, nothing is changed or removed).
For shops made earlier, run once on the server: `python manage.py seed_shop_defaults --dry-run` to look, then `python manage.py seed_shop_defaults`. By default it fills only shops that have no units and no categories at all; `--all` adds the missing ones to every shop. No migration.

## Expenses and Fixed Expenses dropdowns

The **Expenses** form (by hand) offers only **Other** (the default): write what it was in the note. Rent, Electricity, Water, Maintenance and Salary are set up once in **Fixed Expenses** (the dropdown there is unchanged), and their entries arrive in Expenses by themselves. **Salary** shows in the Expenses form only when it is opened from the Salary Sheet's "+ Add salary" button. An old or automatic entry keeps its own type and can still be opened and corrected. The data model is unchanged (no migration), so reports and the dashboard split by type work as before.

## Maintenance mode (many-shop setup)

For updates that need a short break. Platform panel, **Maintenance** (`/superadmin/maintenance/`):
- **Plan it for later** (date, time, minutes, optional message, optional one-time e-mail to every shop owner). From that moment every shop shows a banner: "Planned maintenance: ... save your work before".
- **Start right now** (needs a tick) and **End maintenance now**, **Extend**, **Cancel the plan**.
- Inside the window every shop answers with a plain "We are updating MedSync" page (HTTP 503, refreshes itself every 30 seconds, shows your WhatsApp/e-mail). New sign-ups are paused with a polite message and their e-mail links stay valid. `/healthz/` keeps answering, so uptime monitors do not raise a false alarm.
- **The platform panel keeps working the whole time** (you are not locked out and can check the update). No shop's panel is ever opened for you.
- The window ends by itself at its end time. It is kept in `maintenance.json` next to `manage.py` (not in the database, so it also works while the database is being updated). A missing or damaged file means "no maintenance".
- Server commands: `python manage.py maintenance status | on --minutes 20 --message "..." | schedule --start "2026-10-28 23:00" --minutes 30 | extend --minutes 15 | off`. `off` always works.
- One click with the update: `~/htdocs/medsync.socialflylive.com/deploy.sh --maintenance` (or `--maintenance=30`). It switches maintenance on after pulling the code, updates, restarts, and switches it off only when the site answers. If a step fails it stays on and says how to switch it off. (The first deploy of this feature cannot switch it on yet, because the command does not exist before that deploy.)
- `deploy.sh` now reads itself completely before it starts, so `git pull` replacing the script in the middle of a run can no longer confuse it.
- For the few seconds while the app itself restarts, nginx cannot ask the app for a page. `deploy/maintenance.html` is a ready page for that: copy it to the server and let nginx show it for 502/503/504 (see the comment at the top of that file).

## Setup guide (for new shops)

Menu: **Setup guide** (owner only, `/setup/`). It tells a new shop what to enter and import, in the order that works: shop details, suppliers, medicines, opening stock (with batch and expiry), first bill. Every step has "why it matters", tips, and buttons to the page, the Excel template and the import. Steps tick themselves from the shop's data; nothing is written. Below that: "Also worth doing" (GSTIN and Drug Licence, credit customers, what you owe suppliers, staff logins, 2-step sign-in, a backup copy) and "Check your data" (medicines without a unit or MRP, batches with stock but no expiry date, suppliers without a phone number), each with a link to fix it.
The dashboard card "Get your shop ready" now has five steps and a button to the guide. A brand new shop (nothing done yet) also gets a one-time welcome note (remembered in the browser). Products cannot be added one by one: they are created by the Products import or by the stock entry/import, and the guide says so. No migration; the code is `backend/onboarding.py`.
