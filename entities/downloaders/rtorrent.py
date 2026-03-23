from .base import BaseDownloader
import xmlrpc.client
import urllib.parse


class RTorrentXMLRPC:
    """Direct XML-RPC client for rTorrent using HTTP(S) transport via rutorrent web interface."""
    
    def __init__(self, address: str, timeout: float = 30.0):
        self.address = address
        self.timeout = timeout
        self._client = None
        
    def _get_client(self):
        if self._client is None:
            # Use Python's built-in xmlrpc.client with the full HTTP(S) URL
            # Address format: http://user:pass@host:port/path or https://...
            self._client = xmlrpc.client.ServerProxy(self.address)
        return self._client
    
    def download_list(self, view: str = "main"):
        """Get list of torrent info hashes."""
        client = self._get_client()
        try:
            return client.d.multicall2('', view, 'd.hash=')
        except Exception:
            try:
                return client.download_list(view)
            except Exception:
                return []
    
    def active_downloads(self, view: str = "main", limit: int = 20):
        """Get active (incomplete) downloads with full details.
        
        Uses multicall for efficiency instead of individual queries.
        """
        client = self._get_client()
        try:
            # Get torrents with multiple fields in one call
            # Format: [hash, name, size_bytes, completed_bytes, is_complete]
            result = client.d.multicall2('', view, 
                'd.hash=',
                'd.name=',
                'd.size_bytes=',
                'd.completed_bytes=',
                'd.complete=',
                'd.ratio=',
                'd.directory='
            )
            
            # Filter for incomplete torrents and limit
            active = []
            for torrent_data in result:
                if not isinstance(torrent_data, list) or len(torrent_data) < 5:
                    continue
                
                h, name, size, completed, is_complete = torrent_data[:5]
                ratio = torrent_data[5] if len(torrent_data) > 5 else 0
                directory = torrent_data[6] if len(torrent_data) > 6 else ''
                
                # Only include incomplete torrents
                if not is_complete:
                    pct = (completed / size * 100) if size > 0 else 0
                    active.append({
                        'hash': h,
                        'name': name,
                        'size': size,
                        'completed': completed,
                        'percent': pct,
                        'ratio': ratio / 1000.0 if ratio else 0,
                        'directory': directory,
                    })
                    
                    if len(active) >= limit:
                        break
            
            return active
        except Exception as e:
            return []
    
    def d_name(self, info_hash: str):
        """Get torrent name."""
        return self._get_client().d.name(info_hash)
    
    def d_size_bytes(self, info_hash: str):
        """Get total size in bytes."""
        return self._get_client().d.size_bytes(info_hash)
    
    def d_completed_bytes(self, info_hash: str):
        """Get completed bytes."""
        return self._get_client().d.completed_bytes(info_hash)
    
    def d_is_complete(self, info_hash: str):
        """Check if torrent is complete."""
        return self._get_client().d.complete(info_hash)
    
    def d_ratio(self, info_hash: str):
        """Get ratio (permille)."""
        return self._get_client().d.ratio(info_hash)
    
    def d_directory(self, info_hash: str):
        """Get torrent directory."""
        return self._get_client().d.directory(info_hash)
    
    def d_erase(self, info_hash: str):
        """Delete torrent."""
        return self._get_client().d.erase(info_hash)
    
    def d_start(self, info_hash: str):
        """Start a torrent."""
        return self._get_client().d.start(info_hash)
    
    def load_torrent(self, file_path: str):
        """Load a torrent file."""
        with open(file_path, 'rb') as f:
            data = xmlrpc.client.Binary(f.read())
        return self._get_client().load.raw(data, '')
    
    def load_start(self, file_path: str):
        """Load and start a torrent file."""
        with open(file_path, 'rb') as f:
            data = xmlrpc.client.Binary(f.read())
        return self._get_client().load.raw_start(data, '')
    
    def load_magnet(self, magnet_uri: str, info_hash: str):
        """Load a magnet link."""
        return self._get_client().load.magnet(magnet_uri, '', info_hash)


class RTorrentDownloader(BaseDownloader):
    optionfields = {
        'host': 'string',
        'port': 'int',
        'url_path': 'string',
        'use_ssl': 'boolean',
        'username': 'string',
        'password': 'string',
        'startonload': 'boolean',
    }

    def __init__(self, downloader=None):
        import logging
        logger = logging.getLogger(__name__)
        
        super().__init__(downloader)
        self._rtorrent = None
        
        if downloader and self.options:
            try:
                self._init_client()
            except Exception as e:
                logger.error(f"Failed to init RTorrent client: {e}")
                self._rtorrent = None
        else:
            self._rtorrent = None

    def _init_client(self):
        import logging
        logger = logging.getLogger(__name__)
        
        opts = self.options
        protocol = 'https' if opts.get('use_ssl', True) else 'http'
        host = opts.get('host', '')
        port = opts.get('port', 443)
        url_path = opts.get('url_path', 'destron23/RPC1')
        username = opts.get('username', '')
        password = opts.get('password', '')

        if username and password:
            auth = f"{username}:{password}@"
        else:
            auth = ''

        address = f"{protocol}://{auth}{host}:{port}/{url_path}"
        
        # Use lib/rtorrent like v1 does
        from lib.rtorrent import RTorrent
        try:
            self._rtorrent = RTorrent(address)
            logger.debug(f"RTorrent client initialized: {self._rtorrent}")
        except Exception as e:
            logger.error(f"Failed to create RTorrent client: {e}")
            self._rtorrent = None
        
        self.start_on_load = opts.get('startonload', True)

    def _ensure_client(self):
        if self._rtorrent is None:
            self._init_client()

    @property
    def client(self):
        """Return self for compatibility."""
        return self
    
    def _get_rtorrent(self):
        """Get the lib/rtorrent RTorrent instance."""
        self._ensure_client()
        return self._rtorrent

    def add(self, file_path: str, **kwargs) -> str:
        """Add a torrent file or URL to rTorrent.
        
        Args:
            file_path: Local path to .torrent file or a magnet URL
            **kwargs: Optional parameters (label, etc.)
        
        Returns:
            Torrent hash (info_hash)
        """
        import logging
        import os
        
        logger = logging.getLogger(__name__)
        
        self._ensure_client()
        
        if self._rtorrent is None:
            raise Exception("RTorrent client not initialized")
        
        label = kwargs.get('label', '')
        
        # Use lib/rtorrent like v1 does - with verify_load=True
        try:
            # load_torrent returns a Torrent object if successful
            torrent = self._rtorrent.load_torrent(file_path, start=self.start_on_load, verify_load=True)
            
            if torrent is None:
                raise Exception("Failed to load torrent - verify_load returned None")
            
            info_hash = torrent.info_hash
            logger.debug(f"Successfully loaded torrent: {info_hash}")
            
            # Set label after loading (like v1 does)
            if label:
                try:
                    torrent.set_custom(1, label)
                    logger.debug(f"Set label '{label}' on torrent {info_hash}")
                except Exception as e:
                    logger.debug(f"Failed to set label: {e}")
            
            return info_hash
            
        except AssertionError as e:
            logger.error(f"Torrent was not added successfully: {e}")
            raise Exception(f"Failed to add torrent: {e}")
        except Exception as e:
            logger.error(f"Error adding torrent: {e}")
            raise
    
    def _set_label(self, info_hash: str, label: str):
        """Set a label on a torrent."""
        import logging
        logger = logging.getLogger(__name__)
        
        rpc = self._rtorrent.client
        
        try:
            rpc.d.custom1.set(info_hash, label)
            logger.debug(f"Set label '{label}' on torrent {info_hash}")
        except Exception as e:
            logger.debug(f"Label set method 1 failed: {e}")
            try:
                rpc.d.custom1(info_hash, label)
                logger.debug(f"Set label via method 2")
            except Exception as e2:
                logger.debug(f"Label set method 2 failed: {e2}")

    def find(self, hash: str):
        """Find a torrent by its info hash.
        
        Args:
            hash: Torrent info hash (40 char hex string)
        
        Returns:
            Torrent info dict or None if not found
        """
        self._ensure_client()
        try:
            rpc = self._rtorrent.client
            torrents = rpc.download_list() or []
            torrent_hashes = [t[0].upper() if isinstance(t, list) else t.upper() for t in torrents]
            
            if hash.upper() in torrent_hashes:
                name = rpc.d.name(hash)
                size_bytes = rpc.d.size_bytes(hash)
                completed_bytes = rpc.d.completed_bytes(hash)
                is_complete = rpc.d.complete(hash)
                ratio = rpc.d.ratio(hash)
                directory = rpc.d.directory(hash)
                label = rpc.d.custom1(hash) if hasattr(rpc.d, 'custom1') else ''
                
                return {
                    'name': name,
                    'info_hash': hash,
                    'completed': is_complete,
                    'size_bytes': size_bytes,
                    'bytes_downloaded': completed_bytes,
                    'ratio': ratio / 1000.0 if ratio else 0,
                    'directory': directory,
                    'label': label,
                }
        except Exception as e:
            pass
        return None

    def get_status(self, hash: str) -> dict:
        """Get the status of a torrent.
        
        Args:
            hash: Torrent info hash
        
        Returns:
            Dict with status information
        """
        self._ensure_client()
        torrent = self.find(hash)
        if not torrent:
            return {'status': 'not_found', 'completed': False}
        
        return {
            'status': 'completed' if torrent.get('completed') else 'downloading',
            'completed': torrent.get('completed', False),
            'hash': hash,
            'name': torrent.get('name', ''),
            'size': torrent.get('size_bytes', 0),
            'downloaded': torrent.get('bytes_downloaded', 0),
            'ratio': torrent.get('ratio', 0),
        }
    
    def get_completed(self) -> list:
        """Get list of completed torrents using efficient multicall.
        
        Returns:
            List of torrent info dicts for completed torrents
        """
        self._ensure_client()
        try:
            rpc = self._rtorrent.client
            result = rpc.d.multicall2('', 'main',
                'd.hash=',
                'd.name=',
                'd.size_bytes=',
                'd.complete=',
                'd.directory='
            )
            
            completed_torrents = []
            for torrent_data in result:
                if not isinstance(torrent_data, list) or len(torrent_data) < 5:
                    continue
                
                hash_val, name, size, is_complete, directory = torrent_data[:5]
                
                if is_complete:
                    completed_torrents.append({
                        'hash': hash_val.upper() if hash_val else '',
                        'info_hash': hash_val.upper() if hash_val else '',
                        'name': name,
                        'size': size,
                        'size_bytes': size,
                        'directory': directory,
                        'completed': True,
                    })
            
            return completed_torrents
        except Exception as e:
            return []

    def get_files(self, hash: str) -> list:
        """Get the list of files in a torrent.
        
        Args:
            hash: Torrent info hash
        
        Returns:
            List of file info dicts
        """
        self._ensure_client()
        try:
            rpc = self._rtorrent.client
            directory = rpc.d.directory(hash)
            if directory:
                return [{'path': directory}]
        except Exception:
            pass
        return []

    def delete(self, hash: str) -> bool:
        """Remove a torrent from rTorrent (and optionally data).
        
        Args:
            hash: Torrent info hash
            delete_files: Whether to delete downloaded files (via kwargs)
        
        Returns:
            True if successful
        """
        self._ensure_client()
        try:
            rpc = self._rtorrent.client
            rpc.d.erase(hash)
            return True
        except Exception:
            pass
        return False

    def test(self) -> tuple:
        """Test the connection to rTorrent.
        
        Returns:
            (success: bool, message: str)
        """
        self._ensure_client()
        try:
            rpc = self._rtorrent.client
            torrents = rpc.download_list() or []
            return (True, f"Connected. {len(torrents)} torrents in queue.")
        except Exception as e:
            return (False, f"Connection failed: {str(e)}")

    def find_item(self, hash: str):
        """Find a torrent by hash. For backward compatibility.
        
        Returns:
            Hash string if found, -1 if not found
        """
        self._ensure_client()
        try:
            rpc = self._rtorrent.client
            torrents = rpc.download_list() or []
            torrent_hashes = [t[0].upper() if isinstance(t, list) else t.upper() for t in torrents]
            if hash.upper() in torrent_hashes:
                return hash
        except Exception:
            pass
        return -1
    
    def get_active_downloads(self, limit: int = 20):
        """Get active (incomplete) downloads efficiently using multicall.
        
        Args:
            limit: Maximum number of downloads to return
        
        Returns:
            List of active download info dicts
        """
        self._ensure_client()
        try:
            rpc = self._rtorrent.client
            result = rpc.d.multicall2('', 'main',
                'd.hash=',
                'd.name=',
                'd.size_bytes=',
                'd.completed_bytes=',
                'd.complete=',
                'd.ratio=',
                'd.directory='
            )
            
            active = []
            for torrent_data in result:
                if not isinstance(torrent_data, list) or len(torrent_data) < 5:
                    continue
                
                h, name, size, completed, is_complete = torrent_data[:5]
                ratio = torrent_data[5] if len(torrent_data) > 5 else 0
                directory = torrent_data[6] if len(torrent_data) > 6 else ''
                
                if not is_complete:
                    pct = (completed / size * 100) if size > 0 else 0
                    active.append({
                        'hash': h,
                        'name': name,
                        'size': size,
                        'completed': completed,
                        'percent': pct,
                        'ratio': ratio / 1000.0 if ratio else 0,
                        'directory': directory,
                    })
                    
                    if len(active) >= limit:
                        break
            
            return active
        except Exception as e:
            return []

    def verify_completion(self, hash: str) -> tuple:
        """Verify that a torrent is complete and ready for post-processing.
        
        Returns:
            (success: bool, message: str)
        """
        self._ensure_client()
        
        if not self.client:
            return (False, "RTorrent client not initialized")
        
        try:
            torrent_info = self.find(hash)
            if not torrent_info:
                return (False, f"Torrent {hash} not found in RTorrent")
            
            if not torrent_info.get('completed', False):
                return (False, f"Torrent {hash} not complete on RTorrent")
            
            return (True, "Torrent verified complete")
        except Exception as e:
            return (False, f"Error verifying torrent: {str(e)}")

    def get_download_info(self, hash: str) -> dict:
        """Get information needed for file transfer post-processing.
        
        Returns:
            Dict with remote_dir, files_to_copy, is_single_file, name
        """
        self._ensure_client()
        
        torrent_info = self.find(hash)
        if not torrent_info:
            return {
                'remote_dir': '',
                'files_to_copy': None,
                'is_single_file': False,
                'name': '',
            }
        
        directory = torrent_info.get('directory', '')
        name = torrent_info.get('name', '')
        
        is_single_file = name and ('.' in name.split('/')[-1])
        
        # For single-file torrents, specify which file to copy
        files_to_copy = None
        if is_single_file:
            # Extract just the filename if there's a path component
            filename = name.split('/')[-1] if '/' in name else name
            files_to_copy = [filename]
        
        return {
            'remote_dir': directory,
            'files_to_copy': files_to_copy,
            'is_single_file': is_single_file,
            'name': name,
        }

    def cleanup(self, file_transfer) -> tuple:
        """Cleanup not implemented for RTorrent downloader."""
        return (True, "Cleanup not implemented for RTorrent")


def RTorrent(downloader=None):
    """Compatibility wrapper for original RTorrent class name."""
    return RTorrentDownloader(downloader)
