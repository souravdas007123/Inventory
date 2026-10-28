"""The first FAQ and the first text of the legal pages. They are copied into the database once (see migration 0022), after
which the owner edits them in the admin. They also serve as the fallback if the database cannot be read."""

FAQS = [('Where is my data stored?',
  'In a private space made only for your shop. Other shops cannot see it, nothing is sold or shared, and a compressed backup is '
  'taken every day.'),
 ('Do I need special hardware?',
  'No. MedSync runs in a web browser on any computer, tablet or phone. A barcode scanner works like a keyboard.'),
 ('Can my accountant use it?',
  'Yes. One Excel export has the sales and purchase registers, credit and debit notes, GST by rate, expenses and payments. The '
  'GST page also gives GSTR-1, GSTR-3B and HSN summaries.'),
 ('Is Schedule H and H1 selling tracked?',
  'Yes. Billing asks for the doctor and patient details for scheduled drugs, and the drug register exports to Excel.'),
 ('Can I move my existing stock into MedSync?',
  'Yes. Products, stock and purchases can be imported from Excel or CSV. You see a preview and any problems before anything is '
  'saved.'),
 ('What happens to expired medicines?',
  'Expired batches are never billed. They are listed on the expiry page, and you can return them to the supplier or write them '
  'off. Stock and accounts adjust automatically.'),
 ('Can several staff use it at once?',
  'Yes. Each person has a login with a role, so billing staff, purchase staff and the owner each see only what they need.'),
 ('How do I know the numbers are right?',
  "The owner's health score compares stock with batches, opens the latest backup to check it, times the pages on the server and "
  'lists every problem with its reason.')]

LEGAL_DESCRIPTIONS = {'privacy': 'What MedSync collects, why, who can see it, how it is protected and how long it is kept. Your shop data stays '
            'private to your shop.',
 'refund': 'How the free trial, plans, cancellation and refunds work at MedSync. Try free first, stop any time and get a refund '
           'for mistakes.',
 'terms': 'The rules for using MedSync: your account, the free trial and plans, your data, acceptable use and what we are '
          'responsible for.'}

LEGAL_BODIES = {
    'terms': '''
These terms explain how you may use MedSync, the online pharmacy billing and inventory service. By creating a shop on MedSync you agree to them. If you do not agree, please do not use the service.

## 1. The service

MedSync helps a medical store keep stock, bill customers, track batches and expiry and prepare reports for its accountant. It is provided over the internet, in a web browser.

## 2. Your account

- Give correct details when you sign up, and keep them up to date.
- Keep your password private. You are responsible for what is done with your sign-in and with the sign-ins of the staff you create.
- You must be allowed to run the shop you register, and to enter the data you put in MedSync.

## 3. Your responsibilities as a shop

MedSync is a tool. You remain responsible for your bills, your GST returns, your drug licence and register, and for following the laws that apply to your shop. Reports are calculated from what you enter, so please check important figures with your accountant. Nothing in MedSync is tax, legal or medical advice.

## 4. Free trial and plans

A new shop gets a free trial. After the trial you choose a monthly or a yearly plan at the price shown on our home page. Every plan has every feature. If the trial or a plan period ends, your shop becomes read-only: you can still see your data, but new entries are switched off until you choose a plan. Your data is not removed because a plan ended.

## 5. Your data

The data you enter belongs to you. We use it only to run the service for you, as explained in the [privacy policy](/privacy/). Your reports can be exported to Excel at any time, and you may ask us for a copy of your data.

## 6. Acceptable use

Please do not use MedSync for anything illegal, try to get into another shop's data, test or attack our security without our written permission, or put a load on the service that affects other shops.

## 7. Availability and backups

We work to keep MedSync available and take a backup of every shop every day. We cannot promise that the service will never be interrupted. Keep your own exports of the reports you need.

## 8. Ending your use

You can stop using MedSync at any time, and you can ask us to delete your shop. A last backup copy may be kept for a short time for safety and is then removed. We may switch off a shop that is used against these terms, after telling you why where we can.

## 9. Our responsibility

We provide MedSync with reasonable care, and as it is. As far as the law allows, we are not responsible for indirect loss, such as lost profit, and our total responsibility for any claim is limited to the amount you paid us in the last twelve months. Nothing here limits a right you have by law.

## 10. Changes

We may update these terms. We will show the new date above, and tell you about important changes on the site or by email. Using MedSync after a change means you accept it.

## 11. Law

These terms follow the laws of India.
''',
    'privacy': '''
MedSync keeps the data of your shop private. This page explains in plain words what we collect, why, and what you can ask of us.

## What we collect

- Your account: your name, email address, mobile number, shop name and a password (stored only in a scrambled form that we cannot read).
- Your shop data: what you enter to run the shop, such as products, stock, purchases, bills, customers and their credit, expenses, staff logins and, for scheduled drugs, the doctor and patient details the law asks for.
- Technical records: the time and network address of sign-ins and important changes (this is the audit log your owner can see), and error records we need to fix problems.

## Why we use it

Only to run MedSync for you: to let you sign in, to show and calculate your shop's figures, to take backups, to send you the emails the service needs (such as confirming your email address, a password reset link, and plan reminders) and to help you when you ask. We do not sell your data, and we do not use it for advertising.

## Who can see it

- You and the staff you give a login to, each according to their role.
- Each shop's data is kept in its own separate space. Another shop cannot see it.
- The people who run MedSync can reach the system for support, backups and repairs. We look at your shop's data only when this is needed to help you or to keep the service safe.
- Service providers that help us run MedSync, such as the company that hosts our servers and the service that delivers our emails. They handle data only for this purpose.
- Authorities, only when the law requires it.

## Your customers' data

Your shop decides what customer details to keep. MedSync stores them for you and does not use them for anything else. Please keep to the privacy rules that apply to your shop.

## How it is protected

Sign-in runs over a secure connection. Repeated wrong passwords lock a sign-in for a while. Important changes are written to an audit log. Every shop is backed up every day. No system is perfect, so please also keep your own exports and use a strong password.

## Cookies

We use only the cookies the service needs: one that keeps you signed in and one that protects forms from misuse. We do not use advertising or tracking cookies.

## How long we keep it

We keep your shop's data while you use MedSync. If you ask us to delete your shop, we remove it, and a last backup copy is kept for a short time for safety and then removed. We may keep records that the law asks us to keep.

## Your choices

You can ask us to show you, correct or delete your data, or to stop sending you reminder emails, by contacting us below. You can also correct most of your details yourself inside MedSync.

## Changes

If we change this policy, we will show the new date above and tell you about important changes.
''',
    'refund': '''
We want you to be sure before you pay. That is why every new shop starts with a free trial.

## Free trial

The trial is free and needs no card. You can use every feature. When it ends, the shop becomes read-only until you choose a plan. Nothing is deleted.

## Plans

A plan is paid in advance for the period you choose, one month or one year, at the price shown on the home page. Every plan has every feature.

## Cancelling

You can stop at any time by not renewing. Your shop stays active until the end of the period you have paid for. After that it becomes read-only, and you can ask us for your data or for your shop to be deleted.

## Refunds

- If you were charged by mistake, or paid twice for the same period, we refund the extra amount in full.
- If a serious fault on our side stopped you from using MedSync for a long time, tell us. We will extend your plan or refund the unused part.
- Apart from this, a payment for a period that has started is not refunded, because you could try the service free first.

Refunds go back by the way you paid, usually within 7 working days after we confirm them.

## How to ask

Write to us with the email address of your shop and the date and amount of the payment. We reply as soon as we can.
''',
}
