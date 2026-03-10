import requests
from itemqueue.models import Item, ItemHistory
from django.db import models
from rtorrent import RTorrent as rtorclient

class SABNzbd(object):
    def __init__(self, downloader=None):
        self.downloader = downloader
        self.optionfields = {'url': 'string',
                             'apikey': 'string',
                             'cleanup': 'boolean',
                             'enabled': 'boolean',
                             }

class RTorrent(object):
    def __init__(self, downloader=None):
        self.downloader = downloader
        self.optionfields = {'host': 'string',
                             'port': 'int',
                             'url_path': 'string',
                             'use_ssl': 'boolean',
                             'username': 'string',
                             'password': 'string',
                             'startonload': 'boolean',
                             }
        if downloader and hasattr(self.downloader, 'client'):
            self.downloader.checkoptions()
            self.options = self.downloader.options
            self.method = 'https' if self.options['use_ssl'] else 'http'
            self.url = self.method + '://' + self.options['username'] + ':' + self.options['password'] + "@" + self.options['host'] + ':' + str(self.options['port']) + '/' + self.options['url_path']
            self.reload = False
            self.rtclient = rtorclient(self.url)
        else:
            self.reload = True
            self.url = ''

    def test(self):
        if self.reload:
            self.__init__(self.downloader)
        return self.rtclient.get_torrents()

    def find_item(self, hash):
        if self.reload:
            self.__init__(self.downloader)
        item = self.rtclient.find_torrent(hash)
        if item == -1:
            return None
        return item
