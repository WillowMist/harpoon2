from harpoon2 import settings
from _version import __version__
import sys

def custom_proc(request):
    # A context processor that provides 'app', 'user' and 'ip_address'.
    interface = settings.INTERFACE
    
    if hasattr(request, 'user') and request.user.is_authenticated:
        if hasattr(request.user, 'interface') and request.user.interface:
            interface = request.user.interface

    ip_address = request.META.get('REMOTE_ADDR', '0.0.0.0')

    return {
        'brandingname': 'Harpoon',
        'interface': interface,
        'ip_address': ip_address,
        'version': __version__,
    }