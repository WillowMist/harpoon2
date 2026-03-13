from .base import BaseDownloader
import xmlrpc.client


class RTorrentXMLRPC:
    """Direct XML-RPC client for rTorrent using Python's built-in xmlrpc.client."""
    
    def __init__(self, address: str, timeout: float = 30.0):
        self.address = address
        self.timeout = timeout
        self._client = None
        
    def _get_client(self):
        if self._client is None:
            # Use Python's built-in xmlrpc.client with embedded credentials
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
        return self._get_client().load.raw(data)
    
    def load_start(self, file_path: str):
        """Load and start a torrent file."""
        with open(file_path, 'rb') as f:
            data = xmlrpc.client.Binary(f.read())
        return self._get_client().load.raw_start(data)
    
    def load_magnet(self, magnet_uri: str, info_hash: str):
        """Load a magnet link."""
        return self._get_client().load.magnet(magnet_uri, info_hash)


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
        super().__init__(downloader)
        if downloader and hasattr(downloader, 'client'):
            self._init_client()
        else:
            self.reload = True
            self.client = None

    def _init_client(self):
        opts = self.options
        protocol = 'https' if opts.get('use_ssl', False) else 'http'
        host = opts.get('host', '')
        port = opts.get('port', 443)
        url_path = opts.get('url_path', 'RPC2')
        username = opts.get('username', '')
        password = opts.get('password', '')

        if username and password:
            auth = f"{username}:{password}@"
        else:
            auth = ''

        address = f"{protocol}://{auth}{host}:{port}/{url_path}"
        self.client = RTorrentXMLRPC(address=address, timeout=30.0)
        self.reload = False
        self.start_on_load = opts.get('startonload', True)

    def _ensure_client(self):
        if self.reload or self.client is None:
            self._init_client()

    def add(self, file_path: str, **kwargs) -> str:
        """Add a torrent file or URL to rTorrent.
        
        Args:
            file_path: Local path to .torrent file or a magnet URL
            **kwargs: Optional parameters (label, etc.)
        
        Returns:
            Torrent hash (info_hash)
        """
        self._ensure_client()
        
        # Determine if it's a magnet URL or file
        if file_path.startswith('magnet:'):
            # For magnets, we need to extract the info hash
            import re
            match = re.search(r'xt=urn:btih:([a-zA-Z0-9]+)', file_path)
            if match:
                info_hash = match.group(1).upper()
                if len(info_hash) == 32:
                    # Base32 hash needs conversion
                    import base64
                    b32_bytes = info_hash.encode()
                    b32_decoded = base64.b32decode(b32_bytes)
                    info_hash = b32_decoded.hex().upper()
                # Load magnet - use load.magnet method
                self.client.load.magnet(file_path, info_hash)
                if self.start_on_load:
                    self.client.d.start(info_hash)
                return info_hash
            raise ValueError("Invalid magnet link")
        else:
            # Local torrent file - use load_start to load and optionally start
            if self.start_on_load:
                self.client.load_start(file_path)
            else:
                self.client.load_torrent(file_path)
            
            # Get the torrent hash by reading the file
            import bencode
            with open(file_path, 'rb') as f:
                torrent_data = bencode.bdecode(f.read())
            info_hash = torrent_data['info'].hexdigest().upper()
            
            return info_hash

    def find(self, hash: str):
        """Find a torrent by its info hash.
        
        Args:
            hash: Torrent info hash (40 char hex string)
        
        Returns:
            Torrent info dict or None if not found
        """
        self._ensure_client()
        try:
            # Get list of all torrents - each is a list like ['hash']
            torrents = self.client.download_list() or []
            # Flatten to get actual hashes
            torrent_hashes = [t[0].upper() if isinstance(t, list) else t.upper() for t in torrents]
            
            if hash.upper() in torrent_hashes:
                # Get torrent details using the RTorrentXMLRPC methods
                name = self.client.d_name(hash)
                size_bytes = self.client.d_size_bytes(hash)
                completed_bytes = self.client.d_completed_bytes(hash)
                is_complete = self.client.d_is_complete(hash)
                ratio = self.client.d_ratio(hash)
                directory = self.client.d_directory(hash)
                
                return {
                    'name': name,
                    'info_hash': hash,
                    'completed': is_complete,
                    'size_bytes': size_bytes,
                    'bytes_downloaded': completed_bytes,
                    'ratio': ratio / 1000.0 if ratio else 0,  # Convert from permille
                    'directory': directory,
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
            # Use multicall2 to get all torrents with their details in one call
            # This is much more efficient than individual calls
            result = self.client._get_client().d.multicall2('', 'main',
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
                
                # Only include completed torrents
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
            # Get directory for the torrent
            directory = self.client.d_directory(hash)
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
            self.client.d_erase(hash)
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
            torrents = self.client.download_list() or []
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
            torrents = self.client.download_list() or []
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
        return self.client.active_downloads(limit=limit)


def RTorrent(downloader=None):
    """Compatibility wrapper for original RTorrent class name."""
    return RTorrentDownloader(downloader)
