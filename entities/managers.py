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
    
    def reject_download(self, item, reason):
        """Notify the manager that a download failed and should be re-attempted.
        
        This sends a message to the *arr manager to mark the download as failed,
        allowing it to search for an alternate release.
        
        Args:
            item: Item object with hash and clientid
            reason: String explanation of why it failed (e.g., "RAR extraction failed: corrupted archive")
            
        Returns:
            (success: bool, message: str)
        """
        try:
            # Build rejection message to send to manager
            # The manager queue item has: id (clientid), title, downloadId (hash)
            url = self.apiurl + '/queue/bulk'
            
            payload = {
                'ids': [item.clientid],
                'blacklist': True  # Mark as blacklisted so *arr won't grab it again
            }
            
            response = requests.delete(url, json=payload, headers=self.headers, timeout=10)
            
            if response.status_code in [200, 204]:
                message = f"Notified manager to reject download: {reason}"
                ItemHistory.objects.create(item=item, details=message)
                return True, message
            else:
                message = f"Failed to notify manager (HTTP {response.status_code}): {reason}"
                ItemHistory.objects.create(item=item, details=message)
                return False, message
                
        except Exception as e:
            message = f"Error notifying manager about failed download: {str(e)}"
            ItemHistory.objects.create(item=item, details=message)
            return False, message
    
    def post_process(self, item, download_path):
        """Send a download completion notification to the manager for post-processing.
        
        This triggers the manager to import the downloaded files from the specified path.
        
        Args:
            item: Item object with hash, name, and clientid
            download_path: Local path where files have been downloaded/extracted to
            
        Returns:
            (success: bool, message: str)
        """
        try:
            url = self.apiurl + '/command'
            
            payload = {
                "name": "DownloadedEpisodesScan",
                "path": download_path,
                "downloadClientID": item.hash,
                "importMode": "Move"
            }
            
            response = requests.post(url, json=payload, headers=self.headers, timeout=30)
            
            if response.status_code in [200, 201]:
                message = f"Post-processing initiated: {download_path}"
                ItemHistory.objects.create(item=item, details=message)
                return True, message
            else:
                message = f"Post-processing failed (HTTP {response.status_code}): {response.text}"
                ItemHistory.objects.create(item=item, details=message)
                return False, message
                
        except Exception as e:
            message = f"Error initiating post-processing: {str(e)}"
            ItemHistory.objects.create(item=item, details=message)
            return False, message


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