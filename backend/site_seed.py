"""First versions of the pricing plans and the website text pages. Added once by migration 0020; edit them in the admin
(Business -> Subscription Plans, Website -> Website Text Pages). Nothing here fixes a price."""

PLANS = [
    dict(name='Starter', tagline='For a shop that wants clean billing and stock', sort_order=10, highlight=False,
         features='\n'.join([
             'GST billing with barcode scan',
             'Products, batches and expiry (oldest batch sold first)',
             'Purchases and supplier balances',
             'Customers and credit ledger',
             'Daily backup',
         ])),
    dict(name='Standard', tagline='For a shop that wants control over buying and accounts', sort_order=20, highlight=True,
         features='\n'.join([
             'Everything in Starter',
             'Supplier price comparison and reorder suggestions',
             'Alternative medicine suggestion and hold / resume bills',
             'Schedule H, H1 and X drug register',
             'GST reports (GSTR-1, GSTR-3B, HSN) and one-click Excel for your accountant',
             'Profit and loss with expenses',
         ])),
    dict(name='Pro', tagline='For a busy shop with several staff', sort_order=30, highlight=False,
         features='\n'.join([
             'Everything in Standard',
             'Several staff logins, each with their own role',
             'Health score and audit log of important changes',
             'Help importing your existing stock from Excel',
         ])),
]

PAGES = [
    dict(slug='pricing-included', title='Included in every plan', body='''
- Your data stays in your own database, and you can export it to Excel at any time.
- Daily backup of your data.
- Login protection, roles and an audit log.
- Works in any web browser on a computer, tablet or phone.
'''),
    dict(slug='terms', title='Terms of Service', body='''
These terms describe how MedSync, a pharmacy inventory and billing application, is provided to you. By using it you agree to them.

## The service
MedSync is provided as a web application for managing the stock, purchases, sales, accounts and reports of a medical store. The plan you choose, its price and what it includes are as shown on the pricing page or agreed with you in writing.

## Your account
- You are responsible for keeping your login details safe and for what is done with them.
- Give each staff member their own login, and remove the login of anyone who leaves.
- Tell us at once if you think someone else has used your account.

## Your data
- The data you enter (products, bills, customers, suppliers and so on) belongs to you.
- We use it only to provide the service to you, and we do not sell it.
- You can export your data to Excel at any time.

## Your responsibilities
- You are responsible for the correctness of what you enter, and for your own GST returns, drug licence records and other legal duties. MedSync helps you keep records and prepare reports, but it does not file returns for you and is not legal, tax or medical advice.
- Check important reports and keep your own copies of important records. A daily backup is taken, but you should also keep copies you control.

## Payment and renewal
Fees, billing periods and renewal dates are described in your invoice or agreement. If a payment is not made, we may pause the service after telling you.

## Stopping the service
You can stop using MedSync by telling us. How the current paid period is treated is described in your invoice or agreement. See also the Refund and Cancellation page.

## Availability and changes
We work to keep the service running and to fix problems quickly, but we cannot promise that it will always be available or free of errors. We may improve or change features over time, and we will tell you about changes that matter to you.

## Limit of liability
To the extent the law allows, our liability for any loss arising from the use of MedSync is limited to the fees you paid for the service in the three months before the event that caused the loss. We are not liable for indirect or consequential loss.

## Changes to these terms
We may update these terms. The date of the latest change is kept on this page, and continuing to use the service after a change means you accept it.

## Law
These terms are governed by the laws of India.

## Contact
Use the contact button on this website to reach us with any question about these terms.
'''),
    dict(slug='privacy', title='Privacy Policy', body='''
This page explains what information MedSync handles and why.

## This website
- This website does not ask you to create an account and does not use advertising or tracking cookies.
- If you contact us, for example by WhatsApp, we receive the details you choose to send us, such as your name and phone number. We use them only to answer you and to set up the service if you ask for it.

## The application
- A shop that uses MedSync stores its own business data in it: products, stock, purchases, bills, customers, suppliers, expenses and staff logins. Some of this may include personal details, such as customer names and phone numbers, and patient and doctor names for scheduled medicines.
- This data is stored in the shop's own database. We use it only to provide the service, for example to keep it running and to restore a backup when you ask.
- We do not sell your data and we do not share it with anyone, except where the law requires it.
- The application uses cookies only to keep you logged in and to protect forms from misuse.

## Backups
A compressed backup of the database is taken every day and kept for a limited time. Backups have the same data as the live database and are protected in the same way.

## Security
Logins are protected, repeated wrong passwords are blocked for a while, roles limit what each person can see, and important changes are recorded in an audit log. No system is perfectly secure, so please use strong passwords and keep them private.

## Your choices
You can ask to see, correct or delete the data we hold about you, or to export your shop's data, by contacting us. When a shop stops using MedSync, we delete its data when it asks us to, unless the law requires us to keep it.

## Changes
We may update this policy from time to time, and the latest version is always on this page.
'''),
    dict(slug='refund', title='Refund and Cancellation', body='''
## Stopping
You can stop using MedSync at any time by telling us. How the period you have already paid for is treated is described in your invoice or agreement.

## Refunds
Refunds, if any, are described in your invoice or agreement. If you think you were charged by mistake, or the service did not work as promised, contact us and we will look into it and tell you what we can do.

## Your data after you stop
You can export your data to Excel before you stop. When you ask us to delete your shop's data, we will do so unless the law requires us to keep it.

## Contact
Use the contact button on this website to reach us.
'''),
]
