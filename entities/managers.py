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
            response_data = r.json()
            # Handle both list and dict responses (dict has 'records' key)
            if isinstance(response_data, dict) and 'records' in response_data:
                queue_data = response_data['records']
            else:
                queue_data = response_data
            dt = self.parse_queue(queue_data)
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
            recordinfo['downloadclient'] = record.get('downloadClient', '')  # Extract downloader client name
            recordinfo['manager'] = self.manager
            records.append(recordinfo)
        return records

    def check_itemqueue(self, record):
        import logging
        logger = logging.getLogger(__name__)
        
        queueitem, created = Item.objects.get_or_create(hash=record['downloadid'])
        if created:
            changed = {'hash': queueitem.hash}
        else:
            changed = {}
        # Preserve archived status - don't overwrite it during updates
        original_archived = queueitem.archived
        original_archived_at = queueitem.archived_at
        
        for attr in ['size', 'name', 'status', 'clientid', 'manager']:
            if getattr(queueitem, attr) != record[attr]:
                changed[attr] = record[attr]
                setattr(queueitem, attr, record[attr])
        
        # Try to assign downloader if not already assigned and downloadclient is provided
        if not queueitem.downloader and record.get('downloadclient'):
            from entities.models import Downloader
            try:
                # Map download client names to downloader types
                client_name = record['downloadclient']
                # Find matching downloader by name or type
                downloader = Downloader.objects.filter(name__iexact=client_name).first()
                if not downloader:
                    # Try matching by type (e.g., 'SABnzbd' -> 'SABNzbd')
                    downloader = Downloader.objects.filter(downloadertype__iexact=client_name).first()
                
                if downloader:
                    queueitem.downloader = downloader
                    changed['downloader'] = downloader.name
                    logger.debug(f"Assigned downloader '{downloader.name}' to item {record['name']}")
                else:
                    logger.warning(f"Could not find downloader matching '{client_name}' for item {record['name']}")
            except Exception as e:
                logger.error(f"Error assigning downloader for item {record['name']}: {e}")
        
        if changed:
            queueitem.save()
            # Re-apply archived status if it was changed
            if queueitem.archived != original_archived or queueitem.archived_at != original_archived_at:
                queueitem.archived = original_archived
                queueitem.archived_at = original_archived_at
                queueitem.save(update_fields=['archived', 'archived_at'])
            for key in changed.keys():
                if key != 'downloader':  # Don't log downloader assignment as a generic change
                    history = ItemHistory.objects.create(item=queueitem, details=f'{key} set to "{changed[key]}"')
                else:
                    history = ItemHistory.objects.create(item=queueitem, details=f'Downloader assigned: {changed[key]}')
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
                 "downloadClientID": str(item.clientid),
                "importMode": "Move"
            }
            
            response = requests.post(url, json=payload, headers=self.headers, timeout=30)
            
            response_json = {}
            try:
                response_json = response.json()
            except:
                pass
            
            history_details = f"Command request: {payload['name']}, path: {download_path}"
            if response.status_code in [200, 201]:
                history_details += f" | Response: id={response_json.get('id')}, name={response_json.get('name')}, status={response_json.get('status')}"
                message = f"Post-processing initiated: {download_path}"
                ItemHistory.objects.create(item=item, details=history_details)
                return True, message
            else:
                history_details += f" | Response failed: HTTP {response.status_code}, body: {response.text[:500]}"
                message = f"Post-processing failed (HTTP {response.status_code}): {response.text}"
                ItemHistory.objects.create(item=item, details=history_details)
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
    
    def post_process(self, item, download_path):
        """Send DownloadedEpisodesScan command for TV shows."""
        try:
            import logging
            logger = logging.getLogger(__name__)
            
            url = self.apiurl + '/command'
            payload = {
                "name": "DownloadedEpisodesScan",
                "path": download_path,
                 "downloadClientID": str(item.clientid),
                "importMode": "Move"
            }
            logger.info(f"[Sonarr post_process] Sending command to {url}")
            logger.info(f"[Sonarr post_process] Payload: {payload}")
            
            response = requests.post(url, json=payload, headers=self.headers, timeout=30)
            logger.info(f"[Sonarr post_process] Response status: {response.status_code}")
            logger.debug(f"[Sonarr post_process] Response body: {response.text}")
            
            response_json = {}
            try:
                response_json = response.json()
            except:
                pass
            
            history_details = f"Command request: {payload['name']}, path: {download_path}"
            if response.status_code in [200, 201]:
                history_details += f" | Response: id={response_json.get('id')}, name={response_json.get('name')}, status={response_json.get('status')}"
                message = f"Post-processing initiated: {download_path}"
                ItemHistory.objects.create(item=item, details=history_details)
                return True, message
            else:
                history_details += f" | Response failed: HTTP {response.status_code}, body: {response.text[:500]}"
                message = f"Post-processing failed (HTTP {response.status_code}): {response.text}"
                ItemHistory.objects.create(item=item, details=history_details)
                return False, message
        except Exception as e:
            message = f"Error initiating post-processing: {str(e)}"
            ItemHistory.objects.create(item=item, details=message)
            return False, message


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

    def post_process(self, item, download_path):
        """Send DownloadedMoviesScan command for movies."""
        try:
            url = self.apiurl + '/command'
            payload = {
                "name": "DownloadedMoviesScan",
                "path": download_path,
                 "downloadClientID": str(item.clientid),
                "importMode": "Move"
            }
            response = requests.post(url, json=payload, headers=self.headers, timeout=30)
            
            response_json = {}
            try:
                response_json = response.json()
            except:
                pass
            
            history_details = f"Command request: {payload['name']}, path: {download_path}"
            if response.status_code in [200, 201]:
                history_details += f" | Response: id={response_json.get('id')}, name={response_json.get('name')}, status={response_json.get('status')}"
                message = f"Post-processing initiated: {download_path}"
                ItemHistory.objects.create(item=item, details=history_details)
                return True, message
            else:
                history_details += f" | Response failed: HTTP {response.status_code}, body: {response.text[:500]}"
                message = f"Post-processing failed (HTTP {response.status_code}): {response.text}"
                ItemHistory.objects.create(item=item, details=history_details)
                return False, message
        except Exception as e:
            message = f"Error initiating post-processing: {str(e)}"
            ItemHistory.objects.create(item=item, details=message)
            return False, message



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

    def post_process(self, item, download_path):
        """Send DownloadedAlbumsScan command for music."""
        try:
            url = self.apiurl + '/command'
            payload = {
                "name": "DownloadedAlbumsScan",
                "path": download_path,
                 "downloadClientID": str(item.clientid),
                "importMode": "Move"
            }
            response = requests.post(url, json=payload, headers=self.headers, timeout=30)
            
            response_json = {}
            try:
                response_json = response.json()
            except:
                pass
            
            history_details = f"Command request: {payload['name']}, path: {download_path}"
            if response.status_code in [200, 201]:
                history_details += f" | Response: id={response_json.get('id')}, name={response_json.get('name')}, status={response_json.get('status')}"
                message = f"Post-processing initiated: {download_path}"
                ItemHistory.objects.create(item=item, details=history_details)
                return True, message
            else:
                history_details += f" | Response failed: HTTP {response.status_code}, body: {response.text[:500]}"
                message = f"Post-processing failed (HTTP {response.status_code}): {response.text}"
                ItemHistory.objects.create(item=item, details=history_details)
                return False, message
        except Exception as e:
            message = f"Error initiating post-processing: {str(e)}"
            ItemHistory.objects.create(item=item, details=message)
            return False, message


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

    def post_process(self, item, download_path):
        """Send DownloadedBooksScan command for books."""
        try:
            url = self.apiurl + '/command'
            payload = {
                "name": "DownloadedBooksScan",
                "path": download_path,
                 "downloadClientID": str(item.clientid),
                "importMode": "Move"
            }
            response = requests.post(url, json=payload, headers=self.headers, timeout=30)
            
            response_json = {}
            try:
                response_json = response.json()
            except:
                pass
            
            history_details = f"Command request: {payload['name']}, path: {download_path}"
            if response.status_code in [200, 201]:
                history_details += f" | Response: id={response_json.get('id')}, name={response_json.get('name')}, status={response_json.get('status')}"
                message = f"Post-processing initiated: {download_path}"
                ItemHistory.objects.create(item=item, details=history_details)
                return True, message
            else:
                history_details += f" | Response failed: HTTP {response.status_code}, body: {response.text[:500]}"
                message = f"Post-processing failed (HTTP {response.status_code}): {response.text}"
                ItemHistory.objects.create(item=item, details=history_details)
                return False, message
        except Exception as e:
            message = f"Error initiating post-processing: {str(e)}"
            ItemHistory.objects.create(item=item, details=message)
            return False, message



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

    def post_process(self, item, download_path):
        """Send DownloadedEpisodesScan command for adult content."""
        try:
            url = self.apiurl + '/command'
            payload = {
                "name": "DownloadedEpisodesScan",
                "path": download_path,
                 "downloadClientID": str(item.clientid),
                "importMode": "Move"
            }
            response = requests.post(url, json=payload, headers=self.headers, timeout=30)
            
            response_json = {}
            try:
                response_json = response.json()
            except:
                pass
            
            history_details = f"Command request: {payload['name']}, path: {download_path}"
            if response.status_code in [200, 201]:
                history_details += f" | Response: id={response_json.get('id')}, name={response_json.get('name')}, status={response_json.get('status')}"
                message = f"Post-processing initiated: {download_path}"
                ItemHistory.objects.create(item=item, details=history_details)
                return True, message
            else:
                history_details += f" | Response failed: HTTP {response.status_code}, body: {response.text[:500]}"
                message = f"Post-processing failed (HTTP {response.status_code}): {response.text}"
                ItemHistory.objects.create(item=item, details=history_details)
                return False, message
        except Exception as e:
            message = f"Error initiating post-processing: {str(e)}"
            ItemHistory.objects.create(item=item, details=message)
            return False, message


class Mylar3:
    """Manager for Mylar3 - Comic Book downloader."""
    
    def __init__(self, manager):
        self.manager = manager
        self.url = manager.url
        self.apikey = manager.apikey
        self.label = manager.label
        self.name = manager.name
    
    def _api_url(self, command):
        """Build Mylar3 API URL."""
        http_root = getattr(self.manager, 'http_root', '')
        return f'{self.url}{http_root}/api?apikey={self.apikey}&cmd={command}'
    
    def test(self):
        """Test Mylar3 API connection."""
        try:
            import logging
            logger = logging.getLogger(__name__)
            url = self._api_url('getVersion')
            logger.info(f"[Mylar3 test] Testing connection to {url}")
            
            import requests
            r = requests.get(url)
            if r.status_code == 200:
                return True, r.json()
            else:
                return False, f"HTTP {r.status_code}"
        except Exception as e:
            return False, str(e)
    
    def get_history(self, limit=50):
        """Get download history from Mylar3.
        
        Returns:
            list of history records with: Status, DateAdded, Title, URL, FolderName, ComicID, Size
        """
        import logging
        logger = logging.getLogger(__name__)
        
        try:
            import requests
            url = self._api_url('getHistory')
            logger.info(f"[Mylar3 get_history] Fetching from {url}")
            
            r = requests.get(url)
            if r.status_code != 200:
                logger.warning(f"[Mylar3 get_history] HTTP {r.status_code}")
                return []
            
            data = r.json()
            history = data.get('history', [])
            logger.info(f"[Mylar3 get_history] Got {len(history)} history records")
            return history
        except Exception as e:
            logger.error(f"[Mylar3 get_history] Error: {e}")
            return []
    
    def get_wanted(self):
        """Get wanted (missing) issues from Mylar3.
        
        Returns:
            list of wanted issues
        """
        import logging
        logger = logging.getLogger(__name__)
        
        try:
            import requests
            url = self._api_url('getWanted')
            logger.info(f"[Mylar3 get_wanted] Fetching from {url}")
            
            r = requests.get(url)
            if r.status_code != 200:
                logger.warning(f"[Mylar3 get_wanted] HTTP {r.status_code}")
                return []
            
            data = r.json()
            wanted = data.get('wanted', [])
            logger.info(f"[Mylar3 get_wanted] Got {len(wanted)} wanted issues")
            return wanted
        except Exception as e:
            logger.error(f"[Mylar3 get_wanted] Error: {e}")
            return []
    
    def find_comic(self, name):
        """Search for comics by name.
        
        Args:
            name: Search query string
            
        Returns:
            list of matching comics
        """
        import logging
        logger = logging.getLogger(__name__)
        
        try:
            import requests
            url = self._api_url(f'findComic&name={requests.utils.quote(name)}')
            logger.info(f"[Mylar3 find_comic] Searching for: {name}")
            
            r = requests.get(url)
            if r.status_code != 200:
                logger.warning(f"[Mylar3 find_comic] HTTP {r.status_code}")
                return []
            
            data = r.json()
            results = data.get('results', data) if isinstance(data, dict) else data
            logger.info(f"[Mylar3 find_comic] Found {len(results)} results")
            return results
        except Exception as e:
            logger.error(f"[Mylar3 find_comic] Error: {e}")
            return []
    
    def get_index(self):
        """Get all series in watchlist.
        
        Returns:
            list of series in the watchlist
        """
        import logging
        logger = logging.getLogger(__name__)
        
        try:
            import requests
            url = self._api_url('getIndex')
            logger.info(f"[Mylar3 get_index] Fetching watchlist from {url}")
            
            r = requests.get(url)
            if r.status_code != 200:
                logger.warning(f"[Mylar3 get_index] HTTP {r.status_code}")
                return []
            
            data = r.json()
            index = data.get('index', data) if isinstance(data, dict) else data
            logger.info(f"[Mylar3 get_index] Got {len(index)} series")
            return index
        except Exception as e:
            logger.error(f"[Mylar3 get_index] Error: {e}")
            return []
    
    def poll(self):
        """Poll Mylar3 logs for newly grabbed comics.
        
        Returns:
            None (results are saved to database)
        """
        import logging
        import requests
        import hashlib
        from django.core.cache import cache
        
        logger = logging.getLogger(__name__)
        
        try:
            # Get logs
            api_url = self.url.rstrip('/') + '/api' if not self.url.endswith('/api') else self.url
            params = {'apikey': self.apikey, 'cmd': 'getLogs'}
            response = requests.get(api_url, params=params, timeout=10)
            logs = response.json()
            
            # Track the last processed log timestamp to avoid re-processing
            # Logs are returned newest-first, so we process from the beginning
            cache_key = f'mylar3_{self.manager.id}_last_log_time'
            last_log_time = cache.get(cache_key, '2000-01-01 00:00:00')
            
            # Look for download initiation logs from any downloader
            # Logs are ordered newest-first, so we track the newest one we've seen
            newest_log_time = last_log_time
            
            for entry in logs:
                timestamp, message, level, category = entry
                
                # Stop processing when we reach entries we've already seen
                if timestamp <= last_log_time:
                    break
                
                # Track the newest log we're processing
                if timestamp > newest_log_time:
                    newest_log_time = timestamp
                
                msg_lower = message.lower()
                
                # Look for "Attempting to download" logs which indicate a grab
                # This is the reliable message that contains the comic name
                # "Download initiated" is just a status message without comic name
                if 'attempting to download' in msg_lower:
                    # Extract comic name from message
                    # Format: "[AIRDCPP] Attempting to download COMIC_NAME with TTH: ..."
                    
                    comic_name = None
                    
                    # Split on " with " to get the part before TTH/other details
                    parts = message.split(' with ')
                    if len(parts) > 0:
                        comic_name = parts[0]
                        # Remove downloader prefix
                        for prefix in ['[AIRDCPP]', '[RTORRENT]', '[QBITTORRENT]', '[SABNZBD]', '[airdcpp]', '[rtorrent]', '[qbittorrent]', '[sabnzbd]']:
                            if prefix in comic_name:
                                comic_name = comic_name.replace(prefix, '').strip()
                        # Remove the method name
                        comic_name = comic_name.replace('Attempting to download ', '').strip()
                    
                    if comic_name:
                        # Detect downloader from log prefix
                        downloader = None
                        msg_upper = message.upper()
                        from entities.models import Downloader as DlModel
                        if '[SABNZBD]' in msg_upper:
                            downloader = DlModel.objects.filter(downloadertype='SABNzbd').first()
                        elif '[AIRDCPP]' in msg_upper:
                            downloader = DlModel.objects.filter(downloadertype='AirDC++').first()
                        elif '[RTORRENT]' in msg_upper:
                            downloader = DlModel.objects.filter(downloadertype='RTorrent').first()
                        elif '[QBITTORRENT]' in msg_upper:
                            downloader = DlModel.objects.filter(downloadertype='QBittorrent').first()
                        
                        # Create hash from the comic name
                        hash_value = hashlib.md5(comic_name.encode()).hexdigest()
                        
                        # Check if item already exists
                        try:
                            item = Item.objects.get(hash__iexact=hash_value)
                            # If item exists but has no manager, assign Mylar3 as the manager
                            if not item.manager:
                                item.manager = self.manager
                                item.save()
                                logger.info(f"[Mylar3] Assigned manager to existing item: {comic_name}")
                            # Assign downloader if not set and we detected one
                            if not item.downloader and downloader:
                                item.downloader = downloader
                                item.save()
                                logger.info(f"[Mylar3] Assigned downloader {downloader.name} to existing item: {comic_name}")
                            else:
                                logger.debug(f"[Mylar3] Item already has manager: {comic_name}")
                        except Item.DoesNotExist:
                            # Create new item
                            item = Item.objects.create(
                                hash=hash_value,
                                name=comic_name,
                                size=0,
                                status='Grabbed',
                                manager=self.manager,
                                downloader=downloader,
                            )
                            ItemHistory.objects.create(
                                item=item,
                                details=f'Grabbed by {self.manager.name} via Mylar3' + (f' with {downloader.name}' if downloader else '')
                            )
                            logger.info(f"[Mylar3] New grabbed item: {comic_name} ({hash_value})" + (f' -> {downloader.name}' if downloader else ''))
            
            # Cache the newest log time for next poll
            cache.set(cache_key, newest_log_time, timeout=3600)  # Cache for 1 hour
        
        except Exception as e:
            logger.error(f"[Mylar3] Error polling {self.name}: {e}", exc_info=True)
    
    def post_process(self, item, download_path):
        """Trigger post-processing in Mylar3 for a downloaded comic.
        
        Args:
            item: Item object
            download_path: Path where comic was downloaded (file or directory)
            
        Returns:
            (success: bool, message: str)
        """
        import logging
        import os
        import re
        import requests
        logger = logging.getLogger(__name__)
        
        try:
            logger.info(f"[Mylar3 post_process] Triggering post-process for {download_path}")
            
            # Extract comic name and issue number from item name
            # Format: "Comic Name #009 (2019) (Digital) (Publisher).cbr"
            filename = os.path.basename(download_path)
            folder = os.path.dirname(download_path)
            
            # If download_path is a directory (no extension), use item.name instead
            if not os.path.splitext(filename)[1]:
                logger.info(f"[Mylar3 post_process] download_path is a directory, using item.name: {item.name}")
                filename = item.name
                folder = download_path
            
            # Keep filename with extension for forceProcess
            filename_for_api = filename
            
            # Remove file extension for parsing comic name
            name_without_ext = filename
            for ext in ['.cbr', '.cbz', '.pdf']:
                if name_without_ext.lower().endswith(ext):
                    name_without_ext = name_without_ext[:-len(ext)]
                    break
            
            # Try to find comic name and issue number
            # Pattern: "Comic Name #009 (2019)" or "Comic Name 009"
            match = re.search(r'^(.+?)\s*#?(\d+)', name_without_ext)
            if match:
                comic_search_name = match.group(1).strip()
                issue_number = match.group(2).strip()
            else:
                comic_search_name = name_without_ext
                issue_number = None
            
            # Remove year and other extras from comic name for API search
            comic_search_name = re.sub(r'\s*\(\d{4}\).*$', '', comic_search_name).strip()
            
            logger.info(f"[Mylar3 post_process] Searching for: '{comic_search_name}' issue {issue_number}")
            
            # Fetch comic ID from Mylar3
            comicid = None
            issueid = None
            
            try:
                # Find comic by name
                find_params = {
                    'apikey': self.apikey,
                    'cmd': 'findComic',
                    'name': comic_search_name
                }
                r = requests.get(f'{self.url}/api', params=find_params, timeout=30)
                result = r.json()
                
                if not isinstance(result, list) or len(result) == 0:
                    logger.warning(f"[Mylar3 post_process] No comics found for: {comic_search_name}")
                else:
                    year_match = re.search(r'\((\d{4})\)', name_without_ext)
                    filename_year = int(year_match.group(1)) if year_match else None
                    
                    def get_comic_with_issue(comics, issue_num):
                        """Try each comic until we find one that has the issue."""
                        for comic in comics:
                            cid = comic.get('comicid')
                            if not cid:
                                continue
                            
                            issues_params = {
                                'apikey': self.apikey,
                                'cmd': 'getComic',
                                'id': cid
                            }
                            r = requests.get(f'{self.url}/api', params=issues_params, timeout=30)
                            issues_result = r.json()
                            data = issues_result.get('data', {})
                            issues = data.get('issues', [])
                            
                            for issue in issues:
                                issue_num_str = str(issue.get('number', ''))
                                issue_num_stripped = issue_num_str.lstrip('0')
                                issue_num_compare = str(issue_num).lstrip('0') if issue_num else None
                                
                                if issue_num and issue_num_compare == issue_num_stripped:
                                    logger.info(f"[Mylar3 post_process] Found issue {issue_num} in comicid {cid}")
                                    return cid, issue.get('id')
                            
                            logger.info(f"[Mylar3 post_process] Issue {issue_num} not found in comicid {cid}, trying next")
                        
                        return None, None
                    
                    if filename_year:
                        logger.info(f"[Mylar3 post_process] Matching by year: {filename_year}")
                        
                        scored_comics = []
                        for comic in result:
                            try:
                                start_year = int(comic.get('comicyear', 0))
                                issues_count = int(comic.get('issues', 0))
                            except (ValueError, TypeError):
                                continue
                            
                            if start_year <= 0:
                                continue
                            
                            estimated_end_year = start_year + int(issues_count / 12)
                            
                            in_range = start_year <= filename_year <= estimated_end_year
                            logger.info(f"[Mylar3 post_process] Comic {comic.get('name')}: start={start_year}, end={estimated_end_year}, in_range={in_range}")
                            
                            scored_comics.append({
                                'comic': comic,
                                'start_year': start_year,
                                'estimated_end_year': estimated_end_year,
                                'issues_count': issues_count,
                                'in_range': in_range,
                            })
                        
                        scored_comics.sort(key=lambda x: (
                            x['in_range'],
                            x['issues_count'],
                            x['start_year']
                        ), reverse=True)
                        
                        comics_to_try = [c['comic'] for c in scored_comics]
                        logger.info(f"[Mylar3 post_process] Comics sorted by year match: {[c.get('name') for c in comics_to_try[:5]]}")
                    else:
                        logger.info(f"[Mylar3 post_process] No year in filename, using newest by start_year")
                        
                        sorted_comics = sorted(result, key=lambda x: int(x.get('comicyear', 0) or 0), reverse=True)
                        comics_to_try = sorted_comics
                        logger.info(f"[Mylar3 post_process] Comics sorted by newest: {[c.get('name') for c in comics_to_try[:5]]}")
                    
                    comicid, issueid = get_comic_with_issue(comics_to_try, issue_number)
                    
                    if comicid:
                        logger.info(f"[Mylar3 post_process] Selected comicid: {comicid}, issueid: {issueid}")
                    else:
                        logger.warning(f"[Mylar3 post_process] Could not find comic with matching issue")
                        
            except Exception as e:
                logger.warning(f"[Mylar3 post_process] Could not fetch comic/issue IDs: {e}")
            
            # Build forceProcess parameters
            params = {
                'apikey': self.apikey,
                'cmd': 'forceProcess',
                'nzb_name': filename_for_api,
                'nzb_folder': folder,
            }
            
            # Check issue status - skip if already processed
            if issueid:
                issue_info_params = {
                    'apikey': self.apikey,
                    'cmd': 'getIssueInfo',
                    'id': issueid
                }
                r_issue = requests.get(f'{self.url}/api', params=issue_info_params, timeout=30)
                issue_data = r_issue.json()
                issue_list = issue_data.get('data', [])
                if issue_list and len(issue_list) > 0:
                    issue_status = issue_list[0].get('status', '')
                    logger.info(f"[Mylar3 post_process] Issue {issueid} status: {issue_status}")
                    if issue_status in ['Downloaded', 'Post-Processed']:
                        message = f"Skipping post-processing: issue already {issue_status} (issueid={issueid})"
                        ItemHistory.objects.create(item=item, details=message)
                        return True, message
            
            # Add comicid and issueid if found
            if comicid:
                params['comicid'] = comicid
            if issueid:
                params['issueid'] = issueid
            
            logger.info(f"[Mylar3 post_process] Sending forceProcess: folder={folder}, name={name_without_ext}, comicid={comicid}, issueid={issueid}")
            logger.info(f"[Mylar3 post_process] Full URL: {self.url}/api?{('&').join([f'{k}={v}' for k, v in params.items()])}")
            r = requests.post(f'{self.url}/api', params=params)
            
            logger.info(f"[Mylar3 post_process] Response status: {r.status_code}")
            logger.info(f"[Mylar3 post_process] Response body: {r.text}")
            
            if r.status_code in [200, 201]:
                message = f"Post-processing initiated: {download_path} (comicid={comicid}, issueid={issueid})"
                ItemHistory.objects.create(item=item, details=message)
                return True, message
            else:
                message = f"Post-processing failed (HTTP {r.status_code}): {r.text}"
                ItemHistory.objects.create(item=item, details=message)
                return False, message
        except Exception as e:
            message = f"Error initiating post-processing: {str(e)}"
            ItemHistory.objects.create(item=item, details=message)
            return False, message


class Bindery:
    """Manager for Bindery - Book download manager."""

    # Bindery status -> Harpoon2 Item.status
    # Bindery status values seen on /api/v1/queue.status:
    #   downloading, downloading (in-flight), downloaded, importPending,
    #   importing, imported, failed, importFailed, importBlocked
    STATUS_MAP = {
        'downloading': 'Grabbed',
        'downloaded': 'PostProcessing',
        'importpending': 'PostProcessing',
        'importing': 'PostProcessing',
        'imported': 'Completed',
        'failed': 'Failed',
        'importfailed': 'Failed',
        'importblocked': 'Failed',
    }

    # Default transient-error substring. If Bindery's errorMessage contains
    # this text, the item is treated as PostProcessing (still retryable) instead
    # of Failed. The default is the phrase Bindery includes when the file
    # system hasn't caught up yet (qBittorrent writing to its temp/incomplete
    # directory, etc.). Override via manager.options['transient_error_substring'].
    DEFAULT_TRANSIENT_ERROR_SUBSTRING = 'the download may still be finishing'

    def __init__(self, manager):
        self.manager = manager
        self.url = manager.url
        self.apikey = manager.apikey
        self.label = manager.label
        self.name = manager.name
        self.apiurl = self.url.rstrip('/') + '/api/v1'
        self.headers = {'X-Api-Key': self.apikey, 'Accept': 'application/json'}

        # Per-manager configuration lives on the Manager model directly
        # (bindery_ebook_folder, bindery_audiobook_folder, etc.). Reading
        # from real fields means the form auto-populates on reopen and the
        # values are guaranteed to be persisted server-side.
        self.opts_ebook_folder = manager.bindery_ebook_folder or ''
        self.opts_audiobook_folder = manager.bindery_audiobook_folder or ''
        self.opts_ebook_category = manager.bindery_ebook_category or ''
        self.opts_audiobook_category = manager.bindery_audiobook_category or ''
        # path_remap is a comma-separated list of 'from:to' prefixes. We apply
        # the FIRST match per path. The remap is applied at the manual-import
        # API call so the path we send to Bindery matches its own namespace.
        self.opts_path_remap = self._parse_path_remap(manager.bindery_path_remap or '')
        self.opts_transient_error_substring = (
            manager.bindery_transient_error_substring
            or self.DEFAULT_TRANSIENT_ERROR_SUBSTRING
        )

    @staticmethod
    def _parse_path_remap(spec):
        """Parse a 'from:to,from2:to2' string into a list of (from, to) tuples.

        Whitespace around each segment is stripped. Empty entries are skipped.
        Returns an empty list if spec is empty/None.
        """
        if not spec:
            return []
        pairs = []
        for chunk in str(spec).split(','):
            chunk = chunk.strip()
            if not chunk or ':' not in chunk:
                continue
            src, dst = chunk.split(':', 1)
            src = src.strip()
            dst = dst.strip()
            if src and dst:
                pairs.append((src, dst))
        return pairs

    def apply_path_remap(self, path):
        """Apply the first matching prefix transform from path_remap config.

        Returns the path unchanged if no rule matches. Longest-prefix-first
        ordering prevents accidental partial matches.
        """
        if not path or not self.opts_path_remap:
            return path
        # Sort by source length descending so '/foo/bar' wins over '/foo'.
        for src, dst in sorted(self.opts_path_remap, key=lambda p: -len(p[0])):
            if path == src or path.startswith(src + '/'):
                return path.replace(src, dst, 1)
        return path

    def test(self):
        """Test Bindery API connection."""
        import logging
        import requests
        logger = logging.getLogger(__name__)

        url = self.apiurl + '/health'
        logger.info(f"[Bindery test] Testing connection to {url}")

        try:
            r = requests.get(url, headers=self.headers, timeout=10)
            if r.status_code == 200:
                return True, r.json()
            elif r.status_code == 401:
                return False, "API key invalid (401 Unauthorized)"
            else:
                return False, f"HTTP {r.status_code}"
        except Exception as e:
            return False, str(e)

    def check_queue(self):
        """Get Bindery queue and update Harpoon2 Items.

        Uses Bindery-native /api/v1/queue (richer than /api/queue).
        """
        import logging
        import requests
        from itemqueue.models import Item, ItemHistory
        logger = logging.getLogger(__name__)

        # Log effective config (helps verify the JSON options landed).
        logger.info(
            f"[Bindery] config: ebook_folder={self.opts_ebook_folder!r}, "
            f"audiobook_folder={self.opts_audiobook_folder!r}, "
            f"ebook_category={self.opts_ebook_category!r}, "
            f"audiobook_category={self.opts_audiobook_category!r}, "
            f"path_remap={self.opts_path_remap}, "
            f"transient_error_substring={self.opts_transient_error_substring!r}"
        )

        try:
            url = self.apiurl + '/queue'
            logger.info(f"[Bindery check_queue] Fetching from {url}")

            r = requests.get(url, headers=self.headers, params={'pageSize': 100}, timeout=15)
            if r.status_code == 401:
                logger.error(f"[Bindery check_queue] API key invalid")
                return False, "API key invalid"
            if r.status_code != 200:
                logger.warning(f"[Bindery check_queue] HTTP {r.status_code}")
                return False, f"HTTP {r.status_code}"

            data = r.json()
            items = data.get('items', data) if isinstance(data, dict) else data
            if not isinstance(items, list):
                logger.warning(f"[Bindery check_queue] Unexpected payload shape: {type(data)}")
                return False, "Unexpected payload"

            # Book-level outcome: if any queue row for a book is 'imported',
            # that book is safely in the library. A stale importFailed/importBlocked
            # row for the same book (e.g. one left over from a manual-import
            # recovery flow) must not drag the item back to Failed.
            imported_book_ids = {
                int(rec['bookId'])
                for rec in items
                if isinstance(rec, dict) and rec.get('status', '').lower() == 'imported' and rec.get('bookId')
            }

            updated = 0
            created = 0
            for record in items:
                item, was_created = self._update_item_from_queue(record, imported_book_ids)
                if was_created:
                    created += 1
                elif item:
                    updated += 1

            logger.info(f"[Bindery check_queue] Processed {len(items)} records (created={created}, updated={updated})")
            return True, {'items': len(items), 'created': created, 'updated': updated}
        except Exception as e:
            logger.error(f"[Bindery check_queue] Error: {e}")
            return False, str(e)

    def _update_item_from_queue(self, record, imported_book_ids=None):
        """Create or update an Item from a Bindery queue record.

        Returns (item, created).
        """
        import logging
        from itemqueue.models import Item, ItemHistory
        logger = logging.getLogger(__name__)

        # Bindery queue record (from /api/v1/queue):
        #   id, guid, bookId, title, size, sabnzbdNzoId | torrentId,
        #   status, protocol, errorMessage, book{...}
        download_id = record.get('sabnzbdNzoId') or record.get('torrentId')
        if not download_id:
            # Rows created by manual-import have no client download id
            # (sabnzbdNzoId/torrentId are null). They are tracked by the
            # hash-matched item from the original queue row; using record['id']
            # here would fabricate bogus item hashes.
            return None, False

        book_id = record.get('bookId', 0) or 0
        title = record.get('title', 'Unknown')
        size = record.get('size', 0) or 0
        status_raw = record.get('status', '')
        status_lower = status_raw.lower()
        error_message = record.get('errorMessage', '') or ''

        # Determine hp_status. If Bindery's errorMessage contains the configured
        # transient-error substring, the failure is retryable and we treat it
        # as PostProcessing so the transfer pipeline keeps re-attempting.
        hp_status = self.STATUS_MAP.get(status_lower, 'Grabbed')
        if hp_status == 'Failed' and self.opts_transient_error_substring:
            if self.opts_transient_error_substring in error_message:
                hp_status = 'PostProcessing'
                logger.info(
                    f"[Bindery] Treating transient failure as PostProcessing: "
                    f"{title} (status={status_raw}, errorMessage={error_message[:120]!r})"
                )

        # If the book has been successfully imported via another queue row,
        # a lingering importFailed/importBlocked row for the same book is stale
        # (e.g. the original failed row after a manual-import recovery). Do not
        # flip a healthy item back to Failed because of it.
        if hp_status == 'Failed' and imported_book_ids and book_id in imported_book_ids:
            logger.info(
                f"[Bindery] Book {book_id} already imported; treating stale "
                f"{status_raw} row as Completed ({title})"
            )
            hp_status = 'Completed'

        item, created = Item.objects.get_or_create(
            hash=str(download_id),
            defaults={
                'name': title,
                'size': size,
                'status': hp_status,
                'manager': self.manager,
                'clientid': book_id,  # store bookId here; reused by post_process
            },
        )

        if created:
            ItemHistory.objects.create(
                item=item,
                details=f'Created from {self.manager.name} queue (Bindery bookId={book_id})'
            )
            return item, True

        # Update existing Item
        changed = False
        for attr, value in (('name', title), ('size', size), ('status', hp_status), ('clientid', book_id)):
            if getattr(item, attr) != value:
                setattr(item, attr, value)
                changed = True
        if changed:
            item.save()
        return item, False

    def post_process(self, item, download_path):
        """Recover Bindery items that failed auto-import.

        For Bindery items, the standard transfer pipeline's `download_path`
        argument is the arr-style path and doesn't match real files on disk.
        We use the first completed FileTransfer's local_path to find the
        staged file/folder, optionally move it to the configured ebook or
        audiobook folder so Bindery sees it in the right library root, then
        call Bindery's manual-import API to import it.

        This is called when the transfer pipeline runs manager post_process,
        which happens after Harpoon2 finishes SFTP-grabbing the file via the
        unified transfer pipeline. It tolerates Bindery's queue state being
        'importFailed' or 'importBlocked' (which is what Bindery reports when
        its first attempt couldn't find the file - the path-remap or folder
        mount issue we hit during testing).

        Returns:
            (success: bool, message: str)
        """
        import logging
        import os
        import shutil
        import requests
        from itemqueue.models import FileTransfer, ItemHistory
        logger = logging.getLogger(__name__)

        book_id = item.clientid
        if not book_id:
            return False, f"No Bindery bookId on item {item.name}; cannot manual-import"

        # Resolve the actual local path. The transfer pipeline's `download_path`
        # is arr-style (base_remote_path + item.name) and may not match real
        # files on disk. Use the first completed FileTransfer's local_path,
        # which always points at the staged file(s).
        staged_path = None
        # Multi-file items (epub/mobi + cover art, etc.) must be staged as a
        # whole folder so Bindery sees all book files; a lone single-file
        # transfer points at that file directly. Picking just the first file
        # (e.g. cover.PNG) broke imports with "path is not a recognised book file".
        transfers = list(FileTransfer.objects.filter(item=item, status='completed').order_by('id'))
        if transfers and transfers[0].local_path:
            if len(transfers) == 1 and os.path.isfile(transfers[0].local_path):
                staged_path = transfers[0].local_path
            else:
                staged_path = os.path.dirname(transfers[0].local_path) or transfers[0].local_path

        if not staged_path or not os.path.exists(staged_path):
            return False, f"No staged file/folder found for item {item.name}"

        # Determine the format so we can move the file into the right Bindery
        # folder (ebook vs audiobook). If the configured folder is set, move
        # the staged file/folder there so Bindery's path-root resolution picks
        # the correct library when we feed it the remapped path.
        fmt = self._detect_format(staged_path)
        target_folder = self.opts_audiobook_folder if fmt == 'audiobook' else self.opts_ebook_folder
        if target_folder:
            # Compute the relative path inside the staging dir so the file
            # structure is preserved when moved to the target folder.
            staging_root = self.manager.folder.folder if self.manager.folder else None
            if staging_root and staged_path.startswith(staging_root.rstrip('/') + '/'):
                rel_path = os.path.relpath(staged_path, staging_root)
            else:
                rel_path = os.path.basename(staged_path)
            target_path = os.path.join(target_folder, rel_path)
            try:
                os.makedirs(os.path.dirname(target_path), exist_ok=True)
                if os.path.isdir(staged_path):
                    if os.path.exists(target_path):
                        shutil.rmtree(target_path)
                    shutil.move(staged_path, target_path)
                else:
                    shutil.move(staged_path, target_path)
                ItemHistory.objects.create(
                    item=item,
                    details=f"Move staged {fmt} to bindery folder: {staged_path} -> {target_path}"[:450],
                )
                logger.info(
                    f"[Bindery post_process] Moved {fmt} {staged_path} -> {target_path}"
                )
                staged_path = target_path
            except Exception as e:
                logger.warning(
                    f"[Bindery post_process] Could not move to {target_folder}: {e}. "
                    f"Falling back to original staged path."
                )

        # Apply the path remap so the path we send to Bindery matches its
        # namespace. Harpoon2's staging path is in Harpoon2's view of the
        # filesystem; Bindery's manual-import rejects paths outside its
        # configured library roots.
        remapped_path = self.apply_path_remap(staged_path)
        if remapped_path != staged_path:
            logger.info(
                f"[Bindery post_process] Remapped staged path "
                f"{staged_path} -> {remapped_path}"
            )

        # Find Bindery's queue row for this item. The /api/v1/queue response
        # gives us `id` (the Bindery row id), `bookId`, and either
        # `sabnzbdNzoId` or `torrentId` matching our Item.hash.
        bindery_record = self._queue_record_for_item(item.hash)

        if bindery_record is None:
            # No Bindery row at all (pruned/expired). Only create a manual-import
            # when the book isn't already being imported/in flight.
            if self._book_import_in_flight(book_id):
                return True, f"Bindery has an import in flight for {item.name}; nothing to do"
            return self._manual_import_new(item, book_id, staged_path)

        row_status = (bindery_record.get('status') or '').lower()
        if row_status == 'imported':
            # Bindery already imported this item - don't manufacture a
            # competing manual-import row (duplicate imports on retries).
            return True, f"Bindery already imported {item.name}; nothing to do"

        if row_status not in ('importfailed', 'importblocked'):
            # downloading/downloaded/importpending/importing/failed: Bindery's
            # own flow owns this row. Don't create a competing manual-import
            # row; there is nothing for us to recover.
            return True, f"Bindery queue status '{row_status}' needs no manual-import; nothing to do"

        # Recoverable failure. If Bindery already has another row for this book
        # in flight (an earlier manual-import still processing, or already
        # imported), don't pile on another one every time this recovery runs.
        if self._book_import_in_flight(book_id):
            # The original importFailed/importBlocked row is now stale - clear it
            # so it can't keep dragging the item back to Failed.
            self._delete_stale_original_row(bindery_record)
            return True, f"Bindery already has an import in flight for {item.name}; nothing to do"

        # Recoverable failure. Try manual-import/match first if the row is in
        # a recoverable state. This records that the user has manually told
        # Bindery which book the files belong to, but doesn't move the files
        # itself.
        bindery_id = bindery_record.get('id')
        if bindery_id:
            url = self.apiurl + '/queue/manual-import/match'
            payload = {'downloadId': bindery_id, 'bookId': book_id}
            # We use remapped_path in the history_details so the log shows what
            # path Bindery will see.
            history_details = (
                f"Bindery manual-import/match: downloadId={bindery_id}, "
                f"bookId={book_id}, path={remapped_path} | HTTP attempted"
            )
            try:
                resp = requests.post(url, json=payload, headers=self.headers, timeout=60)
                # 200/201 means the failed Bindery row is now matched to the book.
                # 409 means the state moved on (Bindery already retried and is
                # in a non-importFailed state). Both are non-fatal.
                if resp.status_code in (200, 201, 202):
                    logger.info(f"[Bindery post_process] manual-import/match OK: downloadId={bindery_id} -> bookId={book_id}")
                elif resp.status_code == 409:
                    logger.info(f"[Bindery post_process] manual-import/match 409 (already in non-importFailed state): {resp.text[:200]}")
                else:
                    logger.warning(f"[Bindery post_process] manual-import/match returned {resp.status_code}: {resp.text[:200]}")
            except Exception as e:
                logger.warning(f"[Bindery post_process] manual-import/match error (non-fatal): {e}")

        # Then run manual-import with the remapped path. This is the call that
        # actually moves the file from the staging path into the Bindery
        # library root. If the existing Bindery row was recoverable, Bindery
        # will re-import the new path against the book. If it wasn't, Bindery
        # creates a new download record and imports it.
        result = self._manual_import_new(item, book_id, staged_path)
        # Best-effort: clear the original importFailed/importBlocked row so it
        # can't keep flipping the item back to Failed. The manual-import row
        # we just created is a different record (new guid, no torrentId) and
        # Bindery will process it normally.
        if result and result[0]:
            self._delete_stale_original_row(bindery_record)
        return result

    def _queue_records(self):
        """Fetch and parse the Bindery /api/v1/queue response.

        Returns the list of queue records, or None on failure. Unlike
        _find_bindery_queue_id, records are returned regardless of status so
        callers can decide how to handle them (imported, in-flight, or
        recoverable failure).
        """
        import logging
        import requests
        logger = logging.getLogger(__name__)

        try:
            resp = requests.get(
                self.apiurl + '/queue',
                headers=self.headers,
                params={'pageSize': 100},
                timeout=15,
            )
        except Exception as e:
            logger.warning(f"[Bindery] Queue lookup failed: {e}")
            return None

        if resp.status_code != 200:
            return None

        data = resp.json()
        items = data.get('items', data) if isinstance(data, dict) else data
        if not isinstance(items, list):
            return None
        return items

    def _queue_record_for_item(self, item_hash):
        """Return the /api/v1/queue record matching the given hash, or None."""
        items = self._queue_records()
        if not items:
            return None
        for record in items:
            if (record.get('sabnzbdNzoId') == item_hash
                    or record.get('torrentId') == item_hash):
                return record
        return None

    def _book_import_in_flight(self, book_id):
        """Return True if any queue row for the book is progressing or done.

        Bindery creates a fresh row for each manual-import call. Once any row
        for this book is downloading/downloaded/importpending/importing/imported,
        another manual-import would only create a duplicate. Callers use this to
        avoid firing duplicate imports when the original row is still stuck in
        importFailed/importBlocked.
        """
        items = self._queue_records() or []
        active = {'downloading', 'downloaded', 'importpending', 'importing', 'imported'}
        return any(
            isinstance(r, dict)
            and int(r.get('bookId') or 0) == int(book_id or 0)
            and (r.get('status') or '').lower() in active
            for r in items
        )

    def _delete_stale_original_row(self, bindery_record):
        """Best-effort: drop a stale importFailed/importBlocked Bindery row.

        When Bindery's auto-import fails, its row stays stuck in
        importFailed/importBlocked and would otherwise keep dragging the
        Harpoon2 item back to Failed forever. Once our manual-import recovery
        succeeds (or the book is already imported via another row) the
        original row is stale; we delete it via
        DELETE /api/v1/queue/{id}?removeFromClient=false so the torrent keeps
        seeding in the download client but Bindery's bookkeeping is cleared.

        Safety: only deletes rows whose status is still in the recoverable-
        failure set. Anything in-flight or imported is left alone - if Bindery
        already advanced the row we don't touch it. Failure to delete is
        non-fatal: manual-import already succeeded.
        """
        if not isinstance(bindery_record, dict):
            return
        status = (bindery_record.get('status') or '').lower()
        if status not in ('importfailed', 'importblocked'):
            return
        bindery_id = bindery_record.get('id')
        if not bindery_id:
            return

        import logging
        import requests
        logger = logging.getLogger(__name__)
        url = f"{self.apiurl}/queue/{bindery_id}"
        try:
            resp = requests.delete(
                url,
                headers=self.headers,
                params={'removeFromClient': 'false'},
                timeout=60,
            )
            if resp.status_code in (200, 201, 202, 204):
                logger.info(
                    f"[Bindery post_process] Removed stale {status} row id={bindery_id} "
                    f"(removeFromClient=false)"
                )
            else:
                logger.warning(
                    f"[Bindery post_process] Could not remove stale {status} row id={bindery_id}: "
                    f"HTTP {resp.status_code} {resp.text[:200]}"
                )
        except Exception as e:
            logger.warning(
                f"[Bindery post_process] Failed to remove stale {status} row id={bindery_id}: {e}"
            )

    def _manual_import_new(self, item, book_id, staged_path):
        """Fallback path: create a new Bindery download via manual-import.

        Used when the existing Bindery row can't be matched (e.g. it's no
        longer in importFailed/importBlocked, or is paginated out). Creates
        a fresh row Bindery can import.
        """
        import logging
        import os
        import requests
        from itemqueue.models import ItemHistory
        logger = logging.getLogger(__name__)

        remapped_path = self.apply_path_remap(staged_path)
        fmt = self._detect_format(staged_path)
        url = self.apiurl + '/queue/manual-import'
        payload = {'path': remapped_path, 'bookId': book_id, 'format': fmt}

        try:
            resp = requests.post(url, json=payload, headers=self.headers, timeout=60)
        except Exception as e:
            msg = f"Bindery manual-import request error: {e}"
            logger.error(f"[Bindery post_process] {msg}")
            ItemHistory.objects.create(item=item, details=msg)
            return False, msg

        body_preview = (resp.text or '')[:200]
        history_details = (
            f"Bindery manual-import (new): path={remapped_path}, bookId={book_id}, "
            f"format={fmt} | HTTP {resp.status_code}"
        )
        if resp.status_code in (200, 201, 202):
            history_details += f" | Response: {body_preview}"
            # Keep it bounded for ItemHistory.details (CharField max_length=500).
            ItemHistory.objects.create(item=item, details=history_details[:450])
            return True, f"Bindery accepted manual-import for {remapped_path} (bookId={book_id})"
        else:
            history_details += f" | Failed: {body_preview}"
            ItemHistory.objects.create(item=item, details=history_details[:450])
            return False, f"Bindery manual-import failed (HTTP {resp.status_code}): {body_preview}"

    @staticmethod
    def _detect_format(path):
        """Return 'ebook' or 'audiobook' for the staged path."""
        import os
        audiobook_exts = ('.m4b', '.mp3', '.m4a', '.flac', '.ogg', '.aac')
        if os.path.isdir(path):
            # Directory: peek at contents
            try:
                for entry in os.listdir(path):
                    if entry.lower().endswith(audiobook_exts):
                        return 'audiobook'
            except Exception:
                pass
            return 'ebook'
        # Single file
        if path.lower().endswith(audiobook_exts):
            return 'audiobook'
        return 'ebook'


class Blackhole:
    """Manager that monitors a directory for .nzb and .torrent files."""
    
    def __init__(self, manager):
        self.manager = manager
        self.name = manager.name
        self.monitor_directory = manager.monitor_directory
        self.monitor_subdirectories = manager.monitor_subdirectories
        self.category = manager.category
        self.torrent_downloader = manager.torrent_downloader
        self.nzb_downloader = manager.nzb_downloader
        self.temp_folder = manager.temp_folder
        self.poll_interval = manager.poll_interval
        self.move_on_complete = manager.move_on_complete
        self.delete_source = manager.delete_source
        self.duplicate_handling = manager.duplicate_handling
        self.enabled = manager.enabled
        self.scan_on_startup = manager.scan_on_startup
    
    def test(self):
        """Test that the monitor directory exists and is accessible."""
        import os
        import logging
        logger = logging.getLogger(__name__)
        
        if not self.monitor_directory:
            return False, "Monitor directory not configured"
        
        if not os.path.exists(self.monitor_directory):
            return False, f"Monitor directory does not exist: {self.monitor_directory}"
        
        if not os.path.isdir(self.monitor_directory):
            return False, f"Monitor path is not a directory: {self.monitor_directory}"
        
        if not os.access(self.monitor_directory, os.R_OK | os.W_OK):
            return False, f"Monitor directory is not readable/writable: {self.monitor_directory}"
        
        return True, f"Monitor directory accessible: {self.monitor_directory}"
    
    def get_files_to_process(self):
        """Scan monitor directory for .nzb and .torrent files.
        
        Returns:
            dict with 'torrent' and 'nzb' keys containing lists of file paths
        """
        import os
        import logging
        logger = logging.getLogger(__name__)
        
        nzb_files = []
        torrent_files = []
        
        if not self.monitor_directory or not os.path.exists(self.monitor_directory):
            logger.warning(f"Monitor directory does not exist: {self.monitor_directory}")
            return {'nzb': [], 'torrent': []}
        
        if self.monitor_subdirectories:
            # Walk all subdirectories
            for root, dirs, files in os.walk(self.monitor_directory):
                for filename in files:
                    lower = filename.lower()
                    if lower.endswith('.nzb'):
                        nzb_files.append(os.path.join(root, filename))
                    elif lower.endswith('.torrent'):
                        torrent_files.append(os.path.join(root, filename))
        else:
            # Only monitor root directory
            for filename in os.listdir(self.monitor_directory):
                filepath = os.path.join(self.monitor_directory, filename)
                if os.path.isfile(filepath):
                    lower = filename.lower()
                    if lower.endswith('.nzb'):
                        nzb_files.append(filepath)
                    elif lower.endswith('.torrent'):
                        torrent_files.append(filepath)
        
        logger.debug(f"Found {len(nzb_files)} .nzb and {len(torrent_files)} .torrent files in {self.monitor_directory}")
        
        return {'nzb': nzb_files, 'torrent': torrent_files}
    
    def get_category_for_file(self, filepath):
        """Determine the category for a file based on settings.
        
        Returns:
            str: category name
        """
        import os
        
        if self.monitor_subdirectories:
            # Use subfolder name as category
            dirname = os.path.dirname(filepath)
            if dirname.startswith(self.monitor_directory):
                subdir = dirname[len(self.monitor_directory):].lstrip(os.sep)
                if os.sep in subdir:
                    # Get first subdirectory
                    category = subdir.split(os.sep)[0]
                else:
                    category = subdir
            else:
                category = self.category or 'default'
        else:
            category = self.category or 'default'
        
        return category
    
    def send_to_downloader(self, filepath, file_type):
        """Send a file to the appropriate downloader.
        
        Args:
            filepath: Full path to the .nzb or .torrent file
            file_type: 'nzb' or 'torrent'
            
        Returns:
            (success: bool, download_id: str or None, message: str)
        """
        import os
        import logging
        import hashlib
        from entities.models import Downloader
        
        logger = logging.getLogger(__name__)
        
        if file_type == 'nzb':
            downloader = self.nzb_downloader
        elif file_type == 'torrent':
            downloader = self.torrent_downloader
        else:
            return False, None, f"Unknown file type: {file_type}"
        
        if not downloader:
            return False, None, f"No {file_type} downloader configured"
        
        # Generate a unique hash for this download
        # Use file path + timestamp for uniqueness
        file_hash = hashlib.md5(f"{filepath}{os.path.getmtime(filepath)}".encode()).hexdigest()
        
        try:
            # Get the downloader client
            client = downloader.client
            logger.debug(f"Sending {filepath} to {downloader.name}")
            logger.debug(f"Downloader client: {client}")
            logger.debug(f"Downloader client.client: {client.client}")
            
            # Add the file to the downloader
            if file_type == 'nzb':
                category = self.get_category_for_file(filepath)
                nzo_id = client.add(filepath, category=category)
                return True, nzo_id, f"Added to {downloader.name}"
            elif file_type == 'torrent':
                # For torrent, use the add method
                category = self.get_category_for_file(filepath)
                logger.debug(f"Calling client.add for torrent with category: {category}")
                torrent_hash = client.add(filepath, label=category)
                logger.debug(f"Torrent hash returned: {torrent_hash}")
                if torrent_hash:
                    return True, torrent_hash, f"Added to {downloader.name}"
                return False, None, f"Failed to load torrent"
            
        except Exception as e:
            logger.error(f"Error sending {filepath} to {downloader.name}: {e}")
            return False, None, str(e)
        
        return False, None, "Unknown error"
    
    def should_skip_file(self, filename):
        """Check if file should be skipped based on duplicate handling settings.
        
        Args:
            filename: Name of the file to check
            
        Returns:
            bool: True if file should be skipped
        """
        import os
        
        if self.duplicate_handling == 'skip':
            # Check if we've already processed this file
            from itemqueue.models import Item
            # Use filename as part of hash to detect duplicates
            existing = Item.objects.filter(name__icontains=filename).first()
            return existing is not None
        elif self.duplicate_handling == 'rename':
            # TODO: Implement rename logic
            return False
        elif self.duplicate_handling == 'overwrite':
            return False
        
        return False
    
    def reject_download(self, item, reason):
        """Blackhole manager doesn't support rejecting downloads.
        
        For Blackhole, this logs a warning and creates a history entry
        alerting the user that manual intervention is needed.
        
        Args:
            item: Item object
            reason: String explanation of why it failed
            
        Returns:
            (success: bool, message: str)
        """
        import logging
        logger = logging.getLogger(__name__)
        
        logger.warning(f"Blackhole manager cannot reject downloads automatically. "
                      f"Item '{item.name}' requires manual intervention. Reason: {reason}")
        
        # Create a history entry to alert the user
        from itemqueue.models import ItemHistory
        ItemHistory.objects.create(
            item=item,
            details=f"MANUAL INTERVENTION REQUIRED: {reason}. Please check the download manually."
        )
        
        # Create a notification for the admin
        from users.models import Notification
        Notification.create_for_admin(
            f"Manual intervention required for '{item.name}': {reason}",
            notification_type='manual_intervention',
            item_hash=item.hash
        )
        
        return True, f"Manual intervention required: {reason}"


# Alias for backward compatibility
Mylar = Mylar3