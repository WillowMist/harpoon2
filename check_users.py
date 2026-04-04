#!/usr/bin/env python
import os
import django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "harpoon2.settings")
django.setup()

from django.contrib.auth import get_user_model
User = get_user_model()
print(f"Users: {User.objects.count()}")
for u in User.objects.all():
    print(f"  - {u.username} (staff={u.is_staff}, superuser={u.is_superuser})")