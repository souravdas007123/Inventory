"""'Forgot your password?' for every shop and for the main shop: an e-mail with a one-time link, then a new password.

It uses Django's own signed, time-limited, single-use links (the link stops working once the password is changed). The
answer is the same whether or not the address is known, so nobody can use it to find out who has an account. Each address
and each visitor can ask only a few times an hour."""
import hashlib

from django.contrib.auth.forms import SetPasswordForm
from django.contrib.auth.views import PasswordResetConfirmView, PasswordResetView
from django.core.cache import cache
from django.shortcuts import redirect, render
from django.urls import reverse_lazy

from .security import client_ip

MAX_PER_ADDRESS = 3          # mails an hour to one address
MAX_PER_VISITOR = 10         # requests an hour from one network address


def _too_many(key, limit, seconds=3600):
    n = cache.get(key, 0) + 1
    cache.set(key, n, seconds)
    return n > limit


def _hash(text):
    return hashlib.sha256(text.encode()).hexdigest()


class ForgotView(PasswordResetView):
    template_name = 'app/forgot.html'
    email_template_name = 'app/forgot_email.txt'
    subject_template_name = 'app/forgot_subject.txt'
    success_url = reverse_lazy('forgot_sent')

    def form_valid(self, form):
        email = form.cleaned_data['email'].strip().lower()
        visitor = _too_many('forgot-ip:' + _hash(client_ip(self.request)), MAX_PER_VISITOR)
        address = _too_many('forgot-mail:' + _hash(email), MAX_PER_ADDRESS)
        if visitor or address:                               # silently nothing: the page looks the same
            return redirect(self.success_url)
        return super().form_valid(form)


class ConfirmView(PasswordResetConfirmView):
    template_name = 'app/reset_confirm.html'
    form_class = SetPasswordForm
    success_url = reverse_lazy('reset_done')


def sent(request):
    return render(request, 'app/notice.html', dict(
        title='Check your email',
        message='If an account uses that email address, we have sent a link to choose a new password. '
                'The link works for 24 hours. Please also look in the spam folder.',
        link_url='/login/', link_text='Back to sign in'))


def done(request):
    return render(request, 'app/notice.html', dict(
        title='Your password was changed', message='You can now sign in with your new password.',
        link_url='/login/', link_text='Sign in'))
