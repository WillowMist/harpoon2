import requests
import logging
from typing import Dict, List, Optional
from .base import BaseDownloader

logger = logging.getLogger(__name__)


class AirDCppClient:
    """Client for interacting with AirDC++ Web API."""
    
    def __init__(self, host: str, port: int, username: str, password: str, use_https: bool = False):
        """
        Initialize AirDC++ client.
        
        Args:
            host: AirDC++ server hostname/IP
            port: API port (default 5600)
            username: API username
            password: API password
            use_https: Use HTTPS instead of HTTP
        """
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.scheme = 'https' if use_https else 'http'
        self.base_url = f"{self.scheme}://{self.host}:{self.port}/api/v1"
        self.auth = (username, password)
        self.session = requests.Session()
        self.session.auth = self.auth
    
    def _make_request(self, method: str, endpoint: str, data: Optional[Dict] = None) -> Dict:
        """Make API request to AirDC++."""
        url = f"{self.base_url}{endpoint}"
        try:
            if method.upper() == 'GET':
                resp = self.session.get(url, timeout=10)
            elif method.upper() == 'POST':
                resp = self.session.post(url, json=data, timeout=10)
            elif method.upper() == 'DELETE':
                resp = self.session.delete(url, timeout=10)
            else:
                raise ValueError(f"Unsupported method: {method}")
            
            resp.raise_for_status()
            return resp.json() if resp.text else {}
        except requests.exceptions.RequestException as e:
            logger.error(f"AirDC++ API request failed: {e}")
            raise
    
    def get_transfers(self) -> List[Dict]:
        """
        Get list of active transfers/downloads.
        
        Returns:
            List of transfer objects with: id, name, status, progress, size, etc.
        """
        return self._make_request('GET', '/transfers')
    
    def get_transfer(self, transfer_id: int) -> Dict:
        """Get details for a specific transfer."""
        return self._make_request('GET', f'/transfers/{transfer_id}')
    
    def get_downloads(self) -> List[Dict]:
        """
        Get list of completed downloads/bundles.
        
        Returns:
            List of download/bundle objects
        """
        try:
            return self._make_request('GET', '/downloads')
        except Exception as e:
            logger.debug(f"GET /downloads failed: {e}")
            return []
    
    def get_finished_bundles(self) -> List[Dict]:
        """
        Get list of finished bundles (completed downloads).
        
        Returns:
            List of finished bundle objects
        """
        try:
            return self._make_request('GET', '/finished-bundles')
        except Exception as e:
            logger.debug(f"GET /finished-bundles failed: {e}")
            return []
    
    def get_events(self, limit: int = 20) -> List[Dict]:
        """
        Get recent events from AirDC++.
        
        Returns:
            List of event objects
        """
        try:
            events = self._make_request('GET', f'/events/{limit}')
            logger.info(f"AirDC++ get_events() raw response: {events}")
            return events
        except Exception as e:
            logger.debug(f"GET /events/{limit} failed: {e}")
            return []
    
    def get_share_info(self) -> Dict:
        """Get information about shared files/folders."""
        return self._make_request('GET', '/share')
    
    def refresh_share(self) -> Dict:
        """Trigger a refresh of the shared content."""
        return self._make_request('POST', '/share/refresh')
    
    def test_connection(self) -> tuple:
        """Test if connection to AirDC++ is working. Returns (success, message)"""
        try:
            # Try the transfers endpoint to verify API is working
            url = f"{self.base_url}/transfers"
            logger.info(f"Testing AirDC++ connection to: {url}")
            resp = self.session.get(url, timeout=10)
            resp.raise_for_status()
            
            # If we got here, connection is successful
            data = resp.json() if resp.text else []
            transfer_count = len(data) if isinstance(data, list) else 0
            msg = f"Connected successfully. {transfer_count} active transfers."
            logger.info(msg)
            return (True, msg)
        except requests.exceptions.ConnectionError as e:
            msg = f"Cannot connect to {self.base_url}: {e}"
            logger.error(msg)
            return (False, msg)
        except requests.exceptions.Timeout as e:
            msg = f"Connection timed out to {self.base_url}: {e}"
            logger.error(msg)
            return (False, msg)
        except requests.exceptions.HTTPError as e:
            msg = f"HTTP error: {e.response.status_code} {e.response.reason}"
            logger.error(msg)
            return (False, msg)
        except Exception as e:
            msg = f"Failed to connect to AirDC++: {e}"
            logger.error(msg)
            return (False, msg)


class AirDCppDownloader(BaseDownloader):
    """
    Downloader class for AirDC++ - monitors downloads.
    Follows same pattern as RTorrent and SABNzbd.
    """
    
    downloadertype = 'AirDC++'
    reload = False
    
    optionfields = {
        'host': 'string',
        'port': 'int',
        'username': 'string',
        'password': 'string',
        'use_https': 'boolean',
        'target_folder': 'folder',
    }
    
    def __init__(self, downloader=None):
        """
        Initialize AirDC++ downloader.
        
        Args:
            downloader: Downloader model instance (can be None for initialization)
        """
        self.downloader = downloader
        
        if downloader is None:
            # Used when getting option fields from the API
            self.config = {}
            self.client = None
            self.seedbox = None
            return
            
        self.config = downloader.options if downloader.options else {}
        self.seedbox = downloader.seedbox
        self.client = AirDCppClient(
            host=self.config.get('host', ''),
            port=self.config.get('port', 5600),
            username=self.config.get('username', ''),
            password=self.config.get('password', ''),
            use_https=self.config.get('use_https', False)
        )
    
    def test(self) -> tuple:
        """Test connection to AirDC++. Returns (success: bool, message: str)"""
        self._ensure_client()
        if not self.client:
            return (False, "Client not initialized")
        success, message = self.client.test_connection()
        return (success, message)
    
    def _ensure_client(self):
        """Ensure client is initialized"""
        if not self.client and self.config:
            self.client = AirDCppClient(
                host=self.config.get('host', ''),
                port=self.config.get('port', 5600),
                username=self.config.get('username', ''),
                password=self.config.get('password', ''),
                use_https=self.config.get('use_https', False)
            )
        # Ensure seedbox is also set from downloader model
        if self.downloader and not self.seedbox:
            self.seedbox = self.downloader.seedbox
    
    def add(self, file_path: str, **kwargs) -> str:
        """Not implemented for AirDC++ monitoring-only mode"""
        raise NotImplementedError("AirDC++ downloader is monitoring-only")
    
    def find(self, hash: str):
        """Find download by hash"""
        return self.find_completed_download(hash)
    
    def get_status(self, hash: str) -> dict:
        """Get status of a download"""
        if not self.client:
            return {}
        try:
            download = self.client.get_transfer(int(hash))
            return {
                'status': download.get('status'),
                'progress': download.get('progress', 0),
                'speed': download.get('speed', 0),
            }
        except Exception:
            return {}
    
    def get_files(self, hash: str) -> list:
        """Get files for a download"""
        if not self.client:
            return []
        try:
            download = self.client.get_transfer(int(hash))
            path = download.get('path')
            return [{'path': path, 'name': download.get('name')}] if path else []
        except Exception:
            return []
    
    def delete(self, hash: str) -> bool:
        """Not implemented for AirDC++ (no delete via API in monitoring mode)"""
        return False
    
    def get_active_downloads(self) -> List[Dict]:
        """
        Get list of active downloads with relevant info.
        
        Returns:
            List of downloads with: name, size, progress, status, path, etc.
        """
        if not self.client:
            return []
            
        try:
            transfers = self.client.get_transfers()
            
            # Process transfer info
            active = []
            for transfer in transfers:
                logger.debug(f"AirDC++ transfer object: {transfer}")
                
                # Extract relevant fields
                download_info = {
                    'id': transfer.get('id'),
                    'name': transfer.get('name'),
                    'size': transfer.get('size', 0),
                    'progress': transfer.get('progress', 0),
                    'bytes_transferred': transfer.get('bytes_transferred', 0),  # Direct bytes from /transfers
                    'status': transfer.get('status'),  # downloading, completed, failed, etc.
                    'path': transfer.get('path'),
                    'speed': transfer.get('speed', 0),
                    'eta': transfer.get('eta'),
                }
                active.append(download_info)
            
            return active
        except Exception as e:
            logger.error(f"Failed to get AirDC++ downloads: {e}")
            return []
    
    def find_completed_download(self, name: str, size: Optional[int] = None) -> Optional[Dict]:
        """
        Find a completed download by name (and optionally size).
        
        Args:
            name: Download name to search for
            size: Optional file size to match
        
        Returns:
            Download info dict if found, None otherwise
        """
        try:
            downloads = self.get_active_downloads()
            
            for download in downloads:
                # Check if status is completed
                if download['status'] is None:
                    continue
                    
                status_lower = download['status'].lower()
                if status_lower not in ['completed', 'done', 'finished']:
                    continue
                
                # Match by name
                if download['name'].lower() != name.lower():
                    continue
                
                # Match by size if provided
                if size and download['size'] != size:
                    continue
                
                return download
            
            return None
        except Exception as e:
            logger.error(f"Error finding completed download: {e}")
            return None
    
    def get_download_path(self, download_id: str) -> Optional[str]:
        """Get the local path for a download."""
        try:
            download = self.client.get_transfer(int(download_id))
            return download.get('path')
        except Exception as e:
            logger.error(f"Failed to get download path: {e}")
            return None

    def get_completed(self) -> list:
        """Get completed downloads from AirDC++.
        
        Uses the /events/40 endpoint to get recent events including completed downloads.
        
        Returns:
            List of completed download info dicts
        """
        if not self.client:
            return []
        
        try:
            events = self.client.get_events(limit=40)
            completed = []
            seen_hashes = set()
            
            for event in events:
                text = event.get('text', '')
                
                # Look for "has finished downloading" in the event text
                if 'has finished downloading' in text.lower():
                    # Extract bundle name and path from text
                    # Format: "The bundle <filename> has finished downloading"
                    if text.startswith('The bundle '):
                        # Extract the filename between "The bundle " and " has finished downloading"
                        name = text.replace('The bundle ', '').replace(' has finished downloading', '').strip()
                        
                        # Skip if it's a directory notification (ends with /)
                        if name.endswith('/'):
                            continue
                        
                        # Build the full path - use the configured target folder as base
                        # The name is the filename, so join with the base folder
                        base_folder = self.config.get('target_folder', '/Downloads')
                        path = f"{base_folder}/{name}" if not name.startswith('/') else name
                        
                        # Create a hash from the name for tracking
                        import hashlib
                        hash_value = hashlib.md5(name.encode()).hexdigest()
                        
                        # Avoid duplicates
                        if hash_value in seen_hashes:
                            continue
                        seen_hashes.add(hash_value)
                        
                        completed.append({
                            'hash': hash_value,
                            'name': name,
                            'completed': True,
                            'size': 0,
                            'path': path,
                        })
            
            logger.info(f"AirDC++ get_completed() returned {len(completed)} items from events")
            return completed
        except Exception as e:
            logger.error(f"Error getting AirDC++ completed downloads: {e}")
            return []
        
        try:
            events = self.client.get_events(limit=20)
            completed = []
            seen_hashes = set()
            
            for event in events:
                text = event.get('text', '')
                
                # Look for "has finished downloading" in the event text
                if 'has finished downloading' in text.lower():
                    # Extract bundle name from text
                    # Format: "The bundle Xena.1x17.The.Royal.Couple.of.Thieves.dvdrip-Jem_TVC.avi has finished downloading"
                    if text.startswith('The bundle '):
                        # Extract the filename between "The bundle " and " has finished downloading"
                        name = text.replace('The bundle ', '').replace(' has finished downloading', '').strip()
                        
                        # Skip if it's a directory notification (ends with /)
                        if name.endswith('/'):
                            continue
                        
                        # Create a hash from the name for tracking
                        import hashlib
                        hash_value = hashlib.md5(name.encode()).hexdigest()
                        
                        # Avoid duplicates
                        if hash_value in seen_hashes:
                            continue
                        seen_hashes.add(hash_value)
                        
                        # Extract path if present in a separate event
                        path = ''
                        
                        completed.append({
                            'hash': hash_value,
                            'name': name,
                            'completed': True,
                            'size': 0,
                            'path': path,
                        })
            
            logger.info(f"AirDC++ get_completed() returned {len(completed)} items from events")
            return completed
        except Exception as e:
            logger.error(f"Error getting AirDC++ completed downloads: {e}")
            return []

    def verify_completion(self, hash: str) -> tuple:
        """Verify that a download is complete.
        
        For AirDC++, completion is handled via events, not by hash lookup.
        This method always returns success if the item exists.
        """
        return (True, "AirDC++ completion verified via events")

    def process_completed(self):
        """Process completed downloads from AirDC++.
        
        Gets completed downloads from events, creates items if needed,
        and queues them for postprocessing.
        
        This is the main entry point called by check_downloaders.
        """
        from itemqueue.models import Item, ItemHistory
        
        if not self.client:
            return
        
        try:
            events = self.client.get_events(limit=40)
            seen_hashes = set()
            
            for event in events:
                text = event.get('text', '')
                
                if 'has finished downloading' in text.lower():
                    if text.startswith('The bundle '):
                        name = text.replace('The bundle ', '').replace(' has finished downloading', '').strip()
                        
                        if name.endswith('/'):
                            continue
                        
                        base_folder = self.config.get('target_folder', '/Downloads')
                        full_path = f"{base_folder}/{name}" if not name.startswith('/') else name
                        
                        import hashlib
                        hash_value = hashlib.md5(name.encode()).hexdigest()
                        
                        if hash_value in seen_hashes:
                            continue
                        seen_hashes.add(hash_value)
                        
                        try:
                            item = Item.objects.get(hash__iexact=hash_value)
                            logger.debug(f"AirDC++: Found existing item {item.name} (status={item.status})")
                            if item.status not in ['Completed', 'Failed', 'PostProcessing']:
                                if item.downloader:
                                    logger.info(f"AirDC++: Queueing postprocess for {item.name}")
                                    from itemqueue.tasks import postprocess_item
                                    postprocess_item.delay(hash_value)
                        except Item.DoesNotExist:
                            logger.info(f"AirDC++: Creating item for {name}")
                            item = Item.objects.create(
                                hash=hash_value,
                                name=name,
                                status='Grabbed',
                                downloader=self.downloader if hasattr(self, 'downloader') else None,
                                size=0,
                            )
                            ItemHistory.objects.create(
                                item=item,
                                details=f'Download completed in AirDC++: {name}'
                            )
                            from itemqueue.tasks import postprocess_item
                            postprocess_item.delay(hash_value)
            
            logger.info(f"AirDC++ process_completed() completed")
        except Exception as e:
            logger.error(f"AirDC++ process_completed() error: {e}")


    def get_download_info(self, hash: str) -> dict:
        """Get information needed for file transfer post-processing.
        
        Returns:
            Dict with remote_dir, files_to_copy, is_single_file, name
        """
        import os
        from itemqueue.models import Item
        
        # Get seedbox base_download_folder to substitute for /Downloads
        seedbox = getattr(self, 'seedbox', None)
        if seedbox and seedbox.base_download_folder:
            airdc_base = seedbox.base_download_folder
        else:
            airdc_base = '/Downloads'
        
        try:
            item = Item.objects.get(hash=hash)
            item_name = item.name
            
            # Strip directory prefix from item name (e.g., "13/A-Next..." -> "A-Next...")
            # This happens when AirDC++ returns path with subdirectory prefix for multi-file bundles
            if '/' in item_name:
                item_name = os.path.basename(item_name)
            
            # The item_name from events is just the filename (e.g., "Xena.1x17...")
            # Replace /Downloads with the actual seedbox base folder
            if item_name and item_name.startswith('/Downloads/'):
                full_path = item_name.replace('/Downloads/', f'{airdc_base}/', 1)
            elif item_name:
                full_path = f"{airdc_base}/{item_name}"
            else:
                full_path = airdc_base
            
            # Check if this is a bundle/folder by checking if it has a file extension
            # Folders like "Xena - Season 5" don't have extensions, files do
            basename = os.path.basename(full_path)
            is_bundle = '.' not in basename
            
            logger.info(f"AirDC++ get_download_info: source_path={full_path}, is_bundle={is_bundle}")
            
            if is_bundle:
                # For bundles/folders, transfer all files in that bundle folder
                return {
                    'remote_dir': full_path,  # Use bundle folder as remote_dir, not parent
                    'files_to_copy': None,  # Transfer all files in folder
                    'is_single_file': False,
                    'name': full_path,
                }
            else:
                # For individual files, use single file transfer
                return {
                    'remote_dir': os.path.dirname(full_path),
                    'files_to_copy': [os.path.basename(full_path)],
                    'is_single_file': True,
                    'name': full_path,
                }
        except Item.DoesNotExist:
            logger.debug(f"AirDC++ get_download_info: item not found for hash={hash}")
        
        # Fallback
        return {
            'remote_dir': airdc_base,
            'files_to_copy': None,
            'is_single_file': False,
            'name': hash,
        }

    def cleanup(self, file_transfer) -> tuple:
        """Cleanup not implemented for AirDC++ downloader."""
        return (True, "Cleanup not implemented for AirDC++")


def AirDCpp(downloader=None):
    """Compatibility wrapper for AirDC++ downloader class name."""
    return AirDCppDownloader(downloader)
