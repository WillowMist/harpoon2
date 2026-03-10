from crisp_modals.forms import ModalModelForm
from django import forms
from django.forms import ValidationError
from .models import DownloadFolder, Manager, Downloader, Seedbox
import os
import json

class DLFolderModalForm(ModalModelForm):
    class Meta:
        model = DownloadFolder
        exclude = ['pk']

    def clean_folder(self):
        data = self.cleaned_data['folder']
        if os.path.exists(data) and os.path.isdir(data):
            return data
        elif os.path.exists(data) and os.path.isfile(data):
            self.add_error('folder', ValidationError('Path exists, but is a file.  Please only enter a folder.'))
        else:
            try:
                os.makedirs(data)
            except PermissionError:

                self.add_error('folder', ValidationError('Folder does not exist and could not be created.  Please check permissions.'))
            except NotADirectoryError:
                self.add_error('folder', ValidationError('Invalid directory.  Check the path and try again.'))
        return data


class ManagerModalForm(ModalModelForm):
    class Meta:
        model = Manager
        exclude = ['pk']

class DownloaderModalForm(ModalModelForm):
    options = forms.CharField(widget=forms.HiddenInput(), required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Override the options field to be a CharField instead of JSONField
        self.fields['options'].widget = forms.HiddenInput()
        self.fields['options'].required = False
        
        # Initialize options field with JSON string (MUST be valid JSON)
        if self.instance and self.instance.pk and self.instance.options:
            # Always convert to valid JSON string
            if isinstance(self.instance.options, dict):
                self.fields['options'].initial = json.dumps(self.instance.options)
            else:
                try:
                    # If it's a string, try to parse and re-serialize to ensure valid JSON
                    self.fields['options'].initial = json.dumps(json.loads(self.instance.options))
                except (json.JSONDecodeError, TypeError):
                    self.fields['options'].initial = '{}'
        else:
            self.fields['options'].initial = '{}'

    def clean_options(self):
        options_str = self.cleaned_data.get('options', '{}')
        # If it's already a dict, return it
        if isinstance(options_str, dict):
            return options_str
        # Otherwise parse the JSON string
        try:
            return json.loads(options_str)
        except (json.JSONDecodeError, TypeError):
            return {}

    def save(self, commit=True):
        instance = super().save(commit=False)
        # The clean_options method handles conversion
        instance.options = self.cleaned_data.get('options', {})
        if commit:
            instance.save()
        return instance

    class Meta:
        model = Downloader
        fields = ['name', 'downloadertype', 'options']


class SeedboxModalForm(ModalModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Make password field render as password input
        self.fields['password'].widget = forms.PasswordInput()
        # Make ssh_key field a textarea
        self.fields['ssh_key'].widget = forms.Textarea()
        # Don't pre-populate sensitive fields (browsers block this for security)
        # If editing, clear the initial values so fields appear empty
        if self.instance and self.instance.pk:
            self.fields['password'].initial = None
            self.fields['ssh_key'].initial = None
    
    def save(self, commit=True):
        instance = super().save(commit=False)
        # If password field is empty on edit, keep the existing password
        if self.instance.pk and not self.cleaned_data.get('password'):
            # Restore the original password if field was left empty
            instance.password = self.instance.password
        # If ssh_key field is empty on edit, keep the existing ssh_key
        if self.instance.pk and not self.cleaned_data.get('ssh_key'):
            # Restore the original ssh_key if field was left empty
            instance.ssh_key = self.instance.ssh_key
        if commit:
            instance.save()
        return instance
    
    class Meta:
        model = Seedbox
        fields = ['name', 'host', 'port', 'username', 'auth_type', 'password', 'ssh_key', 'base_download_folder']

