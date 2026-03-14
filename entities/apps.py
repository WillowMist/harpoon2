from django.apps import AppConfig
import logging

logger = logging.getLogger(__name__)


class EntitiesConfig(AppConfig):
    name = 'entities'
    
    def ready(self):
        """Check for items stuck in PostProcessing on startup."""
        from itemqueue.models import Item, FileTransfer
        from django.db import connection
        
        # Check if database is ready
        try:
            connection.ensure_connection()
        except Exception:
            # Database not ready yet, skip
            return
        
        try:
            # Find items in PostProcessing without a downloader
            stuck_items = Item.objects.filter(
                status='PostProcessing',
                downloader__isnull=True
            )
            
            count = stuck_items.count()
            if count > 0:
                logger.info(f"[Startup] Found {count} item(s) stuck in PostProcessing without downloader")
                
                for item in stuck_items:
                    # Try to assign a downloader based on the manager's settings
                    if item.manager:
                        if item.manager.torrent_downloader:
                            item.downloader = item.manager.torrent_downloader
                            item.save()
                            logger.info(f"[Startup] Assigned downloader {item.downloader.name} to item {item.name}")
                        elif item.manager.nzb_downloader:
                            item.downloader = item.manager.nzb_downloader
                            item.save()
                            logger.info(f"[Startup] Assigned downloader {item.downloader.name} to item {item.name}")
            
            # Also check for items in PostProcessing without any FileTransfer records
            # and queue their transfers
            pp_items = Item.objects.filter(status='PostProcessing')
            for item in pp_items:
                has_transfers = FileTransfer.objects.filter(item=item).exists()
                if not has_transfers:
                    logger.info(f"[Startup] Item {item.name} in PostProcessing but has no transfers, queueing transfer")
                    try:
                        # Import and queue the transfer task
                        from itemqueue.tasks import transfer_files_async
                        transfer_files_async.delay(item.hash)
                        logger.info(f"[Startup] Queued transfer for {item.name}")
                    except Exception as te:
                        logger.warning(f"[Startup] Failed to queue transfer for {item.name}: {te}")
                    
        except Exception as e:
            logger.warning(f"[Startup] Error checking stuck items: {e}")
