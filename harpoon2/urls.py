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
    path('download/<int:item_id>/cancel/', views.cancel_download, name='cancel_download'),
    path('transfer/<str:item_name>/cancel/', views.cancel_transfer, name='cancel_transfer'),
    path("search/", include('dplibs.search', namespace="watson")),
    path('users/', include('users.urls')),
    path('logout', auth_views.LogoutView.as_view(), {'next_page': '/'}, name='logout'),
    path('login', auth_views.LoginView.as_view(), name='login'),
    path('entities/', include('entities.urls')),
]
