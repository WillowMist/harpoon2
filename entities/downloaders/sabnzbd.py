from .base import BaseDownloader
import requests
import os
import time
import logging

logger = logging.getLogger(__name__)


class SABnzbdDownloader(BaseDownloader):
    optionfields = {
        'url': 'string',
        'apikey': 'string',
        'cleanup': 'boolean',
        'enabled': 'boolean',
    }

    def __init__(self, downloader=None):
        super().__init__(downloader)
        if downloader and hasattr(downloader, 'client'):
            self._init_client()
        else:
            self.reload = True
            self.client = None

    def _init_client(self):
        opts = self.options
        self.url = opts.get('url', '')
        self.apikey = opts.get('apikey', '')
        self.cleanup = opts.get('cleanup', False)
        self.enabled = opts.get('enabled', True)
        
        if self.url and self.apikey:
            # Ensure url doesn't end with /api
            self.api_url = self.url.rstrip('/')
            if not self.api_url.endswith('/api'):
                self.api_url += '/api'
            self.client = requests.Session()
            self.client.verify = False  # Allow self-signed certs
        else:
            self.client = None

    def _ensure_client(self):
        if self.reload or self.client is None:
            self._init_client()

    def _api_call(self, mode: str, params: dict = None, files: dict = None) -> dict:
        """Make an API call to SABnzbd.
        
        Args:
            mode: API mode (e.g., 'addnzbmember', 'queue', 'history')
            params: Additional parameters for the API call
            files: Files to upload (for adding NZBs)
        
        Returns:
            JSON response as dict
        """
        self._ensure_client()
        if not self.client:
            raise ConnectionError("SABnzbd not configured")
        
        api_params = {
            'apikey': self.apikey,
            'mode': mode,
            'output': 'json',
        }
        if params:
            api_params.update(params)
        
        try:
            if files:
                response = self.client.post(self.api_url, data=api_params, files=files)
            else:
                response = self.client.get(self.api_url, params=api_params)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logger.error(f"SABnzbd API error: {e}")
            return {'status': False, 'error': str(e)}

    def add(self, file_path: str, **kwargs) -> str:
        """Add an NZB file or URL to SABnzbd.
        
        Args:
            file_path: Local path to .nzb file or URL to NZB
            **kwargs: Optional parameters (category, etc.)
        
        Returns:
            NZO (SABnzbd job) ID
        """
        self._ensure_client()
        
        category = kwargs.get('category', 'default')
        name = kwargs.get('name', '')
        
        # Check if it's a file or URL
        if os.path.isfile(file_path):
            # Local file - try different approach: include apikey in URL
            logger.debug(f"Adding local file: {file_path}")
            with open(file_path, 'rb') as f:
                files = {'nzbfile': (os.path.basename(file_path), f)}
                # Put apikey in URL, other params in data
                url = f"{self.api_url}&mode=addlocalfile&output=json&nzbname={os.path.basename(file_path)}"
                result = self.client.post(url, files=files)
                logger.debug(f"addlocalfile result: {result.text}")
                result = result.json()
        elif file_path.startswith('http://') or file_path.startswith('https://'):
            # URL
            result = self._api_call('addurl', {'name': file_path, 'cat': category})
        else:
            raise ValueError("Invalid file path or URL")

        if result.get('status') is True:
            nzo_ids = result.get('nzo_ids', [])
            if nzo_ids:
                return nzo_ids[0]
        
        raise ValueError(f"Failed to add NZB: {result.get('error', 'Unknown error')}")

    def find(self, nzo_id: str):
        """Find an NZB by its job ID.
        
        Args:
            nzo_id: SABnzbd job ID
        
        Returns:
            Job info dict or None if not found
        """
        self._ensure_client()
        
        # Check queue first
        result = self._api_call('queue', {'limit': 100})
        if result.get('status') is True:
            queue = result.get('queue', {})
            slots = queue.get('slots', [])
            for slot in slots:
                if slot.get('nzo_id') == nzo_id:
                    return {
                        'id': nzo_id,
                        'name': slot.get('nzb_name', slot.get('name', '')),
                        'status': slot.get('status', ''),
                        'category': slot.get('category', ''),
                        'size': slot.get('size', 0),
                        'mb': slot.get('mb', 0),
                        'mbleft': slot.get('mbleft', 0),
                        'percentage': slot.get('percentage', 0),
                    }
        
        return None

    def get_status(self, nzo_id: str) -> dict:
        """Get the status of an NZB download.
        
        Args:
            nzo_id: SABnzbd job ID
        
        Returns:
            Dict with status information
        """
        job = self.find(nzo_id)
        
        if not job:
            # Check if completed in history
            result = self._api_call('history', {'limit': 50})
            if 'history' in result:  # Fixed: check for 'history' key, not 'status'
                history = result.get('history', {})
                slots = history.get('slots', [])
                for slot in slots:
                    if slot.get('nzo_id') == nzo_id:
                        return {
                            'status': 'completed' if slot.get('status') == 'Completed' else 'failed',
                            'completed': slot.get('status') == 'Completed',
                            'id': nzo_id,
                            'name': slot.get('nzb_name', ''),
                            'category': slot.get('category', ''),
                            'size': slot.get('bytes', 0),
                            'storage': slot.get('storage', ''),
                        }
            return {'status': 'not_found', 'completed': False}
        
        status = job.get('status', '').lower()
        return {
            'status': 'completed' if status == 'completed' else 'downloading',
            'completed': status == 'completed',
            'id': nzo_id,
            'name': job.get('name', ''),
            'category': job.get('category', ''),
            'size': job.get('size', 0),
            'percentage': job.get('percentage', 0),
        }

    def get_files(self, nzo_id: str) -> list:
        """Get the list of files in a completed NZB download.
        
        Args:
            nzo_id: SABnzbd job ID
        
        Returns:
            List of file info dicts
        """
        result = self._api_call('history', {'limit': 100})
        # SABnzbd returns success with 'history' key, not 'status'
        if 'history' in result:
            history = result.get('history', {})
            slots = history.get('slots', [])
            for slot in slots:
                if slot.get('nzo_id') == nzo_id and slot.get('status') == 'Completed':
                    # For completed downloads, return the storage path
                    storage = slot.get('storage', '')
                    return [{
                        'path': storage,
                        'name': slot.get('nzb_name', ''),
                        'category': slot.get('category', ''),
                    }]
        
        return []

    def delete(self, nzo_id: str) -> bool:
        """Remove an NZB from the queue or history.
        
        Args:
            nzo_id: SABnzbd job ID
        
        Returns:
            True if successful
        """
        self._ensure_client()
        
        # Try to delete from queue first
        result = self._api_call('queue', {'name': 'delete', 'value': nzo_id})
        if result.get('status') is True:
            return True
        
        # Try history
        result = self._api_call('history', {'name': 'delete', 'value': nzo_id, 'del_files': 0})
        return result.get('status') is True

    def test(self) -> tuple:
        """Test the connection to SABnzbd.
        
        Returns:
            (success: bool, message: str)
        """
        self._ensure_client()
        
        if not self.client:
            return (False, "SABnzbd not configured")
        
        try:
            result = self._api_call('queue')
            # SABnzbd returns success with 'queue' key, or error with 'status': False
            if 'queue' in result:
                queue = result.get('queue', {})
                slots = queue.get('slots', [])
                return (True, f"Connected. {len(slots)} items in queue.")
            elif result.get('status') is False:
                return (False, f"API error: {result.get('error', 'Unknown')}")
            return (False, f"API error: Unknown response format")
        except Exception as e:
            return (False, f"Connection failed: {str(e)}")

    def find_item(self, nzo_id: str):
        """Find an NZB by its job ID. For backward compatibility.
        
        Returns:
            Job ID if found, -1 if not found
        """
        job = self.find(nzo_id)
        if job:
            return nzo_id
        return -1


def SABNzbd(downloader=None):
    """Compatibility wrapper for original SABNzbd class name."""
    return SABnzbdDownloader(downloader)
