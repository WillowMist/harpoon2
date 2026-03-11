from harpoon2.celery import app
from celery import shared_task
from entities.models import Manager
from itemqueue.models import Item, ItemHistory
import logging
import requests

logger = logging.getLogger(__name__)


@shared_task
def poll_managers():
    """Poll all managers for newly grabbed items."""
    for manager in Manager.objects.all():
        poll_manager(manager.id)


@shared_task
def poll_manager(manager_id):
    """Poll a specific manager for newly grabbed items."""
    try:
        manager = Manager.objects.get(id=manager_id)
    except Manager.DoesNotExist:
        return
    
    headers = {'X-Api-Key': manager.apikey, 'Accept': 'application/json'}
    
    # Determine API version
    if manager.managertype == 'Lidarr':
        api_url = f'{manager.url}/api/v1'
    elif manager.managertype == 'Readarr':
        api_url = f'{manager.url}/api/v1'
    else:
        api_url = f'{manager.url}/api/v3'
    
    # Get recent history for grabbed events
    url = f'{api_url}/history'
    try:
        resp = requests.get(url, headers=headers, params={'pageSize': 50})
        if resp.status_code != 200:
            logger.warning(f"Failed to fetch history for {manager.name}: {resp.status_code}")
            return
        
        data = resp.json()
        records = data.get('records', []) if 'records' in data else data.get('history', [])
        
        for record in records:
            event_type = record.get('eventType', record.get('event', ''))
            
            if event_type == 'grabbed':
                download_id = record.get('downloadId', '')
                title = record.get('title', record.get('sourceTitle', 'Unknown'))
                size = record.get('size', 0)
                
                if not download_id:
                    continue
                
                # Check if already in queue
                item, created = Item.objects.get_or_create(
                    hash=download_id,
                    defaults={
                        'name': title,
                        'size': size,
                        'status': 'Grabbed',
                        'manager': manager,
                    }
                )
                
                if created:
                    ItemHistory.objects.create(
                        item=item,
                        details=f'Grabbed by {manager.name}'
                    )
                    logger.info(f"New grabbed item: {title} ({download_id})")
            
            elif event_type == 'downloadFolderImported':
                download_id = record.get('downloadId', '')
                title = record.get('title', record.get('sourceTitle', 'Unknown'))
                
                if not download_id:
                    continue
                
                # Item has been downloaded and imported - mark as completed
                try:
                    item = Item.objects.get(hash=download_id)
                    if item.status != 'Completed':
                        item.status = 'Completed'
                        item.save()
                        ItemHistory.objects.create(
                            item=item,
                            details=f'Downloaded and imported by {manager.name}'
                        )
                        logger.info(f"Item completed: {title} ({download_id})")
                except Item.DoesNotExist:
                    logger.debug(f"Received import event for unknown item: {download_id}")
            
            elif event_type == 'downloadFailed':
                download_id = record.get('downloadId', '')
                title = record.get('title', record.get('sourceTitle', 'Unknown'))
                data = record.get('data', {})
                error_message = data.get('message', 'Download failed (no details available)')
                
                if not download_id:
                    continue
                
                # Item download failed - mark as failed with error details
                try:
                    item = Item.objects.get(hash=download_id)
                    if item.status != 'Failed':
                        item.status = 'Failed'
                        item.save()
                        ItemHistory.objects.create(
                            item=item,
                            details=f'Download failed: {error_message}'
                        )
                        logger.warning(f"Item failed: {title} ({download_id}) - {error_message}")
                except Item.DoesNotExist:
                    logger.debug(f"Received failed event for unknown item: {download_id}")
                    
    except Exception as e:
        logger.error(f"Error polling manager {manager.name}: {e}")


@shared_task
def queue_cronjob():
    """Legacy task - redirects to poll_managers."""
    poll_managers()


@shared_task
def assign_items_to_downloaders():
    """Assign items to downloaders based on the download client they use."""
    from itemqueue.models import Item
    from entities.models import Downloader
    
    # Get items that have a manager but no downloader assigned
    items = Item.objects.filter(
        manager__isnull=False,
        downloader__isnull=True,
        status='Grabbed'
    )
    
    for item in items:
        # Query the manager's queue to find which download client is being used
        try:
            manager = item.manager
            headers = {'X-Api-Key': manager.apikey, 'Accept': 'application/json'}
            
            if manager.managertype == 'Lidarr':
                api_url = f'{manager.url}/api/v1'
            elif manager.managertype == 'Readarr':
                api_url = f'{manager.url}/api/v1'
            else:
                api_url = f'{manager.url}/api/v3'
            
            # Get queue to find the download client for this item
            import requests
            url = f'{api_url}/queue'
            resp = requests.get(url, headers=headers, params={'pageSize': 100})
            
            if resp.status_code == 200:
                data = resp.json()
                records = data.get('records', [])
                
                for record in records:
                    if record.get('downloadId') == item.hash:
                        download_client = record.get('downloadClient', '')
                        protocol = record.get('protocol', '')  # 'torrent' or 'nzb'
                        
                        if download_client:
                            # Find matching downloader by name OR by type matching protocol
                            downloader = Downloader.objects.filter(
                                name__icontains=download_client
                            ).first()
                            
                            # If no match by name, try matching by type
                            if not downloader:
                                if protocol in ('torrent', 'bt'):
                                    downloader = Downloader.objects.filter(
                                        downloadertype='RTorrent'
                                    ).first()
                                elif protocol in ('nzb', 'usenet'):
                                    downloader = Downloader.objects.filter(
                                        downloadertype='SABNzbd'
                                    ).first()
                            
                            if downloader:
                                item.downloader = downloader
                                item.save()
                                ItemHistory.objects.create(
                                    item=item,
                                    details=f'Assigned to {downloader.name}'
                                )
                                logger.info(f"Assigned {item.name} to {downloader.name}")
                        break
                        
        except Exception as e:
            logger.error(f"Error assigning item {item.hash}: {e}")
