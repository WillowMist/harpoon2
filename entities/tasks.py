from harpoon2.celery import app
from celery import shared_task
from entities.models import Manager
from itemqueue.models import Item, ItemHistory
import logging
import requests

logger = logging.getLogger(__name__)


def poll_mylar3(manager):
    """Poll Mylar3 for newly grabbed comics."""
    from entities.managers import Mylar3
    
    try:
        client = Mylar3(manager)
        history = client.get_history()
        
        for record in history:
            status = record.get('Status', '')
            
            # Check for newly grabbed comics
            # Mylar3 status values: "Downloaded", "Snatched", "Skipped", "Wanted", etc.
            if status in ('Snatched', 'Downloaded'):
                download_id = record.get('nzb_id', record.get('nzbid', ''))
                title = record.get('Title', record.get('ComicName', 'Unknown'))
                size = record.get('Size', 0)
                
                if not download_id:
                    continue
                
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
                    logger.info(f"[Mylar3] New grabbed item: {title} ({download_id})")
    
    except Exception as e:
        logger.error(f"[Mylar3] Error polling {manager.name}: {e}")


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
    
    # Blackhole managers don't have an API - they poll a folder instead
    if manager.managertype == 'Blackhole':
        return
    
    # Mylar3 uses its own API structure
    if manager.managertype == 'Mylar3':
        poll_mylar3(manager)
        return
    
    if not manager.url:
        logger.warning(f"Manager {manager.name} has no URL configured, skipping poll")
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
                
                # For Whisparr with Blackhole downloaders (Torrent/Usenet Blackhole),
                # downloadId will be empty. Use sourceTitle as the hash instead.
                if not download_id:
                    if manager.managertype == 'Whisparr':
                        # Use sourceTitle as hash for blackhole downloads
                        download_id = record.get('sourceTitle', '')
                        if not download_id:
                            continue
                        logger.debug(f"Using sourceTitle as hash for Whisparr blackhole: {download_id}")
                    else:
                        continue
                
                # For Whisparr with SABnzbd, try to get the downloader from the download client info
                downloader = None
                if manager.managertype == 'Whisparr' and download_id.startswith('SABnzbd_'):
                    # Get the download client type
                    data_field = record.get('data', {})
                    if isinstance(data_field, dict):
                        client_type = data_field.get('downloadClient', '')
                        if client_type == 'SABnzbd':
                            try:
                                from entities.models import Downloader
                                # Find SABnzbd downloader
                                downloader = Downloader.objects.get(downloadertype='SABNzbd')
                                logger.debug(f"Assigned SABnzbd downloader '{downloader.name}' to item {title}")
                            except Downloader.DoesNotExist:
                                logger.debug(f"SABnzbd downloader not found for item {title}")
                
                # Check if already in queue
                item, created = Item.objects.get_or_create(
                    hash=download_id,
                    defaults={
                        'name': title,
                        'size': size,
                        'status': 'Grabbed',
                        'manager': manager,
                        'downloader': downloader,  # Assign downloader if found
                    }
                )
                
                if created:
                    ItemHistory.objects.create(
                        item=item,
                        details=f'Grabbed by {manager.name}'
                    )
                    logger.info(f"New grabbed item: {title} ({download_id})")
            
            elif event_type in ('downloadFolderImported', 'downloadImported'):
                # Handle both Sonarr/Radarr (downloadFolderImported) and Lidarr (downloadImported)
                # NOTE: This event means the manager has imported the item into its library,
                # NOT that it's been transferred to seedbox yet. The actual Completed status
                # is set by the transfer task after files are successfully transferred.
                # We log this event for reference but don't change item status here.
                download_id = record.get('downloadId', '')
                title = record.get('title', record.get('sourceTitle', 'Unknown'))
                
                if not download_id:
                    continue
                
                # Just log that we received the import notification
                try:
                    item = Item.objects.get(hash=download_id)
                    ItemHistory.objects.create(
                        item=item,
                        details=f'Import event received from {manager.name} - awaiting transfer completion'
                    )
                    logger.debug(f"Received import event for item: {title} ({download_id})")
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
                        from users.models import Notification
                        Notification.create_for_admin(
                            f"Download failed for '{item.name}': {error_message[:100]}",
                            notification_type='downloader_failure',
                            item_hash=item.hash
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
def poll_blackhole_managers():
    """Poll all Blackhole managers for new .nzb and .torrent files."""
    from entities.models import Manager
    import os
    import logging
    
    logger = logging.getLogger(__name__)
    
    for manager in Manager.objects.filter(managertype='Blackhole', enabled=True):
        try:
            poll_blackhole_manager(manager.id)
        except Exception as e:
            logger.error(f"Error polling Blackhole manager {manager.name}: {e}")


@shared_task
def poll_blackhole_manager(manager_id):
    """Poll a specific Blackhole manager for new files."""
    from entities.models import Manager
    from itemqueue.models import Item, ItemHistory
    import os
    import logging
    import hashlib
    
    logger = logging.getLogger(__name__)
    
    try:
        manager = Manager.objects.get(id=manager_id, managertype='Blackhole')
    except Manager.DoesNotExist:
        return
    
    if not manager.enabled:
        logger.debug(f"Blackhole manager {manager.name} is disabled, skipping")
        return
    
    # Get the Blackhole client instance
    from entities.managers import Blackhole
    client = Blackhole(manager)
    
    # Test the connection first
    test_result, test_message = client.test()
    if not test_result:
        logger.warning(f"Blackhole manager {manager.name} test failed: {test_message}")
        return
    
    # Get files to process
    files = client.get_files_to_process()
    
    processed_count = 0
    
    # Process .torrent files
    for filepath in files.get('torrent', []):
        filename = os.path.basename(filepath)
        
        # Check if we should skip this file
        if client.should_skip_file(filename):
            logger.debug(f"Skipping duplicate file: {filename}")
            continue
        
        # Check if torrent downloader is configured
        if not client.torrent_downloader:
            logger.info(f"No torrent downloader configured, skipping: {filename}")
            # TODO: Send notification about skipped file
            continue
        
        # Send to downloader
        success, download_id, message = client.send_to_downloader(filepath, 'torrent')
        category = client.get_category_for_file(filepath)
        
        if success and download_id:
            # Create item in our database
            item, created = Item.objects.get_or_create(
                hash=download_id,
                defaults={
                    'name': filename,
                    'size': os.path.getsize(filepath) if os.path.exists(filepath) else 0,
                    'status': 'Grabbed',
                    'manager': manager,
                    'downloader': client.torrent_downloader,
                    'category': category,
                }
            )
            
            if created:
                ItemHistory.objects.create(
                    item=item,
                    details=f'Blackhole grabbed torrent: {filename}'
                )
                logger.info(f"Grabbed torrent from Blackhole: {filename}")
            
            # Delete source file if configured
            if client.delete_source and os.path.exists(filepath):
                try:
                    os.remove(filepath)
                    logger.debug(f"Deleted source file: {filepath}")
                except Exception as e:
                    logger.warning(f"Failed to delete source file {filepath}: {e}")
            
            processed_count += 1
        else:
            logger.warning(f"Failed to send torrent to downloader: {filename} - {message}")
    
    # Process .nzb files
    for filepath in files.get('nzb', []):
        filename = os.path.basename(filepath)
        
        # Check if we should skip this file
        if client.should_skip_file(filename):
            logger.debug(f"Skipping duplicate file: {filename}")
            continue
        
        # Check if nzb downloader is configured
        if not client.nzb_downloader:
            logger.info(f"No NZB downloader configured, skipping: {filename}")
            # TODO: Send notification about skipped file
            continue
        
        # Send to downloader
        success, download_id, message = client.send_to_downloader(filepath, 'nzb')
        category = client.get_category_for_file(filepath)
        
        if success and download_id:
            # Create item in our database
            item, created = Item.objects.get_or_create(
                hash=download_id,
                defaults={
                    'name': filename,
                    'size': os.path.getsize(filepath) if os.path.exists(filepath) else 0,
                    'status': 'Grabbed',
                    'manager': manager,
                    'downloader': client.nzb_downloader,
                    'category': category,
                }
            )
            
            if created:
                ItemHistory.objects.create(
                    item=item,
                    details=f'Blackhole grabbed NZB: {filename}'
                )
                logger.info(f"Grabbed NZB from Blackhole: {filename}")
            
            # Delete source file if configured
            if client.delete_source and os.path.exists(filepath):
                try:
                    os.remove(filepath)
                    logger.debug(f"Deleted source file: {filepath}")
                except Exception as e:
                    logger.warning(f"Failed to delete source file {filepath}: {e}")
            
            processed_count += 1
        else:
            logger.warning(f"Failed to send NZB to downloader: {filename} - {message}")
    
    if processed_count > 0:
        logger.info(f"Blackhole manager {manager.name} processed {processed_count} files")


@shared_task
def assign_items_to_downloaders():
    """Assign items to downloaders based on the download client they use."""
    from itemqueue.models import Item
    from entities.models import Downloader
    
    # Get items that have a manager but no downloader assigned
    # Include both Grabbed items and Failed items (to retry assignment for items that failed due to no downloader)
    items = Item.objects.filter(
        manager__isnull=False,
        downloader__isnull=True,
        status__in=['Grabbed', 'Failed']
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
                            
                            # If no match by name, try fuzzy matching (e.g., "Black SabNZBd" -> "Black SAB")
                            if not downloader:
                                # Try partial matches
                                if 'sab' in download_client.lower():
                                    downloader = Downloader.objects.filter(
                                        name__icontains='SAB'
                                    ).first()
                                elif 'rtorrent' in download_client.lower() or 'torrent' in download_client.lower():
                                    downloader = Downloader.objects.filter(
                                        name__icontains='RTorrent'
                                    ).first()
                            
                            # If still no match, try matching by type based on protocol
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


@shared_task
def cache_downloader_status():
    """Poll all downloaders and cache their status for fast page loads."""
    from entities.models import Downloader, CachedDownloaderStatus
    
    for downloader in Downloader.objects.all():
        try:
            client = downloader.client
            active_downloads = []
            
            if downloader.downloadertype == 'RTorrent':
                active_torrents = client.get_active_downloads(limit=50)
                for torrent in active_torrents:
                    active_downloads.append({
                        'name': torrent.get('name', ''),
                        'hash': torrent.get('hash', ''),
                        'size': torrent.get('size', 0),
                        'completed': torrent.get('completed', 0),
                        'percent': torrent.get('percent', 0),
                    })
            elif downloader.downloadertype == 'SABNzbd':
                client._ensure_client()
                result = client._api_call('queue')
                if 'queue' in result:
                    slots = result['queue'].get('slots', [])
                    for slot in slots:
                        if slot.get('status') != 'Completed':
                            active_downloads.append({
                                'name': slot.get('filename', ''),
                                'hash': slot.get('nzo_id', ''),
                                'size': float(slot.get('mb', 0)) * 1024 * 1024,
                                'completed': 0,
                                'percent': float(slot.get('percentage', 0)),
                            })
            
            elif downloader.downloadertype == 'QBittorrent':
                client._ensure_client()
                # Get all active torrents (not completed)
                all_torrents = client.client.torrents_info()
                for torrent in all_torrents:
                    # Skip completed torrents
                    if torrent.completed == torrent.size and torrent.size > 0:
                        continue
                    active_downloads.append({
                        'name': torrent.name,
                        'hash': torrent.hash.upper(),
                        'size': torrent.size,
                        'completed': torrent.completed,
                        'percent': int(torrent.progress * 100),
                    })
            
            # Update or create cache
            cache, created = CachedDownloaderStatus.objects.get_or_create(
                downloader=downloader,
                defaults={'active_downloads': active_downloads}
            )
            if not created:
                cache.active_downloads = active_downloads
                cache.save()
                
        except Exception as e:
            logger.error(f"Error caching downloader {downloader.name}: {e}")
