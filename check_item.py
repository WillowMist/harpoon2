import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'harpoon2.settings')
import django
django.setup()

from itemqueue.models import Item, FileTransfer, ItemHistory

items = Item.objects.all().order_by('-created')[:10]
for i in items:
    print(f"Item: {i.name}, Status: {i.status}")
    transfers = FileTransfer.objects.filter(item=i)
    print(f"  Transfers: {transfers.count()}")
    for t in transfers:
        print(f"    {t.status}: {t.local_path}")
    print()
