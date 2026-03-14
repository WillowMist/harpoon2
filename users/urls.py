from django.urls import path
from . import views

app_name = 'users'
urlpatterns = [
    # path('', views.UserJson.as_view(), name='index'),
    path('<int:userid>/', views.detail, name='detail'),
    path('prefs/', views.userprefs, name='userprefs'),
    # Notification API endpoints
    path('api/notifications/', views.api_notifications, name='api_notifications'),
    path('api/notifications/unread-count/', views.api_notifications_unread_count, name='api_notifications_unread_count'),
    path('api/notifications/<int:notification_id>/mark-read/', views.api_notifications_mark_read, name='api_notifications_mark_read'),
    path('api/notifications/mark-all-read/', views.api_notifications_mark_all_read, name='api_notifications_mark_all_read'),
]
