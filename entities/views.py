from django.shortcuts import render, redirect
from django.http import JsonResponse
from crisp_modals.views import ModalCreateView, ModalUpdateView, ModalDeleteView
from django.urls import reverse_lazy, reverse
from django.contrib import messages
from . import forms
from . import models
from .managers import Arr, Sonarr, Radarr, Lidarr, Readarr

# Create your views here.

class DLFolderCreateView(ModalCreateView):
    model = models.DownloadFolder
    template_name = 'entities/dlfoldercreate.html'
    form_class = forms.DLFolderModalForm
    success_message = 'Download folder added.'
    success_url = reverse_lazy('entities:settings')
    
    def form_valid(self, form):
        response = super().form_valid(form)
        if self.request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'success': True})
        return response


class DLFolderUpdateView(ModalUpdateView):
    model = models.DownloadFolder
    template_name = 'entities/dlfoldercreate.html'
    form_class = forms.DLFolderModalForm
    success_message = 'Download folder updated.'
    success_url = reverse_lazy('entities:settings')
    
    def form_valid(self, form):
        response = super().form_valid(form)
        if self.request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'success': True})
        return response


class DLFolderDeleteView(ModalDeleteView):
    model = models.DownloadFolder
    template_name = 'entities/dlfolderdelete.html'
    success_message = 'Download folder deleted.'
    success_url = reverse_lazy('entities:settings')
    
    def post(self, request, *args, **kwargs):
        # Get the object
        self.object = self.get_object()
        
        # Check if folder is in use by any managers
        managers_using_folder = models.Manager.objects.filter(folder=self.object)
        if managers_using_folder.exists():
            manager_names = ', '.join([m.name for m in managers_using_folder])
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                return JsonResponse({
                    'success': False, 
                    'error': f'Cannot delete folder. It is being used by manager(s): {manager_names}'
                }, status=400)
            # For non-AJAX, show the same form with error message
            return self.get(request, *args, **kwargs)
        
        # Folder is not in use, proceed with deletion
        self.object.delete()
        
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'success': True})
        
        return redirect(self.success_url)


class ManagerCreateView(ModalCreateView):
    model = models.Manager
    template_name = 'entities/managercreate.html'
    form_class = forms.ManagerModalForm
    success_message = 'Manager successfully created.'
    success_url = reverse_lazy('entities:managers')
    
    def form_valid(self, form):
        response = super().form_valid(form)
        if self.request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'success': True})
        return response


class ManagerUpdateView(ModalUpdateView):
    model = models.Manager
    template_name = 'entities/managercreate.html'
    form_class = forms.ManagerModalForm
    success_message = 'Manager successfully modified.'
    success_url = reverse_lazy('entities:managers')
    
    def form_valid(self, form):
        response = super().form_valid(form)
        if self.request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'success': True})
        return response


class ManagerDeleteView(ModalDeleteView):
    model = models.Manager
    template_name = 'entities/managerdelete.html'
    success_message = 'Manager deleted.'
    success_url = reverse_lazy('entities:managers')
    
    def delete(self, request, *args, **kwargs):
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            self.object = self.get_object()
            self.object.delete()
            return JsonResponse({'success': True})
        response = super().delete(request, *args, **kwargs)
        return response


class DownloaderCreateView(ModalCreateView):
    model = models.Downloader
    template_name = 'entities/downloadercreate.html'
    form_class = forms.DownloaderModalForm
    success_message = 'Downloader successfully created.'
    success_url = reverse_lazy('entities:downloaders')
    
    def form_valid(self, form):
        response = super().form_valid(form)
        if self.request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'success': True})
        return response


class DownloaderUpdateView(ModalUpdateView):
    model = models.Downloader
    template_name = 'entities/downloadercreate.html'
    form_class = forms.DownloaderModalForm
    success_message = 'Downloader successfully modified.'
    success_url = reverse_lazy('entities:downloaders')
    
    def form_valid(self, form):
        response = super().form_valid(form)
        if self.request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'success': True})
        return response


class DownloaderDeleteView(ModalDeleteView):
    model = models.Downloader
    template_name = 'entities/downloaderdelete.html'
    success_message = 'Downloader deleted.'
    success_url = reverse_lazy('entities:downloaders')
    
    def delete(self, request, *args, **kwargs):
        response = super().delete(request, *args, **kwargs)
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'success': True})
        return response


def settings(request):
    folders = models.DownloadFolder.objects.all()
    seedboxes = models.Seedbox.objects.all()
    return render(request, 'entities/settings.html', {'folders': folders, 'seedboxes': seedboxes})

def managers(request):
    managers = models.Manager.objects.all()
    return render(request, 'entities/managers.html', {'managers': managers})

def downloaders(request):
    downloaders = models.Downloader.objects.all()
    return render(request, 'entities/downloaders.html', {'downloaders': downloaders})

def managertest(request, pk):
    manager = models.Manager.objects.get(pk=pk)
    if manager.managertype in ['Sonarr', 'Radarr', 'Lidarr', 'Readarr', 'Whisparr']:
        if manager.managertype == 'Sonarr':
            client = Sonarr(manager)
        elif manager.managertype == 'Radarr':
            client = Radarr(manager)
        elif manager.managertype == 'Readarr':
            client = Readarr(manager)
        elif manager.managertype == 'Whisparr':
            from .managers import Whisparr
            client = Whisparr(manager)
        else:
            client = Lidarr(manager)
        success, message = client.test()
        return render(request, 'entities/clienttest.html', {'success': success, 'message': message, 'manager': manager})


def get_downloader_options(request, downloader_type):
    """Returns the option fields for a given downloader type"""
    from . import downloaders
    
    try:
        # Use mapping to handle downloader types with special characters (like 'AirDC++')
        downloader_attr = downloaders.DOWNLOADER_NAME_MAP.get(downloader_type, downloader_type)
        downloader_class = getattr(downloaders, downloader_attr)
        # Create a temporary instance to get optionfields
        temp_instance = downloader_class(None)
        options = temp_instance.optionfields
        return JsonResponse({'success': True, 'options': options})
    except (AttributeError, TypeError):
        return JsonResponse({'success': False, 'error': 'Invalid downloader type'}, status=400)


class SeedboxCreateView(ModalCreateView):
    model = models.Seedbox
    template_name = 'entities/seedboxcreate.html'
    form_class = forms.SeedboxModalForm
    success_message = 'Seedbox successfully created.'
    success_url = reverse_lazy('entities:settings')
    
    def form_valid(self, form):
        response = super().form_valid(form)
        if self.request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'success': True})
        return response


class SeedboxUpdateView(ModalUpdateView):
    model = models.Seedbox
    template_name = 'entities/seedboxcreate.html'
    form_class = forms.SeedboxModalForm
    success_message = 'Seedbox successfully modified.'
    success_url = reverse_lazy('entities:settings')
    
    def form_valid(self, form):
        response = super().form_valid(form)
        if self.request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'success': True})
        return response


class SeedboxDeleteView(ModalDeleteView):
    model = models.Seedbox
    template_name = 'entities/seedboxdelete.html'
    success_message = 'Seedbox deleted.'
    success_url = reverse_lazy('entities:settings')
    
    def delete(self, request, *args, **kwargs):
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            self.object = self.get_object()
            self.object.delete()
            return JsonResponse({'success': True})
        response = super().delete(request, *args, **kwargs)
        return response