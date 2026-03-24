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
                            )
                            ItemHistory.objects.create(
                                item=item,
                                details=f'Grabbed by {self.manager.name} via Mylar3'
                            )
                            logger.info(f"[Mylar3] New grabbed item: {comic_name} ({hash_value})")
            
            # Cache the newest log time for next poll
            cache.set(cache_key, newest_log_time, timeout=3600)  # Cache for 1 hour
        
        except Exception as e:
            logger.error(f"[Mylar3] Error polling {self.name}: {e}", exc_info=True)
    
    def post_process(self, item, download_path):
        """Trigger post-processing in Mylar3 for a downloaded comic.
        
        Args:
            item: Item object
            download_path: Path where comic was downloaded
            
        Returns:
            (success: bool, message: str)
        """
        import logging
        logger = logging.getLogger(__name__)
        
        try:
            import requests
            url = f'{self.url}/api?apikey={self.apikey}&cmd=forceProcess'
            logger.info(f"[Mylar3 post_process] Triggering post-process for {download_path}")
            
            payload = {
                'nzb_name': item.name,
                'nzb_folder': download_path
            }
            
            r = requests.post(url, json=payload)
            
            if r.status_code in [200, 201]:
                message = f"Post-processing initiated: {download_path}"
                ItemHistory.objects.create(item=item, details=message)
                return True, message
            else:
                message = f"Post-processing failed (HTTP {r.status_code})"
                ItemHistory.objects.create(item=item, details=message)
                return False, message
        except Exception as e:
            message = f"Error initiating post-processing: {str(e)}"
            ItemHistory.objects.create(item=item, details=message)
            return False, message


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