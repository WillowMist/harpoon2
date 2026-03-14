import requests
from itemqueue.models import Item, ItemHistory

class Arr(object):
    def __init__(self, manager):
        self.manager = manager
        self.url = manager.url
        self.apikey = manager.apikey
        self.label = manager.label
        self.headers = {'X-Api-Key': self.apikey, 'Accept': 'application/json'}
        # Default API path - can be overridden in subclasses
        self.apiurl = self.url + '/api/v3'

    def test(self):
        testurl = self.apiurl + '/system/status'
        try:
            r = requests.get(testurl, params=None, headers=self.headers)
            dt = r.json()
            return True, dt
        except Exception as e:
            return False, e

    def check_queue(self):
        url = self.apiurl + '/queue'
        try:
            r = requests.get(url, params=None, headers=self.headers)
            response_data = r.json()
            # Handle both list and dict responses (dict has 'records' key)
            if isinstance(response_data, dict) and 'records' in response_data:
                queue_data = response_data['records']
            else:
                queue_data = response_data
            dt = self.parse_queue(queue_data)
            return True, dt
        except Exception as e:
            return False, e

    def parse_queue(self, queue):
        records = []
        for record in queue:
            recordinfo = {}
            recordinfo['size'] = record['size']
            recordinfo['name'] = record['title']
            recordinfo['status'] = record['status']
            recordinfo['tdstate'] = record['trackedDownloadState'] if 'trackedDownloadState' in record.keys() else ''
            recordinfo['tdstatus'] = record['trackedDownloadStatus']
            recordinfo['statusmessages'] = record['statusMessages']
            recordinfo['downloadid'] = record['downloadId']
            recordinfo['clientid'] = record['id']
            recordinfo['manager'] = self.manager
            records.append(recordinfo)
        return records

    def check_itemqueue(self, record):
        queueitem, created = Item.objects.get_or_create(hash=record['downloadid'])
        if created:
            changed = {'hash': queueitem.hash}
        else:
            changed = {}
        # Preserve archived status - don't overwrite it during updates
        original_archived = queueitem.archived
        original_archived_at = queueitem.archived_at
        
        for attr in ['size', 'name', 'status', 'clientid', 'manager']:
            if getattr(queueitem, attr) != record[attr]:
                changed[attr] = record[attr]
                setattr(queueitem, attr, record[attr])
        if changed:
            queueitem.save()
            # Re-apply archived status if it was changed
            if queueitem.archived != original_archived or queueitem.archived_at != original_archived_at:
                queueitem.archived = original_archived
                queueitem.archived_at = original_archived_at
                queueitem.save(update_fields=['archived', 'archived_at'])
            for key in changed.keys():
                history = ItemHistory.objects.create(item=queueitem, details=f'{key} set to "{changed[key]}"')
        return
    
    def reject_download(self, item, reason):
        """Notify the manager that a download failed and should be re-attempted.
        
        This sends a message to the *arr manager to mark the download as failed,
        allowing it to search for an alternate release.
        
        Args:
            item: Item object with hash and clientid
            reason: String explanation of why it failed (e.g., "RAR extraction failed: corrupted archive")
            
        Returns:
            (success: bool, message: str)
        """
        try:
            # Build rejection message to send to manager
            # The manager queue item has: id (clientid), title, downloadId (hash)
            url = self.apiurl + '/queue/bulk'
            
            payload = {
                'ids': [item.clientid],
                'blacklist': True  # Mark as blacklisted so *arr won't grab it again
            }
            
            response = requests.delete(url, json=payload, headers=self.headers, timeout=10)
            
            if response.status_code in [200, 204]:
                message = f"Notified manager to reject download: {reason}"
                ItemHistory.objects.create(item=item, details=message)
                return True, message
            else:
                message = f"Failed to notify manager (HTTP {response.status_code}): {reason}"
                ItemHistory.objects.create(item=item, details=message)
                return False, message
                
        except Exception as e:
            message = f"Error notifying manager about failed download: {str(e)}"
            ItemHistory.objects.create(item=item, details=message)
            return False, message
    
    def post_process(self, item, download_path):
        """Send a download completion notification to the manager for post-processing.
        
        This triggers the manager to import the downloaded files from the specified path.
        
        Args:
            item: Item object with hash, name, and clientid
            download_path: Local path where files have been downloaded/extracted to
            
        Returns:
            (success: bool, message: str)
        """
        try:
            url = self.apiurl + '/command'
            
            payload = {
                "name": "DownloadedEpisodesScan",
                "path": download_path,
                "downloadClientID": item.hash,
                "importMode": "Move"
            }
            
            response = requests.post(url, json=payload, headers=self.headers, timeout=30)
            
            if response.status_code in [200, 201]:
                message = f"Post-processing initiated: {download_path}"
                ItemHistory.objects.create(item=item, details=message)
                return True, message
            else:
                message = f"Post-processing failed (HTTP {response.status_code}): {response.text}"
                ItemHistory.objects.create(item=item, details=message)
                return False, message
                
        except Exception as e:
            message = f"Error initiating post-processing: {str(e)}"
            ItemHistory.objects.create(item=item, details=message)
            return False, message


class Sonarr(Arr):
    def __init__(self, manager):
        super().__init__(manager)
        self.apiurl = self.url + '/api/v3'

    def test(self):
        testurl = self.apiurl + '/system/status'
        try:
            r = requests.get(testurl, params=None, headers=self.headers)
            dt = r.json()
            return True, dt
        except Exception as e:
            return False, e
    
    def post_process(self, item, download_path):
        """Send DownloadedEpisodesScan command for TV shows."""
        try:
            import logging
            logger = logging.getLogger(__name__)
            
            url = self.apiurl + '/command'
            payload = {
                "name": "DownloadedEpisodesScan",
                "path": download_path,
                "downloadClientID": item.hash,
                "importMode": "Move"
            }
            logger.info(f"[Sonarr post_process] Sending command to {url}")
            logger.info(f"[Sonarr post_process] Payload: {payload}")
            
            response = requests.post(url, json=payload, headers=self.headers, timeout=30)
            logger.info(f"[Sonarr post_process] Response status: {response.status_code}")
            logger.debug(f"[Sonarr post_process] Response body: {response.text}")
            
            if response.status_code in [200, 201]:
                message = f"Post-processing initiated: {download_path}"
                ItemHistory.objects.create(item=item, details=message)
                return True, message
            else:
                message = f"Post-processing failed (HTTP {response.status_code}): {response.text}"
                ItemHistory.objects.create(item=item, details=message)
                return False, message
        except Exception as e:
            message = f"Error initiating post-processing: {str(e)}"
            ItemHistory.objects.create(item=item, details=message)
            return False, message


class Radarr(Arr):
    def __init__(self, manager):
        super().__init__(manager)
        self.apiurl = self.url + '/api/v3'

    def test(self):
        testurl = self.apiurl + '/system/status'
        try:
            r = requests.get(testurl, params=None, headers=self.headers)
            dt = r.json()
            return True, dt
        except Exception as e:
            return False, e

    def post_process(self, item, download_path):
        """Send DownloadedMoviesScan command for movies."""
        try:
            url = self.apiurl + '/command'
            payload = {
                "name": "DownloadedMoviesScan",
                "path": download_path,
                "downloadClientID": item.hash,
                "importMode": "Move"
            }
            response = requests.post(url, json=payload, headers=self.headers, timeout=30)
            
            if response.status_code in [200, 201]:
                message = f"Post-processing initiated: {download_path}"
                ItemHistory.objects.create(item=item, details=message)
                return True, message
            else:
                message = f"Post-processing failed (HTTP {response.status_code}): {response.text}"
                ItemHistory.objects.create(item=item, details=message)
                return False, message
        except Exception as e:
            message = f"Error initiating post-processing: {str(e)}"
            ItemHistory.objects.create(item=item, details=message)
            return False, message


class Lidarr(Arr):
    def __init__(self, manager):
        super().__init__(manager)
        self.apiurl = self.url + '/api/v1'

    def test(self):
        testurl = self.apiurl + '/system/status'
        try:
            r = requests.get(testurl, params=None, headers=self.headers)
            dt = r.json()
            return True, dt
        except Exception as e:
            return False, e

    def check_queue(self):
        url = self.apiurl + '/queue'
        try:
            r = requests.get(url, params=None, headers=self.headers)
            dt = self.parse_queue(r.json()['records'])
            return True, dt
        except Exception as e:
            return False, e

    def post_process(self, item, download_path):
        """Send DownloadedAlbumsScan command for music."""
        try:
            url = self.apiurl + '/command'
            payload = {
                "name": "DownloadedAlbumsScan",
                "path": download_path,
                "downloadClientID": item.hash,
                "importMode": "Move"
            }
            response = requests.post(url, json=payload, headers=self.headers, timeout=30)
            
            if response.status_code in [200, 201]:
                message = f"Post-processing initiated: {download_path}"
                ItemHistory.objects.create(item=item, details=message)
                return True, message
            else:
                message = f"Post-processing failed (HTTP {response.status_code}): {response.text}"
                ItemHistory.objects.create(item=item, details=message)
                return False, message
        except Exception as e:
            message = f"Error initiating post-processing: {str(e)}"
            ItemHistory.objects.create(item=item, details=message)
            return False, message


class Readarr(Arr):
    def __init__(self, manager):
        super().__init__(manager)
        self.apiurl = self.url + '/api/v1'

    def test(self):
        testurl = self.apiurl + '/system/status'
        try:
            r = requests.get(testurl, params=None, headers=self.headers)
            dt = r.json()
            return True, dt
        except Exception as e:
            return False, e

    def check_queue(self):
        url = self.apiurl + '/queue'
        try:
            r = requests.get(url, params=None, headers=self.headers)
            print(r.json())
            dt = self.parse_queue(r.json()['records'])
            return True, dt
        except Exception as e:
            return False, e

    def post_process(self, item, download_path):
        """Send DownloadedBooksScan command for books."""
        try:
            url = self.apiurl + '/command'
            payload = {
                "name": "DownloadedBooksScan",
                "path": download_path,
                "downloadClientID": item.hash,
                "importMode": "Move"
            }
            response = requests.post(url, json=payload, headers=self.headers, timeout=30)
            
            if response.status_code in [200, 201]:
                message = f"Post-processing initiated: {download_path}"
                ItemHistory.objects.create(item=item, details=message)
                return True, message
            else:
                message = f"Post-processing failed (HTTP {response.status_code}): {response.text}"
                ItemHistory.objects.create(item=item, details=message)
                return False, message
        except Exception as e:
            message = f"Error initiating post-processing: {str(e)}"
            ItemHistory.objects.create(item=item, details=message)
            return False, message


class Whisparr(Arr):
    def __init__(self, manager):
        super().__init__(manager)
        self.apiurl = self.url + '/api/v3'

    def test(self):
        testurl = self.apiurl + '/system/status'
        try:
            r = requests.get(testurl, params=None, headers=self.headers)
            dt = r.json()
            return True, dt
        except Exception as e:
            return False, e

    def post_process(self, item, download_path):
        """Send DownloadedScenesScan command for adult content."""
        try:
            url = self.apiurl + '/command'
            payload = {
                "name": "DownloadedScenesScan",
                "path": download_path,
                "downloadClientID": item.hash,
                "importMode": "Move"
            }
            response = requests.post(url, json=payload, headers=self.headers, timeout=30)
            
            if response.status_code in [200, 201]:
                message = f"Post-processing initiated: {download_path}"
                ItemHistory.objects.create(item=item, details=message)
                return True, message
            else:
                message = f"Post-processing failed (HTTP {response.status_code}): {response.text}"
                ItemHistory.objects.create(item=item, details=message)
                return False, message
        except Exception as e:
            message = f"Error initiating post-processing: {str(e)}"
            ItemHistory.objects.create(item=item, details=message)
            return False, message


class Blackhole:
    """Manager that monitors a directory for .nzb and .torrent files."""
    
    def __init__(self, manager):
        self.manager = manager
        self.name = manager.name
        self.monitor_directory = manager.monitor_directory
        self.monitor_subdirectories = manager.monitor_subdirectories
        self.category = manager.category
        self.torrent_downloader = manager.torrent_downloader
        self.nzb_downloader = manager.nzb_downloader
        self.temp_folder = manager.temp_folder
        self.poll_interval = manager.poll_interval
        self.move_on_complete = manager.move_on_complete
        self.delete_source = manager.delete_source
        self.duplicate_handling = manager.duplicate_handling
        self.enabled = manager.enabled
        self.scan_on_startup = manager.scan_on_startup
    
    def test(self):
        """Test that the monitor directory exists and is accessible."""
        import os
        import logging
        logger = logging.getLogger(__name__)
        
        if not self.monitor_directory:
            return False, "Monitor directory not configured"
        
        if not os.path.exists(self.monitor_directory):
            return False, f"Monitor directory does not exist: {self.monitor_directory}"
        
        if not os.path.isdir(self.monitor_directory):
            return False, f"Monitor path is not a directory: {self.monitor_directory}"
        
        if not os.access(self.monitor_directory, os.R_OK | os.W_OK):
            return False, f"Monitor directory is not readable/writable: {self.monitor_directory}"
        
        return True, f"Monitor directory accessible: {self.monitor_directory}"
    
    def get_files_to_process(self):
        """Scan monitor directory for .nzb and .torrent files.
        
        Returns:
            dict with 'torrent' and 'nzb' keys containing lists of file paths
        """
        import os
        import logging
        logger = logging.getLogger(__name__)
        
        nzb_files = []
        torrent_files = []
        
        if not self.monitor_directory or not os.path.exists(self.monitor_directory):
            logger.warning(f"Monitor directory does not exist: {self.monitor_directory}")
            return {'nzb': [], 'torrent': []}
        
        if self.monitor_subdirectories:
            # Walk all subdirectories
            for root, dirs, files in os.walk(self.monitor_directory):
                for filename in files:
                    lower = filename.lower()
                    if lower.endswith('.nzb'):
                        nzb_files.append(os.path.join(root, filename))
                    elif lower.endswith('.torrent'):
                        torrent_files.append(os.path.join(root, filename))
        else:
            # Only monitor root directory
            for filename in os.listdir(self.monitor_directory):
                filepath = os.path.join(self.monitor_directory, filename)
                if os.path.isfile(filepath):
                    lower = filename.lower()
                    if lower.endswith('.nzb'):
                        nzb_files.append(filepath)
                    elif lower.endswith('.torrent'):
                        torrent_files.append(filepath)
        
        logger.debug(f"Found {len(nzb_files)} .nzb and {len(torrent_files)} .torrent files in {self.monitor_directory}")
        
        return {'nzb': nzb_files, 'torrent': torrent_files}
    
    def get_category_for_file(self, filepath):
        """Determine the category for a file based on settings.
        
        Returns:
            str: category name
        """
        import os
        
        if self.monitor_subdirectories:
            # Use subfolder name as category
            dirname = os.path.dirname(filepath)
            if dirname.startswith(self.monitor_directory):
                subdir = dirname[len(self.monitor_directory):].lstrip(os.sep)
                if os.sep in subdir:
                    # Get first subdirectory
                    category = subdir.split(os.sep)[0]
                else:
                    category = subdir
            else:
                category = self.category or 'default'
        else:
            category = self.category or 'default'
        
        return category
    
    def send_to_downloader(self, filepath, file_type):
        """Send a file to the appropriate downloader.
        
        Args:
            filepath: Full path to the .nzb or .torrent file
            file_type: 'nzb' or 'torrent'
            
        Returns:
            (success: bool, download_id: str or None, message: str)
        """
        import os
        import logging
        import hashlib
        from entities.models import Downloader
        
        logger = logging.getLogger(__name__)
        
        if file_type == 'nzb':
            downloader = self.nzb_downloader
        elif file_type == 'torrent':
            downloader = self.torrent_downloader
        else:
            return False, None, f"Unknown file type: {file_type}"
        
        if not downloader:
            return False, None, f"No {file_type} downloader configured"
        
        # Generate a unique hash for this download
        # Use file path + timestamp for uniqueness
        file_hash = hashlib.md5(f"{filepath}{os.path.getmtime(filepath)}".encode()).hexdigest()
        
        try:
            # Get the downloader client
            client = downloader.client
            logger.debug(f"Sending {filepath} to {downloader.name}")
            logger.debug(f"Downloader client: {client}")
            logger.debug(f"Downloader client.client: {client.client}")
            
            # Add the file to the downloader
            if file_type == 'nzb':
                category = self.get_category_for_file(filepath)
                nzo_id = client.add(filepath, category=category)
                return True, nzo_id, f"Added to {downloader.name}"
            elif file_type == 'torrent':
                # For torrent, use the add method
                category = self.get_category_for_file(filepath)
                logger.debug(f"Calling client.add for torrent with category: {category}")
                torrent_hash = client.add(filepath, label=category)
                logger.debug(f"Torrent hash returned: {torrent_hash}")
                if torrent_hash:
                    return True, torrent_hash, f"Added to {downloader.name}"
                return False, None, f"Failed to load torrent"
            
        except Exception as e:
            logger.error(f"Error sending {filepath} to {downloader.name}: {e}")
            return False, None, str(e)
        
        return False, None, "Unknown error"
    
    def should_skip_file(self, filename):
        """Check if file should be skipped based on duplicate handling settings.
        
        Args:
            filename: Name of the file to check
            
        Returns:
            bool: True if file should be skipped
        """
        import os
        
        if self.duplicate_handling == 'skip':
            # Check if we've already processed this file
            from itemqueue.models import Item
            # Use filename as part of hash to detect duplicates
            existing = Item.objects.filter(name__icontains=filename).first()
            return existing is not None
        elif self.duplicate_handling == 'rename':
            # TODO: Implement rename logic
            return False
        elif self.duplicate_handling == 'overwrite':
            return False
        
        return False