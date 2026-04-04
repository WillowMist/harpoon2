#!/usr/bin/env python
import os, django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "harpoon2.settings")
django.setup()

from django.test import Client
from django.contrib.auth import get_user_model

User = get_user_model()
user = User.objects.first()

client = Client()
client.force_login(user)

# Simulate GET request to clear_archive
response = client.get("/archive/clear/")
print(f"Status: {response.status_code}")
print(f"Redirect: {response.url}")

# Check archived items after
from itemqueue.models import Item
print(f"Archived after: {Item.objects.filter(archived=True).count()}")