"""harpoon2 URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/3.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from . import views
from django.contrib.auth import views as auth_views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', views.home, name='home'),
    path('queue/', views.queue, name='queue'),
    path('history/', views.history, name='history'),
    path('download/<str:item_hash>/cancel/', views.cancel_download, name='cancel_download'),
    path('transfer/<str:item_name>/cancel/', views.cancel_transfer, name='cancel_transfer'),
    path('item/<str:item_hash>/archive/', views.archive_item, name='archive_item'),
    path('item/<str:item_hash>/unarchive/', views.unarchive_item, name='unarchive_item'),
    path('item/<str:item_hash>/update-status/', views.update_item_status, name='update_item_status'),
    path('item/<str:item_hash>/update-downloader/', views.update_item_downloader, name='update_item_downloader'),
    path('item/<str:item_hash>/retry/', views.retry_failed_item, name='retry_failed_item'),
    path('archive/failed/', views.archive_all_failed, name='archive_all_failed'),
    path('archive/completed/', views.archive_all_completed, name='archive_all_completed'),
    path("search/", include('dplibs.search', namespace="watson")),
    path('users/', include('users.urls')),
    path('logout', auth_views.LogoutView.as_view(), {'next_page': '/'}, name='logout'),
    path('login', auth_views.LoginView.as_view(), name='login'),
    path('entities/', include('entities.urls')),
]
