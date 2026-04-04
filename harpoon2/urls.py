from django.contrib import admin
from django.urls import path, include, re_path
from django.contrib.auth.views import LogoutView
from . import views
from dplibs import search as search_module

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/', include('django.contrib.auth.urls')),
    path('logout/', LogoutView.as_view(), name='logout'),
    path('login/', views.login_view, name='login'),
    path('', views.home, name='home'),
    path('queue/', views.queue, name='queue'),
    path('history/', views.history, name='history'),
    path('cancel/download/<str:item_hash>/', views.cancel_download, name='cancel_download'),
    path('cancel/transfer/<str:item_name>/', views.cancel_transfer, name='cancel_transfer'),
    path('cancel/postprocessing/<str:item_hash>/', views.cancel_postprocessing, name='cancel_postprocessing'),
    path('retry/postprocessing/<str:item_hash>/', views.retry_postprocessing_transfer, name='retry_postprocessing'),
    path('archive/clear/', views.clear_archive, name='clear_archive'),
    path('archive/<str:item_hash>/', views.archive_item, name='archive_item'),
    path('unarchive/<str:item_hash>/', views.unarchive_item, name='unarchive_item'),
    path('archive/all/failed/', views.archive_all_failed, name='archive_all_failed'),
    path('archive/all/completed/', views.archive_all_completed, name='archive_all_completed'),
    path('update/item/status/<str:item_hash>/', views.update_item_status, name='update_item_status'),
    path('update/item/downloader/<str:item_hash>/', views.update_item_downloader, name='update_item_downloader'),
    path('retry/failed/<str:item_hash>/', views.retry_failed_item, name='retry_failed_item'),
    path('api/dashboard/', views.api_dashboard, name='api-dashboard'),
    path('api/queue/', views.api_queue, name='api-queue'),
    path('api/history/', views.api_history, name='api-history'),
    path('api/version/', views.api_version_check, name='api-version'),
    path('api/item/<str:item_hash>/history/', views.api_item_history, name='api-item-history'),
    path('api/item/<str:item_hash>/transfers/', views.api_item_transfers, name='api-item-transfers'),
    path('search/', search_module.search, name='search'),
    path('entities/', include('entities.urls', namespace='entities')),
    path('users/', include('users.urls', namespace='users')),
]
