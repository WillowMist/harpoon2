from django import forms
from django.contrib.auth.forms import UserCreationForm, UserChangeForm
from django.forms import ModelForm
from .models import CustomUser, NotificationSettings

class CustomUserCreationForm(UserCreationForm):

    class Meta(UserCreationForm):
        model = CustomUser
        fields = ('username', 'email', 'interface', 'timezone')

class CustomUserChangeForm(UserChangeForm):

    class Meta(UserChangeForm):
        model = CustomUser
        fields = ('first_name', 'last_name', 'username', 'email', 'interface', 'timezone')

class UserPrefsForm(ModelForm):

    class Meta(UserChangeForm):
        model = CustomUser
        fields = ('first_name', 'last_name', 'email', 'interface', 'timezone')


class NotificationSettingsForm(forms.ModelForm):
    
    class Meta:
        model = NotificationSettings
        fields = [
            'notify_downloader_failure',
            'notify_torrent_not_found',
            'notify_torrent_incomplete',
            'notify_sabnzbd_not_found',
            'notify_sabnzbd_incomplete',
            'notify_transfer_not_found',
            'notify_transfer_failure',
            'notify_sftp_failure',
            'notify_zip_failure',
            'notify_rar_failure',
            'notify_postprocess_failure',
            'notify_manual_intervention',
            'notify_item_completed',
        ]
        widgets = {
            'notify_downloader_failure': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'notify_torrent_not_found': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'notify_torrent_incomplete': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'notify_sabnzbd_not_found': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'notify_sabnzbd_incomplete': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'notify_transfer_not_found': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'notify_transfer_failure': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'notify_sftp_failure': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'notify_zip_failure': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'notify_rar_failure': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'notify_postprocess_failure': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'notify_manual_intervention': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'notify_item_completed': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
