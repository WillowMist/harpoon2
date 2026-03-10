import requests
from itemqueue.models import Item, ItemHistory

class Arr(object):
    def __init__(self, manager):
        self.manager = manager
        self.url = manager.url
        self.apikey = manager.apikey
        self.label = manager.label
        self.headers = {'X-Api-Key': self.apikey, 'Accept': 'application/json'}
        # Default API path - can be overridden in subclasses
        self.apiurl = self.url + '/api/v3'

    def test(self):
        testurl = self.apiurl + '/system/status'
        try:
            r = requests.get(testurl, params=None, headers=self.headers)
            dt = r.json()
            return True, dt
        except Exception as e:
            return False, e

    def check_queue(self):
        url = self.apiurl + '/queue'
        try:
            r = requests.get(url, params=None, headers=self.headers)
            dt = self.parse_queue(r.json())
            return True, dt
        except Exception as e:
            return False, e

    def parse_queue(self, queue):
        records = []
        for record in queue:
            recordinfo = {}
            recordinfo['size'] = record['size']
            recordinfo['name'] = record['title']
            recordinfo['status'] = record['status']
            recordinfo['tdstate'] = record['trackedDownloadState'] if 'trackedDownloadState' in record.keys() else ''
            recordinfo['tdstatus'] = record['trackedDownloadStatus']
            recordinfo['statusmessages'] = record['statusMessages']
            recordinfo['downloadid'] = record['downloadId']
            recordinfo['clientid'] = record['id']
            recordinfo['manager'] = self.manager
            records.append(recordinfo)
        return records

    def check_itemqueue(self, record):
        queueitem, created = Item.objects.get_or_create(hash=record['downloadid'])
        if created:
            changed = {'hash': queueitem.hash}
        else:
            changed = {}
        for attr in ['size', 'name', 'status', 'clientid', 'manager']:
            if getattr(queueitem, attr) != record[attr]:
                changed[attr] = record[attr]
                setattr(queueitem, attr, record[attr])
        if changed:
            queueitem.save()
            for key in changed.keys():
                history = ItemHistory.objects.create(item=queueitem, details=f'{key} set to "{changed[key]}"')
        return




class Sonarr(Arr):
    def __init__(self, manager):
        super().__init__(manager)
        self.apiurl = self.url + '/api/v3'

    def test(self):
        testurl = self.apiurl + '/system/status'
        try:
            r = requests.get(testurl, params=None, headers=self.headers)
            dt = r.json()
            return True, dt
        except Exception as e:
            return False, e


class Radarr(Arr):
    def __init__(self, manager):
        super().__init__(manager)
        self.apiurl = self.url + '/api/v3'

    def test(self):
        testurl = self.apiurl + '/system/status'
        try:
            r = requests.get(testurl, params=None, headers=self.headers)
            dt = r.json()
            return True, dt
        except Exception as e:
            return False, e


class Lidarr(Arr):
    def __init__(self, manager):
        super().__init__(manager)
        self.apiurl = self.url + '/api/v1'

    def test(self):
        testurl = self.apiurl + '/system/status'
        try:
            r = requests.get(testurl, params=None, headers=self.headers)
            dt = r.json()
            return True, dt
        except Exception as e:
            return False, e

    def check_queue(self):
        url = self.apiurl + '/queue'
        try:
            r = requests.get(url, params=None, headers=self.headers)
            dt = self.parse_queue(r.json()['records'])
            return True, dt
        except Exception as e:
            return False, e


class Readarr(Arr):
    def __init__(self, manager):
        super().__init__(manager)
        self.apiurl = self.url + '/api/v1'

    def test(self):
        testurl = self.apiurl + '/system/status'
        try:
            r = requests.get(testurl, params=None, headers=self.headers)
            dt = r.json()
            return True, dt
        except Exception as e:
            return False, e

    def check_queue(self):
        url = self.apiurl + '/queue'
        try:
            r = requests.get(url, params=None, headers=self.headers)
            print(r.json())
            dt = self.parse_queue(r.json()['records'])
            return True, dt
        except Exception as e:
            return False, e


class Whisparr(Arr):
    def __init__(self, manager):
        super().__init__(manager)
        self.apiurl = self.url + '/api/v3'

    def test(self):
        testurl = self.apiurl + '/system/status'
        try:
            r = requests.get(testurl, params=None, headers=self.headers)
            dt = r.json()
            return True, dt
        except Exception as e:
            return False, e