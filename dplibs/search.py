from __future__ import unicode_literals

from django.urls import re_path
from django.views.generic import ListView
from django.shortcuts import render
from django.db.models import Q
from django.contrib.auth.decorators import login_required
from itemqueue.models import Item


@login_required
def search(request, **kwargs):
    """Renders a page of search results using Django ORM."""
    query = request.GET.get('q', '')
    items = []
    
    if query:
        items = Item.objects.filter(
            Q(name__icontains=query) |
            Q(hash__icontains=query) |
            Q(category__icontains=query)
        ).order_by('-modified')[:100]
    
    return render(request, 'search_results.html', {
        'search_results': items,
        'query': query,
    })


app_name = 'watson'
urlpatterns = [
    re_path("^$", search, name="search"),
]
