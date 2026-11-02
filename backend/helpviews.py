"""Help page (how to do the common things and how to reach us), the setup-card switch and the uptime check."""
from django.contrib.auth.decorators import login_required
from django.db import connection
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from . import access, onboarding, tenancy
from .landing import contact_links

GUIDES = [
    ('How do I make a bill?', 'Open Billing and press New bill. Scan the barcode or type the medicine name, press Enter to move on, and press F9 to save. The rate fills in from the MRP, and the oldest batch is sold first.'),
    ('How do I bring in my existing stock?', 'Open Import from Excel in the menu. Upload your product sheet first, check the preview and save. Then enter your stock as a purchase so every medicine gets a batch with an expiry date.'),
    ('How do I see what is about to expire?', 'The dashboard shows what expires in the next 30 days. The Batch and Expiry page lists every batch, and from there you can return stock to the supplier or write it off.'),
    ('How do I add staff?', 'Open Users in the menu (owner only) and add a login. Give each person a role, so billing staff and purchase staff see only the pages they need.'),
    ('What if the customer wants medicine I do not have?', 'On the bill, the out-of-stock row suggests medicines with the same composition that you have in stock, cheapest first. One tap replaces the row. On any medicine that is in stock, the button "Same salt in stock" shows the cheaper brands. The Medicine Finder page (Stock menu) does the same for any name or salt you type.'),
    ('How do I see what a customer bought before?', 'On the bill, pick the customer: their usual medicines appear under the customer box (one tap adds it to the bill), with their last bills. The Customer History page finds bills by customer, patient or doctor.'),
    ('How do I collect the money customers owe me?', 'Open Payment Reminders (Customers menu). It lists everyone who owes you money, the biggest amount first, and since when. Press WhatsApp: WhatsApp opens with the message already written and you only press Send.'),
    ('Which medicines sell slowly, and where is my money stuck?', 'Open Fast and Dead Stock (Stock menu). It shows which medicines sell fast, which sell slowly, and which sold nothing in the last 30 to 180 days, with the value of the stock. Return slow stock to the supplier before it expires.'),
    ('Can I bill when the internet is down?', 'Yes. Open New Bill once while you are online, then keep billing: medicines are searched from a copy kept on this device, and each bill is saved on the device. A badge at the bottom left shows the bills that are waiting. They are sent, and get their invoice number, as soon as the internet is back. If stock ran out meanwhile, the badge turns red and you can fix the bill.'),
    ('How do I send my accountant the figures?', 'Open the CA export page, choose the period and download the Excel file. The GST page also gives GSTR-1, GSTR-3B and HSN summaries.'),
    ('Is my data backed up?', 'Yes. A backup of your shop is taken every day. The owner can see the latest ones on the Backup page and download a copy.'),
    ('I forgot my password.', 'On the sign-in page press Forgot your password and enter your email. We send a link to choose a new one. Staff without an email can ask the owner to set a new password in Users.'),
]


@login_required
def help_page(request):
    return render(request, 'app/help.html', dict(active='help', guides=GUIDES, contact=contact_links(), tickets_on=tenancy.on_shop_site(), is_owner_user=access.is_owner(request.user)))


@access.owner_required
def setup_page(request):
    """The Setup guide: what to enter and import first, in the right order, with the mistakes to avoid. Owner only."""
    return render(request, 'app/setup.html', dict(active='setup', g=onboarding.guide(request.user)))


@login_required
@require_POST
def hide_setup(request):
    request.session['hide_setup'] = True
    return redirect('/')


@require_GET
@never_cache
def healthz(request):
    """For an uptime monitor: 200 and {"status": "ok"} when the app and its database answer, 503 when not."""
    try:
        with connection.cursor() as cur:
            cur.execute('SELECT 1')
            cur.fetchone()
    except Exception:
        return JsonResponse({'status': 'down'}, status=503)
    return JsonResponse({'status': 'ok'})
