from abc import ABC, abstractmethod


class BaseDownloader(ABC):
    optionfields = {}

    def __init__(self, downloader=None):
        self.downloader = downloader
        self.reload = False
        self.options = {}
        
        if downloader:
            # Only call checkoptions if the downloader already has a client attribute
            # (i.e., it's not being created from_db)
            if hasattr(downloader, 'checkoptions'):
                try:
                    downloader.checkoptions()
                except AttributeError:
                    pass  # Client not yet set, will be set later
            if hasattr(downloader, 'options'):
                self.options = downloader.options

    @abstractmethod
    def add(self, file_path: str, **kwargs) -> str:
        """Add download, return hash/id"""
        pass

    @abstractmethod
    def find(self, hash: str):
        """Find torrent by hash, return info dict or None"""
        pass

    @abstractmethod
    def get_status(self, hash: str) -> dict:
        """Get status (downloading, completed, etc.)"""
        pass

    @abstractmethod
    def get_files(self, hash: str) -> list:
        """Get file list for post-processing"""
        pass

    @abstractmethod
    def delete(self, hash: str) -> bool:
        """Remove from client"""
        pass

    @abstractmethod
    def test(self) -> tuple:
        """Connection test: (success: bool, message: str)"""
        pass

    def get_completed(self) -> list:
        """Get all completed downloads.
        
        Returns:
            List of dicts with 'hash', 'name', and completion info for each completed download
        """
        return []

    def verify_completion(self, hash: str) -> tuple:
        """Verify that a download is complete and ready for post-processing.
        
        Returns:
            (success: bool, message: str)
            - If success is True, download is complete
            - If success is False, message explains why (not found, incomplete, etc.)
        """
        return (False, "Verification not implemented for this downloader type")

    def get_download_info(self, hash: str) -> dict:
        """Get information needed for file transfer post-processing.
        
        Returns:
            Dict with:
            - remote_dir: The directory containing the downloaded files
            - files_to_copy: List of specific files to transfer (or None for all files)
            - is_single_file: Whether this is a single-file download
            - name: Download name (for logging)
        """
        return {
            'remote_dir': '',
            'files_to_copy': None,
            'is_single_file': False,
            'name': '',
        }

    def cleanup(self, file_transfer) -> tuple:
        """Clean up downloaded files from seedbox after successful post-processing.
        
        Args:
            file_transfer: FileTransfer object containing remote_path
            
        Returns:
            (success: bool, message: str)
            - If success is True, cleanup completed (or was skipped if disabled)
            - If success is False, message explains the error (but doesn't fail post-processing)
        """
        return (True, "Cleanup not implemented for this downloader type")
