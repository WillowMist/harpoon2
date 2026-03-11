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
