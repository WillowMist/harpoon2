from django.urls import reverse_lazy, reverse
from django.views.generic.edit import CreateView
from django.shortcuts import get_object_or_404, render
from django.http import HttpResponseRedirect, HttpResponse, JsonResponse
from .forms import CustomUserCreationForm, UserPrefsForm
from .models import CustomUser, Notification
from django.contrib import messages


def userprefs(request):
    if request.method == 'POST':
        form = UserPrefsForm(request.POST)
        if form.is_valid():
            request.user.email = form.cleaned_data['email']
            request.user.interface = form.cleaned_data['interface']
            request.user.first_name = form.cleaned_data['first_name']
            request.user.last_name = form.cleaned_data['last_name']
            request.user.timezone = form.cleaned_data['timezone']
            try:
                request.user.save()
                messages.success(request, "User preferences saved.")
            except Exception as e:
                messages.error(request, "Error: %s" % e)
            return HttpResponseRedirect('/')
    user = get_object_or_404(CustomUser, id=request.user.id)
    session = request.session
    user_sessions = user.get_active_sessions()
    form = UserPrefsForm(instance=user)
    return render(request, 'users/prefs.html', {'form': form, 'session': session, 'user_sessions': user_sessions})


def detail(request, userid):
    try:
        user = CustomUser.objects.get(pk=userid)
    except CustomUser.DoesNotExist:
        raise Http404("User does not exist")
    return render(request, 'users/detail.html', {'thisuser': user})


def api_notifications(request):
    """JSON API for notifications - returns notifications for superusers."""
    if not request.user.is_authenticated or not request.user.is_superuser:
        return JsonResponse({'error': 'Unauthorized'}, status=403)
    
    notifications = Notification.objects.filter(user=request.user)[:20]
    data = [{
        'id': n.id,
        'message': n.message,
        'created_at': n.created_at.isoformat(),
        'is_read': n.is_read,
    } for n in notifications]
    
    return JsonResponse({'notifications': data})


def api_notifications_unread_count(request):
    """JSON API for unread notification count."""
    if not request.user.is_authenticated or not request.user.is_superuser:
        return JsonResponse({'count': 0})
    
    count = Notification.objects.filter(user=request.user, is_read=False).count()
    return JsonResponse({'count': count})


def api_notifications_mark_read(request, notification_id):
    """Mark a notification as read."""
    if not request.user.is_authenticated or not request.user.is_superuser:
        return JsonResponse({'error': 'Unauthorized'}, status=403)
    
    try:
        notification = Notification.objects.get(id=notification_id, user=request.user)
        notification.is_read = True
        notification.save()
        return JsonResponse({'success': True})
    except Notification.DoesNotExist:
        return JsonResponse({'error': 'Notification not found'}, status=404)


def api_notifications_mark_all_read(request):
    """Mark all notifications as read."""
    if not request.user.is_authenticated or not request.user.is_superuser:
        return JsonResponse({'error': 'Unauthorized'}, status=403)
    
    Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
    return JsonResponse({'success': True})

