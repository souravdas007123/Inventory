"""The platform owner's "Server health" page (/superadmin/server/): how busy the server is. Read-only."""
from django.contrib import messages
from django.shortcuts import redirect, render

from . import serverstats
from .platform import platform_admin_required


@platform_admin_required
def server_page(request):
    if request.method == 'POST':                    # "Measure again now"
        serverstats.collect(force=True)
        messages.success(request, 'Measured again.')
        return redirect('platform_server')
    data = serverstats.collect()
    return render(request, 'app/platform_server.html', dict(d=data, worst=serverstats.worst_level(data)))
