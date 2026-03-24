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
            
            # Note: Items in PostProcessing without FileTransfer records will be handled
            # by the check_downloader_failures periodic task, which runs every 5 minutes.
            # We don't queue tasks here to avoid Celery broker connectivity issues during startup.
                    
        except Exception as e:
            logger.warning(f"[Startup] Error checking stuck items: {e}")
