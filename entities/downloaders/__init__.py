from .base import BaseDownloader
from .rtorrent import RTorrentDownloader, RTorrent
from .sabnzbd import SABnzbdDownloader, SABNzbd
from .airdcpp import AirDCppDownloader, AirDCpp

__all__ = [
    'BaseDownloader',
    'RTorrentDownloader',
    'RTorrent',
    'SABnzbdDownloader',
    'SABNzbd',
    'AirDCppDownloader',
    'AirDCpp',
]

# Mapping from display names (in settings) to Python attribute names
# This is needed because some display names contain characters invalid in Python identifiers
DOWNLOADER_NAME_MAP = {
    'RTorrent': 'RTorrent',
    'SABNzbd': 'SABNzbd',
    'AirDC++': 'AirDCpp',  # Map 'AirDC++' to 'AirDCpp' function
}
