"""
Django management command to assign missing downloaders to queued items.

This command queries the manager APIs to find downloader information for items
that don't have a downloader assigned, then assigns them.

Usage:
    python manage.py assign_missing_downloaders
    python manage.py assign_missing_downloaders --fix  # Actually make changes
"""

from django.core.management.base import BaseCommand
from itemqueue.models import Item, ItemHistory
from entities.models import Manager, Downloader
import requests
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Assign missing downloaders to queued items by querying manager APIs'

    def add_arguments(self, parser):
        parser.add_argument(
            '--fix',
            action='store_true',
            help='Actually make changes (otherwise just shows what would be done)',
        )

    def handle(self, *args, **options):
        fix = options.get('fix', False)
        
        # Find items without downloaders
        items_without_downloaders = Item.objects.filter(downloader__isnull=True, archived=False)

        if not items_without_downloaders.exists():
            self.stdout.write(self.style.SUCCESS("✓ No items without downloaders found"))
            return

        self.stdout.write(self.style.WARNING(f"\nFound {items_without_downloaders.count()} items without downloaders:\n"))

        assigned_count = 0
        
        for item in items_without_downloaders:
            self.stdout.write(f"Item: {item.name}")
            self.stdout.write(f"  Hash: {item.hash}")
            self.stdout.write(f"  Manager: {item.manager}")
            self.stdout.write(f"  Status: {item.status}")
            
            # Try to query the manager for downloader info
            if item.manager:
                manager = item.manager
                self.stdout.write(f"  Querying {manager.managertype}...")
                
                headers = {'X-Api-Key': manager.apikey, 'Accept': 'application/json'}
                
                # Determine API URL
                if manager.managertype == 'Lidarr':
                    api_url = f'{manager.url}/api/v1'
                elif manager.managertype == 'Readarr':
                    api_url = f'{manager.url}/api/v1'
                else:
                    api_url = f'{manager.url}/api/v3'
                
                # Query history for this item
                try:
                    url = f'{api_url}/history'
                    resp = requests.get(url, headers=headers, params={'pageSize': 100}, timeout=10)
                    if resp.status_code == 200:
                        records = resp.json().get('records', [])
                        matching_record = None
                        for record in records:
                            if record.get('downloadId') == item.hash:
                                matching_record = record
                                break
                        
                        if matching_record:
                            client_name = matching_record.get('data', {}).get('downloadClient', '')
                            self.stdout.write(f"  Found in history - Client: {client_name}")
                            
                            if client_name:
                                # Try to find downloader
                                downloader = Downloader.objects.filter(name__iexact=client_name).first()
                                if not downloader:
                                    downloader = Downloader.objects.filter(downloadertype__iexact=client_name).first()
                                
                                if downloader:
                                    if fix:
                                        item.downloader = downloader
                                        item.save()
                                        ItemHistory.objects.create(
                                            item=item,
                                            details=f'Downloader assigned via management command: {downloader.name}'
                                        )
                                        self.stdout.write(self.style.SUCCESS(f"  ✓ Assigned downloader: {downloader.name}"))
                                        assigned_count += 1
                                    else:
                                        self.stdout.write(self.style.WARNING(f"  → Would assign downloader: {downloader.name}"))
                                else:
                                    self.stdout.write(self.style.ERROR(f"  ✗ No matching downloader found for '{client_name}'"))
                                    # List available downloaders
                                    available = Downloader.objects.all()
                                    if available.exists():
                                        self.stdout.write(f"    Available downloaders:")
                                        for dl in available:
                                            self.stdout.write(f"      - {dl.name} ({dl.downloadertype})")
                            else:
                                self.stdout.write(self.style.ERROR(f"  ✗ No download client info in manager history"))
                        else:
                            self.stdout.write(self.style.ERROR(f"  ✗ Item not found in manager history"))
                    else:
                        self.stdout.write(self.style.ERROR(f"  ✗ Failed to query manager (HTTP {resp.status_code})"))
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"  ✗ Error querying manager: {e}"))
            else:
                self.stdout.write(self.style.ERROR("  ✗ No manager assigned"))
            
            self.stdout.write("")

        # Summary
        if fix:
            self.stdout.write(self.style.SUCCESS(f"\n✓ Assigned {assigned_count} downloaders"))
        else:
            self.stdout.write(self.style.WARNING(f"\nWould assign {assigned_count} downloaders. Run with --fix to make changes."))
