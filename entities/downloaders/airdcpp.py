import requests
import logging
from typing import Dict, List, Optional

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
    
    def test_connection(self) -> bool:
        """Test if connection to AirDC++ is working."""
        try:
            self._make_request('GET', '/sessions/current')
            return True
        except Exception as e:
            logger.error(f"Failed to connect to AirDC++: {e}")
            return False


class AirDCppDownloader:
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
    
    def test(self) -> bool:
        """Test connection to AirDC++."""
        if not self.client:
            return False
        return self.client.test_connection()
    
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
                # Extract relevant fields
                download_info = {
                    'id': transfer.get('id'),
                    'name': transfer.get('name'),
                    'size': transfer.get('size', 0),
                    'progress': transfer.get('progress', 0),
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
