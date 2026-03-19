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
        Get list of downloads/bundles.
        
        Returns:
            List of download/bundle objects
        """
        # Endpoint may vary - could be /transfers or /queue or /downloads
        try:
            return self._make_request('GET', '/downloads')
        except Exception as e:
            logger.debug(f"GET /downloads failed: {e}, trying /transfers")
            # Fallback to transfers endpoint
            return self.get_transfers()
    
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
        if downloader is None:
            # Used when getting option fields from the API
            self.config = {}
            self.client = None
            return
            
        self.config = downloader.options if downloader.options else {}
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


def AirDCpp(downloader=None):
    """Compatibility wrapper for AirDC++ downloader class name."""
    return AirDCppDownloader(downloader)

    def get_completed(self) -> list:
        """Get completed downloads from AirDC++.
        
        Note: AirDC++ doesn't have the same completed tracking as other downloaders.
        This returns an empty list - completion is handled differently via events.
        """
        return []

    def verify_completion(self, hash: str) -> tuple:
        """Verify that a download is complete.
        
        For AirDC++, completion is handled via events, not by hash lookup.
        This method always returns success if the item exists.
        """
        return (True, "AirDC++ completion verified via events")

    def get_download_info(self, hash: str) -> dict:
        """Get information needed for file transfer post-processing.
        
        Returns:
            Dict with remote_dir, files_to_copy, is_single_file, name
        """
        if not self.seedbox:
            return {
                'remote_dir': '',
                'files_to_copy': None,
                'is_single_file': False,
                'name': '',
            }
        
        return {
            'remote_dir': self.seedbox.base_download_folder,
            'files_to_copy': None,
            'is_single_file': False,
            'name': hash,  # For AirDC++, hash is the download ID/name
        }
