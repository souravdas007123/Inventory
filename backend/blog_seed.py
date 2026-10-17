"""The first articles of the blog. They are added once by migration 0016 and can then be edited or unpublished in the admin."""

POSTS = [
    dict(
        slug='how-to-track-medicine-expiry-in-a-medical-store',
        title='How to track medicine expiry in a medical store (and stop losing money on it)',
        summary='A simple routine to catch expiring medicines early: batch-wise stock, a monthly expiry list and returns to the supplier before it is too late.',
        body='''
Expired medicine is a quiet loss. It does not show up on any bill, so it often goes unnoticed until the day you clean a shelf and count what you have to throw away. The good news is that almost all of it can be avoided with a small routine.

## Why expiry loss happens

- **Stock is tracked only as a total.** "40 strips of Dolo" tells you nothing about which strips expire first.
- **Nobody looks until it is too late.** An expiry check done once a year finds medicines that are already past returning.
- **The oldest stock is hidden at the back.** New stock goes in front, so the old batch sits there until it expires.

## Step 1: Track stock batch by batch

Every purchase brings a batch number and an expiry date. Keep both. When you know that you hold 28 tablets of batch A117 expiring in March and 50 of batch B2231 expiring in August, you can make decisions. A single total cannot help you.

## Step 2: Sell the oldest batch first

The rule is called FEFO: first expiry, first out. At the counter, always sell the batch that expires first. Doing this by memory fails on a busy day, so it is better when the billing screen picks the batch for you. MedSync does exactly this, and it never bills an expired batch.

## Step 3: Look at expiry every month

Once a month, list everything that expires in the next 60 to 90 days. For each item you have three choices:

1. **Return it to the supplier.** Most suppliers accept returns of near-expiry stock if you ask early, and the window is usually a few months before expiry. Ask your suppliers for their exact policy.
2. **Sell it faster.** Move it to the front, mention it to staff, or offer it with a related medicine.
3. **Write it off.** If nothing else works, record it as a loss so your stock and your accounts stay correct.

## Step 4: Record the return or write-off properly

A return that is only handed over and never recorded leaves a wrong stock count and a wrong supplier balance. When you record a purchase return or a write-off, stock and accounts should adjust together. In MedSync, a purchase return creates a debit note and reduces the supplier's balance, and a write-off removes the batch from stock.

## A simple monthly checklist

1. Open the list of batches expiring within 90 days.
2. Separate what can go back to each supplier.
3. Prepare the returns and record them.
4. Tell staff which items to push this month.
5. Write off anything that is already expired.

Ten minutes a month is enough. The money you save is usually far more than the cost of the time.
''',
    ),
    dict(
        slug='pharmacy-gst-billing-gstr-1-and-gstr-3b-explained',
        title='Pharmacy GST billing: GSTR-1, GSTR-3B and HSN explained in plain language',
        summary='What a medical store needs to record for GST, what GSTR-1 and GSTR-3B contain, and how clean bills make your monthly filing easier.',
        body='''
GST filing becomes stressful when the bills are not clean. If every bill already carries the right information, the monthly return is mostly a matter of adding things up. This article explains what the main returns contain, so you know what your billing must capture. Always confirm rates and due dates with your accountant and the GST portal, because they change.

## What GST needs from every sale

- **The GST rate of each product.** Different medicines can have different rates, and the rate can change. Keep it on the product, and do not retype it on each bill.
- **The HSN code of each product.** This is the classification code used in your GST summary.
- **The customer's GSTIN, if the customer is a business.** A sale to a GST-registered buyer is a B2B sale and is reported with that GSTIN. A sale to an ordinary customer is B2C.
- **The invoice number and date,** in a continuous series.

## GSTR-1: what you sold

GSTR-1 is the statement of your sales (outward supplies). In simple terms it lists:

- **B2B sales:** invoice by invoice, with the buyer's GSTIN.
- **B2C sales:** usually summed up by tax rate.
- **Credit notes:** sales returns that reduce your sales.
- **HSN summary:** your sales grouped by HSN code, with quantity and value.

## GSTR-3B: what you owe

GSTR-3B is the summary return in which you pay the tax. It brings together:

1. The tax on your sales for the month.
2. The input tax credit (ITC) you can claim on your purchases.
3. The tax you actually pay: sales tax minus the input credit.

This is why purchase invoices matter. If a purchase bill is missing or wrong, you may lose input credit.

## Returns and credit notes

When a customer returns medicine, record a sales return so that a credit note is created. It reduces your sales and your tax. In the same way, when you return stock to a supplier, record a purchase return so that a debit note is created and your input credit adjusts.

## What makes filing easier

- Fix the GST rate and HSN once on each product.
- Never delete a bill. Cancel it, so the number series stays complete.
- Record purchases the day they arrive, with the supplier's invoice number.
- Keep your GST report open on the same day each month and compare it with your books before filing.

MedSync has a GST page with GSTR-1 sections (B2B, B2C and credit notes), a GSTR-3B summary and an HSN summary, and each can be exported to Excel. There is also one accountant export with the registers and notes together. It does not file the return for you: your accountant still files it on the portal.
''',
    ),
    dict(
        slug='schedule-h-h1-x-drug-register-what-medical-stores-must-record',
        title='Schedule H, H1 and X: what a medical store needs to record',
        summary='A plain explanation of the drug register for scheduled medicines: what details to note, why billing should ask for them, and how to stay inspection-ready.',
        body='''
Scheduled drugs are sold only against a valid prescription, and some of them must be written in a register. Drug inspectors look at this register closely. This article gives a practical view of what to record. The rules differ by state and change over time, so check the current rules with your state drug control office before you rely on any detail here.

## The three groups in short

- **Schedule H:** prescription drugs. They are sold only on a valid prescription from a registered doctor.
- **Schedule H1:** a stricter group, which includes certain antibiotics and other drugs that are easily misused. A separate register has to be kept.
- **Schedule X:** drugs with the strictest control, with their own record and storage requirements.

## What the register usually needs

For each sale of a scheduled drug, shops are expected to note details such as:

1. Date of sale and bill number.
2. Name of the drug, batch number and quantity.
3. Name and address of the patient.
4. Name and registration number of the prescribing doctor.

Records have to be kept for a fixed number of years, which is longer for some schedules. Confirm the exact period for your state.

## Why the register goes wrong

- Details are written later from memory, so they are incomplete.
- The register is a separate paper book, so a sale and its entry do not match.
- Nobody can quickly find all sales of one drug during an inspection.

## Make billing ask for the details

The most reliable way to keep a correct register is to collect the details at the moment of billing. When the product is marked as Schedule H, H1 or X, the bill should not be saved until the doctor and patient details are filled in. Then every scheduled sale has an entry, because the entry is made by the bill itself.

MedSync works this way. A product can be marked with its schedule, billing requires the doctor and patient fields for scheduled drugs, and the drug register lists these sales by date and exports to Excel, so you can hand it over during an inspection.

## Before an inspection

1. Open the register for the period and check that every scheduled bill has an entry.
2. Compare the stock of scheduled drugs with the purchase and sale records.
3. Keep the prescriptions that the rules ask you to keep.
4. Keep your drug licence and the pharmacist's registration where they can be seen.

A register that fills itself at the counter is far less work than one that has to be rebuilt in a hurry.
''',
    ),
    dict(
        slug='what-is-fefo-and-why-every-pharmacy-needs-it',
        title='What is FEFO, and why every pharmacy needs it',
        summary='FEFO means first expiry, first out. Learn how it differs from FIFO, why it matters for medicines and how to make it happen without relying on memory.',
        body='''
FEFO stands for **first expiry, first out**. It means that you always sell the batch that expires first, whatever the order in which it arrived.

## FEFO and FIFO are not the same

FIFO (first in, first out) sells the stock that came in first. That works for many goods, but medicines are different, because two batches of the same product can have very different expiry dates. A batch that arrived later can expire earlier. If you follow FIFO, you may sell the long-dated batch and keep the short-dated one until it expires.

For medicines, expiry is what counts, so FEFO is the correct rule.

## An example

You hold two batches of the same medicine:

- Batch A117: 28 tablets, expiry March 2027, bought in July.
- Batch B2231: 50 tablets, expiry August 2027, bought in September.

A customer asks for 30 tablets. With FEFO, 28 come from A117 and 2 from B2231. You clear the batch that is closest to expiry first. With FIFO the answer is the same here, but if B2231 had arrived first, FIFO would take it first and leave A117 to expire.

## Why it is hard to do by hand

- Shelves hold many batches, and staff pick whatever is at the front.
- Batch and expiry are printed in small letters on the strip.
- On a busy day, nobody checks.

## How to make FEFO automatic

1. **Record the batch and expiry at purchase.** FEFO is impossible without this data.
2. **Let billing choose the batch.** The system should pick the unexpired batch with the nearest expiry and reduce its quantity.
3. **Block expired batches.** An expired batch should never be sold.
4. **Show the batch on the bill.** It helps you and your customer, and helps with returns later.

MedSync follows these four steps. A purchase creates batches, billing takes from the oldest valid batch, and expired stock cannot be billed.

## What FEFO does for your shop

- Less stock is thrown away.
- Your customers get medicine with a longer remaining life.
- Your stock is easier to trust, because every sale reduces a specific batch.

It is a small rule with a large effect, and it costs nothing once the software does it for you.
''',
    ),
    dict(
        slug='compare-supplier-prices-and-save-on-every-purchase',
        title='How to compare supplier prices and save on every purchase',
        summary='Supplier prices drift quietly. Here is how to compare the real price per strip, spot a sudden increase and choose who to order from.',
        body='''
Most medical stores buy from the same two or three suppliers out of habit. That is convenient, but prices drift, and a supplier who was the cheapest last year may not be the cheapest today. A small comparison habit can save a surprising amount over a year.

## Compare the same thing

Prices are easy to compare wrongly. Make sure you compare:

- **The same unit.** A price for a box of 10 strips is not comparable with a price per strip. Convert everything to the base unit, such as per tablet or per strip.
- **The same tax basis.** Compare prices including GST, or all excluding it, but not a mix.
- **The same pack.** Different pack sizes of the same medicine can look cheaper or dearer than they are.

## Look at the trend, not only the last price

One low price may be a one-time offer. Look at what each supplier charged over the last few purchases. A supplier whose price rose 10% in two months deserves a question, and a call to another supplier.

## What to watch for

1. **Price jumps.** A rise of 10% or more in a short time.
2. **The cheapest current supplier,** not the cheapest ever.
3. **Odd entries.** A price that is 40% above or below your usual is often a typing mistake, and sometimes a billing mistake by the supplier.
4. **Duplicate invoices.** The same invoice number entered twice doubles your stock and your payable.

## Decide who to order from

When you create an order, a sensible rule is: use the product's usual supplier if you have set one, otherwise the supplier with the lowest current price, otherwise the supplier of the last purchase. Order quantities should be in whole packs.

## Let the software do the comparing

MedSync builds the comparison from your existing purchases, so you do not enter anything extra. For each product it shows what each supplier charged, who is cheapest now, the trend and the full history, per base unit and per pack, including GST. On the purchase screen it shows the last price from the selected supplier and how the price you type compares. The dashboard lists supplier prices that rose 10% or more in the last 60 days, with a cheaper supplier if there is one. You can export the comparison to Excel.

## A one-month experiment

For one month, check the comparison before each large order. Note every time a different supplier turns out cheaper. That list is your saving, and it also gives you something to talk about with your regular supplier.
''',
    ),
    dict(
        slug='moving-your-medical-store-from-excel-to-pharmacy-software',
        title='Moving your medical store from Excel to pharmacy software: a safe checklist',
        summary='A step-by-step plan to switch from Excel or paper to pharmacy software without stopping sales, losing stock data or confusing your staff.',
        body='''
Many medical stores start with Excel and paper bills. It works until the shop grows, and then expiry, GST and supplier balances become hard to follow. Switching feels risky, but it is manageable if you do it in steps and keep the old files safe.

## Before you start

1. **Keep a copy of your Excel files.** Never delete them. They are your fallback.
2. **Choose a quiet time to switch,** such as the start of a month, so your reports are clean from one date.
3. **Decide who does what.** One person should own the stock list during the move.

## Step 1: Clean your product list

A messy list becomes a messy system. In Excel, check that:

- Each medicine appears once, with one spelling.
- The unit is clear: tablet, strip, bottle.
- If you sell in two units, you know the conversion, for example 1 strip = 10 tablets.
- The GST rate and HSN code are filled in.

## Step 2: Bring in your stock

Look for software that lets you import from Excel and shows a **preview** before saving. A good preview tells you which columns are required, which rows have a problem and why. It should also let you map your own column names, because nobody's sheet matches the software's.

## Step 3: Add batch and expiry

Stock without expiry hides your biggest risk. If your Excel list has no batches, take the time to add at least the nearest expiry for each product during your first physical count. Do it for fast-moving medicines first.

## Step 4: Do a physical count

Count the shelves and compare with the system. Fix the differences before you start billing. From this point on, the system should be the single source of truth.

## Step 5: Run in parallel for a few days

For two or three days, keep your old way and the new way side by side for a small set of bills. Compare totals at the end of each day. When they match for several days, stop the old way.

## Step 6: Train your staff on the three things that matter

- How to find a medicine and bill it.
- What to do when something is out of stock or unusual.
- How to hold and resume a bill when a customer is undecided.

Keyboard shortcuts speed things up a great deal once staff know them. In MedSync, for instance, one key searches, Enter moves to the next field and one key saves the bill.

## Step 7: Check the backup

Before you rely on the new system, make sure a backup is taken every day, and that you have seen one open correctly. A system without a working backup is a risk, however good it is.

## After the move

Give it a week, then look at what the reports tell you: what is about to expire, what is running low, which supplier is cheapest. That information was hard to get from Excel, and it is the real reason to switch.
''',
    ),
]
