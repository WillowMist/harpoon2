from .base import BaseDownloader
import logging
import hashlib

logger = logging.getLogger(__name__)


class QBittorrentDownloader(BaseDownloader):
    optionfields = {
        'host': 'string',
        'port': 'int',
        'username': 'string',
        'password': 'string',
        'use_ssl': 'boolean',
    }

    def __init__(self, downloader=None):
        super().__init__(downloader)
        if downloader and hasattr(downloader, 'client'):
            self._init_client()
        else:
            self.reload = True
            self.client = None

    def _init_client(self):
        import qbittorrentapi
        import bencoder
        
        opts = self.options
        self.host = opts.get('host', 'localhost')
        self.port = opts.get('port', 8080)
        self.username = opts.get('username', 'admin')
        self.password = opts.get('password', 'adminadmin')
        self.use_ssl = opts.get('use_ssl', False)
        
        try:
            self.client = qbittorrentapi.Client(
                host=self.host,
                port=self.port,
                username=self.username,
                password=self.password,
                REQUESTS_ARGS={'verify': False}  # Allow self-signed certs
            )
            # Test connection
            self.client.auth_log_in()
            logger.info(f"QBittorrent client initialized: {self.username}@{self.host}:{self.port}")
        except Exception as e:
            logger.error(f"Failed to initialize QBittorrent client: {e}")
            self.client = None

    def _ensure_client(self):
        if self.reload or self.client is None:
            self._init_client()

    def add(self, file_path: str, **kwargs) -> str:
        """Add a torrent file or URL to qBittorrent.
        
        Args:
            file_path: Local path to .torrent file or URL (magnet, http, etc.)
            **kwargs: Optional parameters
        
        Returns:
            Torrent hash (infohash)
        """
        self._ensure_client()
        
        if not self.client:
            raise ConnectionError("QBittorrent not configured")
        
        try:
            # Check if it's a file or URL
            import os
            if os.path.isfile(file_path):
                # Local .torrent file
                with open(file_path, 'rb') as f:
                    torrent_data = f.read()
                
                # Log what we're about to send
                logger.debug(f"QBittorrent add() - file: {file_path}, save_path: {kwargs.get('save_path')}, category: {kwargs.get('category')}, is_paused: {kwargs.get('is_paused')}")
                
                # Add torrent file
                result = self.client.torrents_add(
                    torrent_files=[file_path],
                    save_path=kwargs.get('save_path') or None,
                    category=kwargs.get('category') or None,
                    is_paused=kwargs.get('is_paused', False)
                )
                
                logger.debug(f"QBittorrent add() result: '{result}'")
                
                # Get the torrent hash from the file content
                import bencoder
                torrent_info = bencoder.decode(torrent_data)
                info_hash = hashlib.sha1(bencoder.encode(torrent_info[b'info'])).hexdigest().upper()
                
                # qBittorrent returns empty string on success
                if result == '':
                    logger.info(f"Added torrent file: {file_path} -> {info_hash}")
                    return info_hash
                else:
                    # Check if the torrent already exists in qBittorrent
                    existing = self.find(info_hash)
                    if existing:
                        # Torrent already exists - that's fine, return the hash
                        logger.info(f"Torrent already exists in QBittorrent: {file_path} -> {info_hash}")
                        return info_hash
                    else:
                        # Real error - torrent doesn't exist and couldn't add it
                        logger.error(f"Failed to add torrent: {result} (torrent hash: {info_hash})")
                        raise Exception(f"Failed to add torrent: {result}")
            else:
                # URL or magnet link
                result = self.client.torrents_add(
                    urls=[file_path],
                    save_path=kwargs.get('save_path', None),
                    category=kwargs.get('category', None),
                    is_paused=kwargs.get('is_paused', False)
                )
                
                if result == '':
                    logger.info(f"Added torrent URL/magnet: {file_path}")
                    # For URLs/magnets, qBittorrent generates the hash asynchronously
                    # We'll return a placeholder and look it up later
                    # Return a hash based on the URL for tracking
                    return hashlib.sha1(file_path.encode()).hexdigest().upper()[:40]
                else:
                    logger.error(f"Failed to add torrent: {result}")
                    raise Exception(f"Failed to add torrent: {result}")
                    
        except Exception as e:
            logger.error(f"QBittorrent add error: {e}")
            raise

    def find(self, hash: str):
        """Find torrent by hash, return info dict or None"""
        self._ensure_client()
        
        if not self.client:
            return None
        
        try:
            # qBittorrent uses uppercase hashes
            hash = hash.upper()
            torrent = self.client.torrents_info(torrent_hashes=hash)
            
            if torrent and len(torrent) > 0:
                t = torrent[0]
                return {
                    'hash': t.hash,
                    'name': t.name,
                    'size': t.size,
                    'progress': t.progress,
                    'state': t.state,
                    'category': t.category,
                    'completed': t.completed,
                    'save_path': t.save_path,
                }
            return None
        except Exception as e:
            logger.debug(f"QBittorrent find error for {hash}: {e}")
            return None

    def get_status(self, hash: str) -> dict:
        """Get status (downloading, completed, etc.)"""
        torrent = self.find(hash)
        
        if not torrent:
            return {'status': 'unknown', 'error': 'Not found'}
        
        # Map qBittorrent states to our status
        state_map = {
            'downloading': 'downloading',
            'uploading': 'seeding',
            'paused': 'paused',
            'queued': 'queued',
            'checking': 'checking',
            'moving': 'moving',
            'error': 'error',
        }
        
        qbt_state = torrent.get('state', '').lower()
        status = state_map.get(qbt_state, qbt_state)
        
        # Check if completed
        if torrent.get('completed', 0) == torrent.get('size', 0) and torrent.get('size', 0) > 0:
            status = 'completed'
        
        return {
            'status': status,
            'progress': int(torrent.get('progress', 0) * 100),
            'completed': torrent.get('completed', 0),
            'size': torrent.get('size', 0),
            'name': torrent.get('name', ''),
        }

    def get_files(self, hash: str) -> list:
        """Get file list for post-processing"""
        self._ensure_client()
        
        if not self.client:
            return []
        
        try:
            hash = hash.upper()
            files = self.client.torrents_files(hash)
            
            result = []
            for f in files:
                result.append({
                    'name': f.name,
                    'size': f.size,
                    'progress': f.progress,
                    'priority': f.priority,
                    'is_seed': f.is_seed,
                })
            
            return result
        except Exception as e:
            logger.error(f"QBittorrent get_files error for {hash}: {e}")
            return []

    def get_completed(self) -> list:
        """Get all completed torrents.
        
        Returns:
            List of dicts with hash, name, and completed size for each completed torrent
        """
        self._ensure_client()
        
        if not self.client:
            return []
        
        try:
            # Get all torrents
            all_torrents = self.client.torrents_info()
            
            completed = []
            for torrent in all_torrents:
                # Check if torrent is completed (completed size equals total size)
                if torrent.completed == torrent.size and torrent.size > 0:
                    completed.append({
                        'hash': torrent.hash.upper(),
                        'name': torrent.name,
                        'completed': torrent.completed,
                        'size': torrent.size,
                        'save_path': torrent.save_path,
                        'category': torrent.category,
                    })
            
            logger.debug(f"QBittorrent: Found {len(completed)} completed torrent(s)")
            return completed
        except Exception as e:
            logger.error(f"QBittorrent get_completed error: {e}")
            return []

    def verify_completion(self, hash: str) -> tuple:
        """Verify that a torrent is complete and ready for post-processing.
        
        Returns:
            (success: bool, message: str)
        """
        self._ensure_client()
        
        if not self.client:
            return (False, "QBittorrent client not initialized")
        
        try:
            hash = hash.upper()
            torrent = self.client.torrents_info(torrent_hashes=hash)
            
            if not torrent or len(torrent) == 0:
                return (False, f"Torrent {hash} not found in QBittorrent")
            
            t = torrent[0]
            
            # Check if completed
            if t.completed != t.size or t.size == 0:
                return (False, f"Torrent {hash} not complete on QBittorrent")
            
            return (True, "Torrent verified complete")
        except Exception as e:
            return (False, f"Error verifying torrent: {str(e)}")

    def get_download_info(self, hash: str) -> dict:
        """Get information needed for file transfer post-processing.
        
        Returns:
            Dict with remote_dir, files_to_copy, is_single_file, name
        """
        self._ensure_client()
        
        if not self.client:
            return {
                'remote_dir': '',
                'files_to_copy': None,
                'is_single_file': False,
                'name': '',
            }
        
        try:
            hash = hash.upper()
            torrent = self.client.torrents_info(torrent_hashes=hash)
            
            if not torrent or len(torrent) == 0:
                return {
                    'remote_dir': '',
                    'files_to_copy': None,
                    'is_single_file': False,
                    'name': '',
                }
            
            t = torrent[0]
            
            # Check if single file - compare completed bytes to total size
            # For single file torrents, the torrent name ends with a file extension
            is_single_file = '.' in t.name.split('/')[-1]
            
            return {
                'remote_dir': t.save_path,
                'files_to_copy': None,  # Transfer all files from save_path
                'is_single_file': is_single_file,
                'name': t.name,
            }
        except Exception as e:
            logger.error(f"QBittorrent get_download_info error: {e}")
            return {
                'remote_dir': '',
                'files_to_copy': None,
                'is_single_file': False,
                'name': '',
            }

    def delete(self, hash: str) -> bool:
        """Remove from client"""
        self._ensure_client()
        
        if not self.client:
            return False
        
        try:
            hash = hash.upper()
            self.client.torrents_delete(torrent_hashes=hash, delete_files=False)
            logger.info(f"Deleted torrent: {hash}")
            return True
        except Exception as e:
            logger.error(f"QBittorrent delete error for {hash}: {e}")
            return False

    def test(self) -> tuple:
        """Connection test: (success: bool, message: str)"""
        import qbittorrentapi
        
        try:
            self._ensure_client()
            
            if not self.client:
                return (False, "QBittorrent client not initialized")
            
            # Try to get version info
            version = self.client.app.version
            api_version = self.client.app.web_api_version
            
            return (True, f"Connected to QBittorrent v{version} (API v{api_version})")
        except qbittorrentapi.LoginFailed:
            return (False, "Authentication failed - check username/password")
        except qbittorrentapi.APIError as e:
            return (False, f"API error: {e}")
        except Exception as e:
            return (False, f"Connection failed: {e}")


def QBittorrent(downloader=None):
    """Compatibility wrapper for QBittorrent downloader class name."""
    return QBittorrentDownloader(downloader)
