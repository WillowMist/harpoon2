from django.urls import path
from . import views

app_name = 'entities'
urlpatterns = [
    path('dlfoldercreate/', views.DLFolderCreateView.as_view(), name='dlfolder-create'),
    path('folders/<int:pk>/update/', views.DLFolderUpdateView.as_view(), name='dlfolder-update'),
    path('dlfolder/<int:pk>/delete/', views.DLFolderDeleteView.as_view(), name='dlfolder-delete'),
    path('managercreate/', views.ManagerCreateView.as_view(), name='manager-create'),
    path('downloadercreate/', views.DownloaderCreateView.as_view(), name='downloader-create'),
    path('managers/<int:pk>/update/', views.ManagerUpdateView.as_view(), name='manager-update'),
    path('managers/<int:pk>/delete/', views.ManagerDeleteView.as_view(), name='manager-delete'),
    path('downloaders/<int:pk>/update/', views.DownloaderUpdateView.as_view(), name='downloader-update'),
    path('downloaders/<int:pk>/delete/', views.DownloaderDeleteView.as_view(), name='downloader-delete'),
    path('seedboxcreate/', views.SeedboxCreateView.as_view(), name='seedbox-create'),
    path('seedboxes/<int:pk>/update/', views.SeedboxUpdateView.as_view(), name='seedbox-update'),
    path('seedboxes/<int:pk>/delete/', views.SeedboxDeleteView.as_view(), name='seedbox-delete'),
    path('settings/', views.settings, name='settings'),
    path('managers/', views.managers, name='managers'),
    path('downloaders/', views.downloaders, name='downloaders'),
    path('managers/<int:pk>/test/', views.managertest, name='manager-test'),
    path('api/downloader-options/<str:downloader_type>/', views.get_downloader_options, name='get-downloader-options'),
]