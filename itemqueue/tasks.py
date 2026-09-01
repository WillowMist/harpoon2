from celery import shared_task
from django.conf import settings
from itemqueue.models import Item, ItemHistory, FileTransfer
from entities.models import Downloader
from users.models import Notification
from django.db import OperationalError, InterfaceError, DatabaseError, transaction
from django.db.models import Q
from django.utils import timezone
from datetime import timedelta
import logging
import os
import shutil
import threading
import paramiko
import django.utils.timezone
import subprocess
import glob
import re
from dplibs.filesystem import safe_rename
from dplibs.retry import _sftp_connect_with_retry

logger = logging.getLogger(__name__)


# Wall-clock timeout for a single sftp.get() call. Mirrors the stall-detection
# threshold in check_stalled_transfers (5 minutes) so the watchdog fires at the
# same point the operator would otherwise see the transfer marked stalled.
# Without this, sftp.get() can hang indefinitely when the seedbox stops sending
# data but keeps the SSH channel alive via keepalives — the Celery worker
# holds the slot until task_time_limit (1h).
SFTP_GET_TIMEOUT_SECONDS = 300

# Stall-detection threshold for check_stalled_transfers. Mirrors
# SFTP_GET_TIMEOUT_SECONDS so the watchdog and the recovery tick fire at the
# same wall-clock boundary (5 minutes).
STALL_THRESHOLD_SECONDS = 300

# PIPE-03: cooldown between recovery dispatches for the same Item. Tighter
# than the legacy 5-minute Block B cooldown; Item.last_recovery_at is the
# single source of truth for the gate.
RECOVERY_COOLDOWN_SECONDS = 60

# PIPE-01: hard cap on retry_postprocessing attempts. The 4th invocation is
# a no-op (marks the item Failed). Pin this constant; operators can raise it
# post-Phase-5 if the failure mode changes.
RETRY_CAP_ATTEMPTS = 3


class SFTPStallTimeout(Exception):
    """Raised when sftp.get() does not return within SFTP_GET_TIMEOUT_SECONDS.

    The caller is expected to handle this in its retry loop, clean up the
    partial local file, and reconnect to the seedbox.
    """
    pass


def _stoppable_progress_callback(inner_callback, stop_event):
    """Wrap a progress callback so it short-circuits once stop_event is set.

    Used by _sftp_get_with_timeout to prevent the (leaked) worker thread
    from continuing to write to the DB after we've decided the transfer
    has stalled and moved on.
    """
    def wrapped(bytes_so_far, bytes_total):
        if stop_event.is_set():
            return
        try:
            inner_callback(bytes_so_far, bytes_total)
        except Exception as e:
            logger.debug(f"Could not update transfer progress: {e}")
    return wrapped


def _sftp_get_with_timeout(sftp, remote, local, callback, timeout):
    """Run sftp.get() with a hard wall-clock timeout.

    If the call hangs longer than `timeout` seconds (typical: seedbox stalls
    mid-file but keeps the SSH channel alive via keepalives), set the stop
    event so the progress callback stops bumping DB rows, close the SFTP
    channel so the leaked worker thread can exit, and raise SFTPStallTimeout.

    The leaked thread is `daemon=True` and will be reaped by Celery's
    task_time_limit (1h) if sftp.close() races. The progress_callback's
    stop_event prevents the leaked thread from corrupting the FileTransfer
    row after we've decided the transfer failed.
    """
    stop_event = threading.Event()
    holder = {'exc': None}

    def _runner():
        try:
            sftp.get(remote, local, callback=_stoppable_progress_callback(callback, stop_event))
        except Exception as e:
            # Expected if we close the channel mid-call. Don't propagate —
            # the join/timeout check below decides whether we report success
            # or raise SFTPStallTimeout.
            holder['exc'] = e

    t = threading.Thread(target=_runner, daemon=True, name=f"sftp-get-{os.path.basename(remote)[:40]}")
    t.start()
    t.join(timeout=timeout)

    if t.is_alive():
        logger.error(
            f"SFTP get stalled after {timeout}s for {remote}; "
            f"closing channel and raising SFTPStallTimeout (worker thread will be reaped by task_time_limit)"
        )
        stop_event.set()  # tell progress callback to stop touching the DB
        try:
            sftp.close()
        except Exception as close_exc:
            logger.debug(f"sftp.close() after stall raised: {close_exc}")
        # Do NOT wait for the leaked thread — it's daemon=True and will be reaped
        # when the worker process exits. Waiting here just blocks the worker.
        raise SFTPStallTimeout(f"sftp.get() did not return within {timeout}s for {remote}")

    if holder['exc'] is not None:
        raise holder['exc']


def find_rar_archives(directory):
    """Find all RAR archive files in a directory.
    
    Returns a list of paths to RAR files (.rar, .r00, .r01, etc).
    For multi-part archives, this returns all parts found.
    """
    rar_files = []
    
    # Look for .rar files and .rXX files (multi-part)
    pattern_rar = os.path.join(directory, '*.rar')
    pattern_rxx = os.path.join(directory, '*.r[0-9][0-9]')
    
    rar_files.extend(glob.glob(pattern_rar))
    rar_files.extend(glob.glob(pattern_rxx))
    
    return sorted(rar_files)


def extract_rar_archive(rar_file_path, extract_to_dir):
    """Extract a RAR archive to the specified directory.
    
    Uses 'unrar' command to handle multi-part RAR archives.
    Validates that extraction actually produced files.
    
    Returns (success: bool, message: str)
    """
    try:
        # Count files in directory before extraction
        files_before = set(os.listdir(extract_to_dir))
        
        # Use unrar for extraction
        cmd = ['/usr/bin/unrar', 'x', '-y', rar_file_path, extract_to_dir]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        
        # Count files after extraction
        files_after = set(os.listdir(extract_to_dir))
        new_files = files_after - files_before
        
        # Check stderr for "No files to extract" error
        if 'No files to extract' in result.stderr:
            logger.error(f"RAR archive contains no extractable files")
            return False, "Archive corrupted or incomplete - no files extracted"
        
        if result.returncode in [0, 6]:  # 0=success, 6=warning but extraction occurred
            if new_files:
                logger.info(f"Successfully extracted {len(new_files)} file(s) from {os.path.basename(rar_file_path)}")
                return True, f"Extracted {len(new_files)} files with unrar"
            else:
                # unrar returned success but didn't extract anything
                logger.error(f"RAR extraction reported success but produced no files")
                return False, "Archive corrupted - extraction failed"
        
        logger.error(f"RAR extraction failed with return code {result.returncode}")
        logger.error(f"stderr: {result.stderr}")
        return False, f"Extraction failed: {result.stderr}"
        
    except FileNotFoundError as e:
        logger.error(f"unrar tool not found: {e}")
        return False, "unrar not installed"
    except subprocess.TimeoutExpired:
        logger.error(f"RAR extraction timed out for {rar_file_path}")
        return False, "Extraction timed out (>1 hour)"
    except Exception as e:
        logger.error(f"Error extracting RAR archive: {e}")
        return False, str(e)


def process_rar_archives(directory, item):
    """Process all RAR archives in a directory.
    
    Extracts archives and removes the archive files afterward.
    Updates item.extraction_status for dashboard display.
    
    Returns (success: bool, message: str)
    """
    rar_files = find_rar_archives(directory)
    
    if not rar_files:
        logger.debug(f"No RAR archives found in {directory}")
        return True, "No RAR archives found"
    
    logger.info(f"Found {len(rar_files)} RAR archive(s) in {directory}")
    
    # Update item status - mark as extracting
    item.extraction_status = 'extracting'
    item.extraction_started = django.utils.timezone.now()
    item.extraction_progress = 0
    item.save()
    ItemHistory.objects.create(item=item, details=f'Starting RAR extraction ({len(rar_files)} files)')
    
    # For multi-part RAR archives, we only need to extract the first .rar file
    # The extraction tool will handle all parts automatically
    first_rar = None
    for rar_file in rar_files:
        if rar_file.endswith('.rar'):
            first_rar = rar_file
            break
    
    if not first_rar:
        # If no .rar file found, it might be multi-part starting with .r00
        first_rar = rar_files[0]
    
    logger.info(f"Extracting RAR archive: {first_rar}")
    success, message = extract_rar_archive(first_rar, directory)
    
    if not success:
        error_msg = f"RAR extraction failed: {message}"
        logger.error(error_msg)
        item.extraction_status = 'failed'
        item.extraction_progress = 0
        item.status = 'Failed'  # Mark item as failed
        item.save()
        ItemHistory.objects.create(item=item, details=error_msg)
        Notification.create_for_admin(
            f"RAR extraction failed for '{item.name}': {message[:100]}",
            notification_type='rar_failure',
            item_hash=item.hash
        )
        
        # Notify manager to reject this download and search for alternative
        if item.manager:
            try:
                manager_client = item.manager.client
                reject_success, reject_msg = manager_client.reject_download(
                    item, 
                    f"RAR extraction failed - archive appears corrupted: {message}"
                )
                logger.info(f"Manager notification result: {reject_msg}")
                ItemHistory.objects.create(item=item, details=reject_msg)
            except Exception as e:
                logger.error(f"Failed to notify manager of extraction failure: {e}")
                ItemHistory.objects.create(item=item, details=f"Could not notify manager: {str(e)}")
        
        return False, error_msg
    
    # Update progress to indicate extraction is done, starting cleanup
    item.extraction_progress = 95
    item.save()
    
    # Remove all RAR archive files after successful extraction
    try:
        for idx, rar_file in enumerate(rar_files):
            os.remove(rar_file)
            logger.info(f"Removed RAR archive: {os.path.basename(rar_file)}")
            # Update progress during cleanup
            progress = 95 + int((idx + 1) / len(rar_files) * 5)
            item.extraction_progress = min(progress, 99)
            item.save()
        
        # Mark extraction as complete
        item.extraction_status = 'completed'
        item.extraction_progress = 100
        item.extraction_completed = django.utils.timezone.now()
        item.save()
        
        success_msg = f"Successfully extracted and removed {len(rar_files)} RAR file(s)"
        logger.info(success_msg)
        ItemHistory.objects.create(item=item, details=success_msg)
        return True, success_msg
        
    except Exception as e:
        error_msg = f"Failed to remove RAR archive files: {str(e)}"
        logger.error(error_msg)
        item.extraction_status = 'completed'  # Extraction succeeded, cleanup failed
        item.extraction_progress = 100
        item.extraction_completed = django.utils.timezone.now()
        item.save()
        ItemHistory.objects.create(item=item, details=error_msg)
        # Don't fail the whole process if cleanup fails
        return True, f"Extracted but cleanup failed: {str(e)}"


def find_zip_archives(directory):
    """Find all ZIP archive files in a directory.
    
    Returns a list of paths to ZIP files (.zip).
    """
    zip_files = []
    pattern_zip = os.path.join(directory, '*.zip')
    zip_files.extend(glob.glob(pattern_zip))
    return sorted(zip_files)


def extract_zip_archive(zip_file_path, extract_to_dir):
    """Extract a ZIP archive to the specified directory.
    
    Uses Python's zipfile module.
    Validates that extraction actually produced files.
    
    Returns (success: bool, message: str)
    """
    import zipfile
    try:
        files_before = set(os.listdir(extract_to_dir))
        
        with zipfile.ZipFile(zip_file_path, 'r') as zip_ref:
            zip_ref.extractall(extract_to_dir)
        
        files_after = set(os.listdir(extract_to_dir))
        new_files = files_after - files_before
        
        if new_files:
            logger.info(f"Successfully extracted {len(new_files)} file(s) from {os.path.basename(zip_file_path)}")
            return True, f"Extracted {len(new_files)} files"
        else:
            logger.error(f"ZIP archive contains no extractable files")
            return False, "Archive empty or corrupted"
            
    except Exception as e:
        logger.error(f"Error extracting ZIP archive: {e}")
        return False, str(e)


def process_zip_archives(directory, item):
    """Process all ZIP archives in a directory.
    
    Extracts all ZIP files, then removes them.
    """
    zip_files = find_zip_archives(directory)
    
    if not zip_files:
        return True, "No ZIP archives found"
    
    logger.info(f"Found {len(zip_files)} ZIP archive(s) in {directory}")
    
    item.extraction_status = 'extracting'
    item.extraction_started = django.utils.timezone.now()
    item.extraction_progress = 0
    item.save()
    
    ItemHistory.objects.create(item=item, details=f'Starting ZIP extraction ({len(zip_files)} files)')
    
    all_success = True
    for idx, zip_file in enumerate(zip_files):
        logger.info(f"Extracting ZIP archive: {zip_file}")
        success, message = extract_zip_archive(zip_file, directory)
        
        if not success:
            error_msg = f"ZIP extraction failed: {message}"
            logger.error(error_msg)
            item.extraction_status = 'failed'
            item.extraction_progress = 0
            item.save()
            ItemHistory.objects.create(item=item, details=error_msg)
            Notification.create_for_admin(
                f"ZIP extraction failed for '{item.name}': {message[:100]}",
                notification_type='zip_failure',
                item_hash=item.hash
            )
            return False, message
        
        progress = int((idx + 1) / len(zip_files) * 90)
        item.extraction_progress = progress
        item.save()
    
    # Remove all ZIP archive files after successful extraction
    for idx, zip_file in enumerate(zip_files):
        try:
            os.remove(zip_file)
            logger.info(f"Removed ZIP archive: {os.path.basename(zip_file)}")
        except Exception as e:
            logger.warning(f"Failed to remove ZIP archive {zip_file}: {e}")
    
    item.extraction_status = 'completed'
    item.extraction_progress = 100
    item.extraction_completed = django.utils.timezone.now()
    item.save()
    
    success_msg = f"Successfully extracted and removed {len(zip_files)} ZIP file(s)"
    logger.info(success_msg)
    ItemHistory.objects.create(item=item, details=success_msg)
    
    return True, success_msg


@shared_task(time_limit=3600, soft_time_limit=3300)
def transfer_files_async(item_hash):
    """Async task to transfer files from seedbox to local storage.
    
    This runs in the background and can take a long time for large files.
    Creates all FileTransfer records UPFRONT, then transfers them.
    """
    logger.warning(f"[transfer_files_async] ================= STARTING ================= for item {item_hash}")
    try:
        with transaction.atomic():
            if getattr(settings, 'PIPELINE_HARDENING_ENABLED', True):
                # PIPE-02 + COR-06: row-level lock prevents concurrent double-execution.
                # skip_locked=True: a second worker observing the row is locked gets None and exits.
                item = (
                    Item.objects
                    .select_for_update(skip_locked=True)
                    .filter(hash=item_hash)
                    .first()
                )
            else:
                # Legacy: simple get without lock. Faster but unsafe under concurrency.
                item = Item.objects.filter(hash=item_hash).first()
    except Item.DoesNotExist:
        # filter().first() returns None, not raises — this branch is unreachable
        # but kept for defensive coverage during the cutover.
        logger.error(f"[transfer_files_async] Item {item_hash} not found in database")
        return
    if item is None:
        # Either the row is locked by another worker OR it doesn't exist.
        # Skip_locked=True means we got None because another worker holds the lock.
        logger.info(f"[transfer_files_async] Item {item_hash} locked or missing, exiting")
        return
    
    logger.info(f"[transfer_files_async] ========== Processing item: {item.name} ({item.status}) ==========")
    logger.info(f"[transfer_files_async] Downloader: {item.downloader}, Seedbox: {item.downloader.seedbox if item.downloader else 'None'}")
    
    logger.info(f"[transfer_files_async] Transferring files for {item.name} (status={item.status})")
    
    if not item.downloader:
        logger.error(f"[transfer_files_async] No downloader assigned to {item.name}")
        return
    
    if not item.downloader.seedbox:
        logger.error(f"[transfer_files_async] No seedbox configured for {item.name}")
        return
    
    downloader = item.downloader
    seedbox = downloader.seedbox
    logger.info(f"[transfer_files_async] Downloader: {downloader.name}, Seedbox: {seedbox.name}, Auth: {seedbox.auth_type}")
    
    try:
        # Get the download path from the downloader
        client = downloader.client
        client._ensure_client()
        hash_value = item.hash
        logger.info(f"[transfer_files_async] Connected to downloader client for {item.name}")
        
        # Initialize variables
        category = ''
        remote_dir = ''
        torrent_name = ''
        
        # Get download info using downloader's get_download_info method
        logger.debug(f"[transfer_files_async] Getting download info for {downloader.downloadertype}")
        download_info = client.get_download_info(hash_value)
        remote_dir = download_info.get('remote_dir', '')
        files_to_copy = download_info.get('files_to_copy')
        is_single_file = download_info.get('is_single_file', False)
        name = download_info.get('name', item.name)
        torrent_name = name  # Use the name from download_info as the torrent name
        
        if not remote_dir:
            logger.error(f"[transfer_files_async] No remote directory found for {hash_value}")
            ItemHistory.objects.create(
                item=item,
                details=f"No remote directory found for {downloader.downloadertype} download"
            )
            item.status = 'Failed'
            item.save()
            return
        
        logger.info(f"[transfer_files_async] {downloader.downloadertype} - remote_dir={remote_dir}, name={name}, is_single_file={is_single_file}")
        
        # Handle AirDC++ folder downloads specially
        if downloader.downloadertype == 'AirDC++' and not is_single_file:
            # Check if this is a folder download
            try:
                history = item.history.filter(details__icontains='Folder bundle detected').first()
                if history:
                    is_folder = True
                    remote_dir = os.path.join(seedbox.base_download_folder, item.name)
                    files_to_copy = None
                    logger.info(f"[transfer_files_async] AirDC++ FOLDER - remote_dir={remote_dir}")
            except:
                pass
        
        # Connect to seedbox via SFTP
        logger.info(f"[transfer_files_async] Connecting to seedbox {seedbox.host}:{seedbox.port} as {seedbox.username} (auth={seedbox.auth_type})")
        ssh = _sftp_connect_with_retry(seedbox)
        
        logger.debug(f"[transfer_files_async] Opening SFTP channel")
        sftp = ssh.open_sftp()
        sftp.get_channel().settimeout(60)
        logger.info(f"[transfer_files_async] SFTP channel open for {item.name}")
        
        # For SABnzbd, check if remote_dir is a file or folder
        if downloader.downloadertype == 'SABNzbd':
            try:
                file_stat = sftp.stat(remote_dir)
                import stat as stat_module_check
                if stat_module_check.S_ISREG(file_stat.st_mode):
                    # It's a file, need to find the parent directory
                    current_dir = os.path.dirname(remote_dir)
                    parent_name = os.path.basename(current_dir)
                    if ' (' in parent_name and ' of ' in parent_name:
                        base_name = parent_name.split(' (')[0]
                        category_dir = os.path.dirname(current_dir)
                        try:
                            category_contents = sftp.listdir(category_dir)
                            matching_folders = [f for f in category_contents if f.startswith(base_name)]
                            if matching_folders:
                                remote_dir = os.path.join(category_dir, matching_folders[0])
                        except:
                            pass
                    else:
                        remote_dir = current_dir
            except:
                pass
         
        # Determine destination folder - create subfolder for item
        if item.manager and item.manager.folder:
            final_base_folder = item.manager.folder.folder
        elif item.downloader and item.downloader.options:
            # For downloaders like AirDC++ without a manager, get folder from downloader config
            from entities.models import DownloadFolder
            target_folder_id = item.downloader.options.get('target_folder')
            if target_folder_id:
                try:
                    target_folder = DownloadFolder.objects.get(id=target_folder_id)
                    final_base_folder = target_folder.folder
                    logger.info(f"[transfer_files_async] Using target folder from downloader config: {final_base_folder}")
                except Exception as e:
                    logger.error(f"[transfer_files_async] Could not find target folder {target_folder_id}: {e}")
                    final_base_folder = '/tmp'
            else:
                logger.warning(f"[transfer_files_async] No target folder in downloader config, using /tmp")
                final_base_folder = '/tmp'
        else:
            final_base_folder = '/tmp'
        
        # Check if this is a Blackhole manager
        is_blackhole = item.manager and item.manager.managertype == 'Blackhole'
        
        # Get temp folder from Blackhole manager config
        if is_blackhole and item.manager and item.manager.temp_folder:
            temp_base_folder = item.manager.temp_folder
        else:
            temp_base_folder = None
        
        # Get category from item (stored from Blackhole manager based on subfolder)
        # For SABNzbd, this is already stored in item.category when the item was created
        # For RTorrent, use the label from torrent_info if available, otherwise use item.category
        if not category:
            category = item.category or ''
        
        # Add category subfolder if available (from rtorrent label or manager settings)
        # Only for Blackhole manager
        if is_blackhole and category:
            final_base_folder = os.path.join(final_base_folder, category)
        
        # Create a subfolder for this item using a sanitized name
        # For Blackhole manager, ALWAYS create a subfolder per item (even for single files)
        # For other managers, single files go directly in base folder
        import re
        sanitized_name = re.sub(r'[<>:"/\\|?*]', '', item.name)
        sanitized_name = sanitized_name.strip()
        
        # Determine if we need a subfolder for this item
        # Blackhole: always create subfolder to keep items organized
        # Other managers: only for multi-file downloads
        if is_blackhole or not is_single_file:
            # Create subfolder for this item
            subfolder_name = sanitized_name
        else:
            # Single file in non-Blackhole manager: no subfolder
            subfolder_name = None
        
        # Use temporary folder for transfer for Blackhole manager, then move to final location after complete
        if is_blackhole and temp_base_folder:
            if subfolder_name:
                temp_folder = os.path.join(temp_base_folder, subfolder_name)
                final_folder = os.path.join(final_base_folder, subfolder_name)
            else:
                temp_folder = temp_base_folder
                final_folder = final_base_folder
            
            os.makedirs(temp_folder, exist_ok=True)
            logger.info(f"Created temp folder for transfer: {temp_folder}")
        else:
            temp_folder = None
            if subfolder_name:
                final_folder = os.path.join(final_base_folder, subfolder_name)
            else:
                final_folder = final_base_folder
            
            os.makedirs(final_folder, exist_ok=True)
            logger.info(f"Created folder for transfer: {final_folder}")
        
        # Build transfer list
        # For single-file torrents, only transfer that specific file
        # For multi-file torrents, recursively transfer all files from the directory
        transfer_list = []  # List of (remote_path, relative_path) tuples
        import stat as stat_module_list
        
        # Use temp_folder for transfer if Blackhole, else use final_folder
        item_folder = temp_folder if temp_folder else final_folder
        
        logger.info(f"[transfer_files_async] About to list files from remote_dir={remote_dir}, files_to_copy={files_to_copy}, is_single_file={is_single_file}, downloader={downloader.downloadertype}")
        
        if is_single_file and downloader.downloadertype == 'RTorrent':
            # Single-file torrent: find and transfer the actual media file
            # The torrent_name should be the actual filename (e.g., "Movie.mkv")
            try:
                # For single-file torrents, the torrent name IS the filename
                # But check in the directory to be safe (in case structure changed)
                logger.info(f"[transfer_files_async] Listing files in {remote_dir}")
                remote_files = sftp.listdir(remote_dir)
                logger.info(f"[transfer_files_async] Found {len(remote_files)} files in remote_dir")
                media_file = None
                
                # First, try to match the exact torrent name
                if torrent_name in remote_files:
                    media_file = torrent_name
                    logger.info(f"[transfer_files_async] Matched exact torrent name: {media_file}")
                else:
                    # If exact match not found, look for media files (skip .nfo, Screens directories, etc)
                    logger.info(f"[transfer_files_async] Looking for media file in {len(remote_files)} files...")
                    for filename in remote_files:
                        if filename.lower().endswith(('.mkv', '.mp4', '.avi', '.mov', '.m4v', '.flv', '.wmv', '.webm')):
                            # Additional check: make sure it matches the expected name pattern
                            # Extract just the filename from torrent_name for comparison
                            torrent_basename = os.path.basename(torrent_name)
                            # Check if filenames are similar (handles minor naming variations)
                            if filename.lower() == torrent_basename.lower():
                                media_file = filename
                                break
                    
                    # If still no match, use files_to_copy if available
                    if not media_file and files_to_copy:
                        # Use the specific file from files_to_copy
                        for filename in remote_files:
                            if filename in files_to_copy:
                                media_file = filename
                                logger.info(f"[transfer_files_async] Matched file from files_to_copy: {media_file}")
                                break
                    
                    # Last resort: take the first media file as fallback
                    if not media_file:
                        logger.warning(f"[transfer_files_async] No match found in files_to_copy, using first media file as fallback")
                        for filename in remote_files:
                            if filename.lower().endswith(('.mkv', '.mp4', '.avi', '.mov', '.m4v')):
                                media_file = filename
                                break
                
                if not media_file:
                    logger.warning(f"No media file found in single-file torrent directory {remote_dir}. Torrent: {torrent_name} - will transfer all files")
                    # Set is_single_file to False to trigger multi-file transfer
                    is_single_file = False
                else:
                    full_remote_path = os.path.join(remote_dir, media_file)
                    # Verify the file exists
                    sftp.stat(full_remote_path)
                    # Use just the filename as the relative path so it transfers to item_folder/filename
                    transfer_list.append((full_remote_path, media_file))
                    logger.info(f"Single-file torrent detected: {media_file} (from torrent: {torrent_name})")
            except Exception as e:
                logger.error(f"Cannot process single-file torrent in {remote_dir}: {e} - aborting transfer")
                sftp.close()
                ssh.close()
                return
        
        # Single file handling - directly add files_to_copy to transfer_list
        # (avoids walking the entire base download directory)
        if is_single_file and files_to_copy:
            logger.info(f"[transfer_files_async] Single file: adding {files_to_copy} from {remote_dir}")
            for filename in files_to_copy:
                remote_path = os.path.join(remote_dir, filename)
                transfer_list.append((remote_path, filename))
            logger.info(f"[transfer_files_async] Added {len(transfer_list)} files to transfer list")
        
        # Transfer ALL files in the directory (handles both multi-file torrents and single-file torrents without media)
        # Only run if transfer_list is still empty
        if len(transfer_list) == 0:
            # Multi-file torrent: recursively traverse directories
            actual_remote_dir = remote_dir
            try:
                remote_files = sftp.listdir(actual_remote_dir)
                logger.info(f"[transfer_files_async] Found {len(remote_files)} files in {actual_remote_dir}")
            except Exception as e:
                logger.error(f"Cannot access remote directory {actual_remote_dir}: {e}")
                sftp.close()
                ssh.close()
                return
            
            def walk_remote_sftp(sftp_obj, remote_path, base_remote_dir, relative_prefix=''):
                """Recursively walk remote directory and collect files while preserving structure."""
                nonlocal transfer_list
                logger.warning(f"[walk_remote_sftp] Walking {remote_path}, files_to_copy={files_to_copy}, downloader={downloader.downloadertype}")
                try:
                    remote_items = sftp_obj.listdir(remote_path)
                    logger.info(f"[walk_remote_sftp] Found {len(remote_items)} items in {remote_path}: {remote_items[:10]}")
                except Exception as e:
                    logger.warning(f"Cannot access remote directory {remote_path}: {e}")
                    return
                
                for item_name in remote_items:
                    remote_item_path = os.path.join(remote_path, item_name)
                    relative_item_path = os.path.join(relative_prefix, item_name) if relative_prefix else item_name
                    
                    # Check if it's a directory or file first (needed for AirDC++ filtering)
                    try:
                        item_stat = sftp_obj.stat(remote_item_path)
                        is_dir = stat_module_list.S_ISDIR(item_stat.st_mode)
                    except Exception as e:
                        logger.warning(f"Cannot stat {remote_item_path}: {e}")
                        continue
                    
                    # For all downloaders, only transfer items matching files_to_copy list (if specified)
                    if files_to_copy:
                        # Check if this file/folder matches any in files_to_copy
                        logger.info(f"[transfer_files_async] files_to_copy={files_to_copy}, checking {item_name}")
                        if item_name not in files_to_copy:
                            logger.debug(f"[transfer_files_async] Skipping {item_name} (not in files_to_copy)")
                            continue
                    
                    # Skip hidden files, images, HTML
                    if item_name.startswith('.') or item_name.endswith('.jpg') or item_name.endswith('.html'):
                        continue
                    
                    logger.info(f"[walk_remote_sftp] Processing item {item_name}, is_dir={is_dir}")
                    
                    # Handle directories and files
                    if is_dir:
                        # Recursively walk subdirectories
                        logger.info(f"[walk_remote_sftp] Recursing into directory {item_name}")
                        walk_remote_sftp(sftp_obj, remote_item_path, base_remote_dir, relative_item_path)
                    else:
                        # It's a file, add to transfer list
                        logger.info(f"[walk_remote_sftp] Adding file {item_name} to transfer list")
                        transfer_list.append((remote_item_path, relative_item_path))
            
            walk_remote_sftp(sftp, actual_remote_dir, actual_remote_dir)
            logger.info(f"[transfer_files_async] walk_remote_sftp completed, transfer_list has {len(transfer_list)} items")
        logger.info(f"[transfer_files_async] Found {len(transfer_list)} files to transfer for {item.name} (including nested directories)")
        
        # STEP 1: Create FileTransfer records UPFRONT for ALL files
        # This ensures the dashboard shows correct total size from the start
        # transfer_list now contains (remote_path, relative_path) tuples.
        #
        # The per-file loop below (around line 770) handles dedup correctly:
        # reuses completed transfers, leaves in-flight ones alone, deletes
        # stale ones. There used to be an early-return here that bailed if
        # ANY pending/transferring transfer existed — that prevented the
        # function from creating the missing transfers (e.g., the ~545
        # files the Phase 1 band-aid had not created on a previous run),
        # which is why a 120 GB torrent could end up with only 138 (25 GB)
        # FileTransfer rows and stall out at 20%. The per-file logic makes
        # the early-return redundant (and harmful), so it's removed.

        transfer_records = {}
        skipped_count = 0

        if len(transfer_list) == 0:
             logger.warning(f"[transfer_files_async] NO FILES FOUND to transfer for {item.name}! remote_dir={remote_dir}")
            # Don't return early - still proceed to post_process since files may already exist locally
            # Continue to the post-process section below

        # Dedup transfer_list by relative_path (filename). Some downloaders
        # (rTorrent in particular) can return the same logical file twice
        # with slightly different remote paths — e.g., once via the proper
        # directory tree and once via a fallback listing. Without dedup,
        # each duplicate entry writes its own "Copied" history event and
        # re-runs the inner loop's status='completed' save for the same
        # FileTransfer row. Defensive — the DB-level unique constraint is
        # the authoritative fix for row duplicates.
        if transfer_list:
            seen = set()
            deduped = []
            for _rp, _fp in transfer_list:
                if _fp not in seen:
                    seen.add(_fp)
                    deduped.append((_rp, _fp))
            if len(deduped) < len(transfer_list):
                logger.debug(
                    f"[transfer_files_async] Deduped transfer_list "
                    f"{len(transfer_list)} -> {len(deduped)} (by filename)"
                )
                transfer_list = deduped

        for remote_file_path, relative_path in transfer_list:
            # Build local path preserving folder structure
            local_path = os.path.join(item_folder, relative_path)
            
            # Create local directories if needed
            local_dir = os.path.dirname(local_path)
            os.makedirs(local_dir, exist_ok=True)
            
            # Get remote file size
            try:
                file_stat = sftp.stat(remote_file_path)
                file_size = file_stat.st_size
            except Exception as e:
                logger.warning(f"Cannot stat {remote_file_path}: {e}")
                continue
            
            # Deduplicate: check for existing transfers for this item+filename
            existing_transfer = FileTransfer.objects.filter(
                item=item,
                filename=relative_path
            ).first()
            
            if existing_transfer:
                if existing_transfer.status == 'completed' and existing_transfer.file_size == file_size:
                    # Already transferred successfully - reuse the record
                    logger.info(f"Reusing existing completed transfer for {relative_path}")
                    transfer_records[(remote_file_path, relative_path)] = existing_transfer
                    continue
                elif existing_transfer.status in ('pending', 'transferring'):
                    # Mid-flight from a prior run - leave alone. Deleting an
                    # in-progress transfer would lose bytes already copied and
                    # confuse the dashboard. The next call (or
                    # check_stalled_transfers recovery) will pick it up once it
                    # finishes or stalls out.
                    logger.debug(
                        f"Leaving in-flight transfer for {relative_path} "
                        f"(status={existing_transfer.status}); not touching"
                    )
                    continue
                else:
                    # Stale terminal-but-not-completed record (failed, or
                    # completed-with-wrong-size from a local edit). Clean it
                    # up so the per-file logic below creates a fresh record.
                    logger.info(f"Removing stale {existing_transfer.status} transfer record for {relative_path}")
                    existing_transfer.delete()
            
            # Check if file exists locally and verify file size
            if os.path.exists(local_path):
                local_size = os.path.getsize(local_path)
                if local_size == file_size:
                    # File exists and has correct size - skip it, but make sure
                    # a completed FileTransfer record exists so manager
                    # post-processing (e.g. Bindery) can locate the staged file
                    # even when this run didn't transfer anything.
                    logger.info(f"Skipped (complete): {local_path} ({local_size / (1024*1024):.1f}MB)")
                    skipped_count += 1
                    if (remote_file_path, relative_path) not in transfer_records:
                        try:
                            # get_or_create is atomic at the row level — prevents
                            # the check-then-create race that left 2,037 duplicate
                            # rows in the user's 120 GB torrent (sum(file_size)
                            # showed 480 GB). If a concurrent transfer_files_async
                            # task already created this row, we get the existing
                            # one back instead of creating a duplicate.
                            transfer, created = FileTransfer.objects.get_or_create(
                                item=item,
                                filename=relative_path,
                                defaults={
                                    'remote_path': remote_file_path,
                                    'local_path': local_path,
                                    'file_size': file_size,
                                    'status': 'completed',
                                }
                            )
                            transfer_records[(remote_file_path, relative_path)] = transfer
                        except Exception as e:
                            logger.error(f"Failed to create completed FileTransfer record for {relative_path}: {e}")
                    continue
                else:
                    # File exists but size mismatch - delete and retransfer
                    logger.warning(f"File size mismatch for {local_path}: local={local_size}, remote={file_size}. Deleting and retransferring.")
                    try:
                        os.remove(local_path)
                    except Exception as e:
                        logger.error(f"Failed to delete incomplete file {local_path}: {e}")
                        continue
            
            # Create FileTransfer record with 'pending' status.
            # Use get_or_create for atomic check-then-create — prevents the
            # duplicate-row race when multiple transfer_files_async tasks
            # run concurrently (e.g., check_downloaders + Block B requeue).
            # See commit history; user observed 2,037 duplicates inflating
            # a 120 GB torrent to 480 GB before this fix.
            try:
                transfer, created = FileTransfer.objects.get_or_create(
                    item=item,
                    filename=relative_path,
                    defaults={
                        'remote_path': remote_file_path,
                        'local_path': local_path,
                        'file_size': file_size,
                        'status': 'pending',
                    }
                )
                transfer_records[(remote_file_path, relative_path)] = transfer
            except Exception as e:
                logger.error(f"Failed to create FileTransfer record for {relative_path}: {e}")
                continue
         
        logger.info(f"Created {len(transfer_records)} FileTransfer records upfront (skipped {skipped_count})")
        
        ItemHistory.objects.create(item=item, details=f'Created {len(transfer_records)} file transfer records')
        
        # STEP 2: Now transfer the files
        copied_count = 0
        failed_count = 0
        
        for (remote_file_path, filename), transfer in transfer_records.items():
            local_path = transfer.local_path
            file_size = transfer.file_size
            
            # Update status to transferring
            transfer.status = 'transferring'
            transfer.started = django.utils.timezone.now()
            transfer.save()
            
            retry_count = 0
            max_retries = 3
            transfer_successful = False
            
            while retry_count < max_retries and not transfer_successful:
                try:
                    logger.info(f"SFTP: {remote_file_path} -> {local_path} ({file_size / 1024 / 1024:.1f}MB)" + 
                               (f" [Retry {retry_count+1}/{max_retries}]" if retry_count > 0 else ""))
                    
                    # Progress callback with throttling
                    last_update = [0]
                    update_interval = 1024 * 1024
                    
                    def progress_callback(bytes_so_far, bytes_total):
                        """Update progress during file transfer."""
                        try:
                            if bytes_so_far - last_update[0] >= update_interval or bytes_so_far >= bytes_total:
                                transfer.bytes_transferred = bytes_so_far
                                transfer.save()
                                last_update[0] = bytes_so_far
                        except Exception as e:
                            logger.debug(f"Could not update transfer progress: {e}")
                    
                    # Download file with progress tracking + stall watchdog.
                    # _sftp_get_with_timeout raises SFTPStallTimeout if sftp.get()
                    # hangs longer than SFTP_GET_TIMEOUT_SECONDS — without this,
                    # a dead seedbox can hold the Celery worker hostage for up to
                    # task_time_limit (1h).
                    _sftp_get_with_timeout(
                        sftp, remote_file_path, local_path,
                        callback=progress_callback,
                        timeout=SFTP_GET_TIMEOUT_SECONDS,
                    )
                    
                    # Update transfer record - mark as completed
                    transfer.bytes_transferred = file_size
                    transfer.status = 'completed'
                    transfer.completed = django.utils.timezone.now()
                    transfer.save()
                    
                    ItemHistory.objects.create(item=item, details=f'Copied {filename}')
                    copied_count += 1
                    transfer_successful = True
                    
                except Exception as e:
                    logger.error(f"Failed to copy {remote_file_path}: {e}")
                    retry_count += 1
                    
                    if retry_count < max_retries:
                        # Try to recover connection and retry
                        logger.warning(f"Attempting to reconnect for retry {retry_count}/{max_retries}...")
                        try:
                            sftp.close()
                            ssh.close()
                        except:
                            pass
                        
                        # Reconnect
                        try:
                            ssh = paramiko.SSHClient()
                            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                            
                            if seedbox.auth_type == 'password':
                                ssh.connect(seedbox.host, port=seedbox.port, username=seedbox.username, password=seedbox.password, timeout=10)
                            else:
                                pkey = paramiko.RSAKey.from_private_key_string(seedbox.ssh_key)
                                ssh.connect(seedbox.host, port=seedbox.port, username=seedbox.username, pkey=pkey, timeout=10)
                            
                            sftp = ssh.open_sftp()
                            sftp.get_channel().settimeout(60)
                            logger.info("Successfully reconnected to seedbox")
                        except Exception as reconnect_error:
                            logger.error(f"Failed to reconnect: {reconnect_error}")
                            transfer_successful = False
                    else:
                        # Max retries exceeded
                        transfer.status = 'failed'
                        transfer.error_message = f"Transfer failed after {max_retries} retries: {str(e)}"
                        transfer.save()
                        ItemHistory.objects.create(item=item, details=f'Failed to copy {filename} after {max_retries} retries: {str(e)}')
                        Notification.create_for_admin(
                            f"SFTP transfer failed for '{item.name}': {str(e)[:100]}",
                            notification_type='sftp_failure',
                            item_hash=item.hash
                        )
                        failed_count += 1
                        transfer_successful = True  # Exit retry loop
        
        sftp.close()
        ssh.close()
        
        logger.info(f"Async transfer complete for {item.name} ({copied_count} files)")
        ItemHistory.objects.create(item=item, details=f'Async file transfer complete ({copied_count} files)')
        
        # Post-transfer processing: extract ZIP and RAR archives FIRST
        # (before moving to final folder so extraction happens in temp location)
        local_folder = None
        if copied_count > 0:
            try:
                first_transfer = FileTransfer.objects.filter(item=item, status='completed').first()
                if first_transfer and first_transfer.local_path:
                    local_folder = os.path.dirname(first_transfer.local_path)
                    
                    # Process ZIP archives first
                    logger.info(f"Processing ZIP archives in: {local_folder}")
                    success, message = process_zip_archives(local_folder, item)
                    if not success:
                        logger.warning(f"ZIP processing encountered issues: {message}")
                    else:
                        logger.info(f"ZIP processing completed: {message}")
                    
                    # Then process RAR archives (in case there were RARs inside ZIPs or alongside)
                    logger.info(f"Processing RAR archives in: {local_folder}")
                    success, message = process_rar_archives(local_folder, item)
                    if not success:
                        logger.warning(f"RAR processing encountered issues: {message}")
                    else:
                        logger.info(f"RAR processing completed: {message}")
            except Exception as e:
                logger.error(f"Error during archive processing: {e}")
         
        # THEN move from temp folder to final destination (Blackhole manager only)
        # This happens AFTER extraction
        # Verify ALL transfers have completed (success or failure) before moving
        total_transfers = FileTransfer.objects.filter(item=item).count()
        completed_transfers = FileTransfer.objects.filter(
            item=item
        ).exclude(status__in=['pending', 'transferring']).count()
        all_transfers_done = (total_transfers > 0 and completed_transfers == total_transfers)
        
        if is_blackhole and all_transfers_done and temp_folder and os.path.exists(temp_folder):
            try:
                # Create category folder if it doesn't exist
                if not os.path.exists(final_base_folder):
                    os.makedirs(final_base_folder)
                    logger.info(f"Created category folder: {final_base_folder}")
                
                # Only delete final folder if temp folder exists (meaning we're replacing it)
                # This prevents accidental deletion of existing extracted content
                if os.path.exists(temp_folder):
                    if os.path.exists(final_folder):
                        import shutil
                        shutil.rmtree(final_folder)
                        logger.info(f"Removed existing final folder: {final_folder}")
                    # Rename temp to final (atomic on same filesystem; cross-device safe via shutil.move)
                    safe_rename(temp_folder, final_folder)
                    logger.info(f"Moved temp folder to final: {final_folder}")
                    ItemHistory.objects.create(item=item, details=f'Moved to final folder: {final_folder}')
                else:
                    logger.warning(f"Temp folder does not exist: {temp_folder}. Final folder may already be in place.")
            except Exception as e:
                logger.error(f"Failed to move temp folder to final: {e}")
                ItemHistory.objects.create(item=item, details=f'Failed to move to final folder: {e}')
        
        # Call manager post-processing BEFORE marking as Completed
        # This sends to Sonarr/Radarr/etc while item is still in PostProcessing
        # Call even if copied_count is 0 (files may have already existed locally).
        # post_process_succeeded tracks the outcome so items are NOT marked
        # Completed when post-processing failed (for Bindery-managed items: they
        # stay PostProcessing and are owned by the manager poll + retry task).
        post_process_succeeded = None
        if item.manager and hasattr(item.manager, 'client'):
            try:
                # Get local_folder from first completed transfer if not set
                if not local_folder:
                    first_transfer = FileTransfer.objects.filter(item=item, status='completed').first()
                    if first_transfer and first_transfer.local_path:
                        local_folder = os.path.dirname(first_transfer.local_path)
                    elif item.manager and item.manager.folder:
                        # If no transfer record, use manager's folder (files may already exist locally)
                        local_folder = item.manager.folder.folder
                
                # Always try to call post_process - log even if local_folder is None
                logger.info(f"Attempting manager post-processing for {item.name}, local_folder={local_folder}")
                
                if local_folder:
                    # Construct the download path with item folder name included
                    # Get the sanitized item name (same logic as during transfer)
                    sanitized_item_name = re.sub(r'[<>:"/\\|?*]', '', item.name)
                    sanitized_item_name = sanitized_item_name.strip()
                    
                    # Get base folder (local version for extraction check, remote for API)
                    if item.manager.folder:
                        # Construct remote path
                        if item.manager.folder.remote_folder_name:
                            base_remote_path = item.manager.folder.remote_folder_name
                        else:
                            base_remote_path = item.manager.folder.folder
                        
                        # For forceProcess, we need the full file path
                        # Construct it as base_folder/filename (single files go directly in base folder)
                        download_path = os.path.join(base_remote_path, item.name)
                    else:
                        download_path = local_folder
                    
                    # Check if there's only one video file in the directory - if so, use that file path
                    video_extensions = ['.mkv', '.mp4', '.avi', '.mov', '.flv', '.wmv', '.webm']
                    try:
                        local_item_path = os.path.join(local_folder, sanitized_item_name)
                        if os.path.isdir(local_item_path):
                            video_files = [f for f in os.listdir(local_item_path) 
                                         if os.path.isfile(os.path.join(local_item_path, f)) 
                                         and os.path.splitext(f)[1].lower() in video_extensions]
                            if len(video_files) == 1:
                                # Single video file - use it instead of directory
                                video_file = video_files[0]
                                download_path = os.path.join(download_path, video_file)
                                logger.info(f"Single video file detected: {video_file}, using file path for post-processing")
                    except Exception as e:
                        logger.warning(f"Could not check for single video file: {e}")
                    
                    # Only call post_process if the manager supports it
                    if item.manager:
                        logger.info(f"Calling manager post-processing for {item.name} at path: {download_path}")
                        try:
                            success, pp_message = item.manager.post_process(item, download_path)
                            
                            if success:
                                post_process_succeeded = True
                                logger.info(f"Manager post-processing succeeded: {pp_message}")
                                ItemHistory.objects.create(item=item, details=f'Post-processing succeeded: {pp_message}')
                                
                                # Cleanup seedbox files after successful post-processing
                                # Get the first completed transfer to know what was transferred
                                first_transfer = FileTransfer.objects.filter(item=item, status='completed').first()
                                if first_transfer and item.downloader:
                                    try:
                                        cleanup_success, cleanup_message = item.downloader.client.cleanup(first_transfer)
                                        ItemHistory.objects.create(item=item, details=cleanup_message)
                                        if cleanup_success:
                                            logger.info(f"Seedbox cleanup: {cleanup_message}")
                                        else:
                                            logger.warning(f"Seedbox cleanup error: {cleanup_message}")
                                    except Exception as e:
                                        logger.warning(f"Error calling cleanup: {e}")
                                        ItemHistory.objects.create(item=item, details=f'Cleanup error: {str(e)}')
                            else:
                                post_process_succeeded = False
                                logger.error(f"Manager post-processing failed: {pp_message}")
                                ItemHistory.objects.create(item=item, details=f'Post-processing failed: {pp_message}')
                                Notification.create_for_admin(
                                    f"Post-processing failed for '{item.name}': {pp_message[:100]}",
                                    notification_type='postprocess_failure',
                                    item_hash=item.hash
                                )
                                logger.info(f"Scheduling post-processing retry for {item.name} in 5 minutes")
                                retry_postprocessing.apply_async(args=[item_hash], countdown=300)
                        except Exception as e:
                            post_process_succeeded = False
                            logger.error(f"Error calling post-processing: {e}")
                            ItemHistory.objects.create(item=item, details=f'Error calling post-processing: {str(e)}')
                            retry_postprocessing.apply_async(args=[item_hash], countdown=300)
            except Exception as e:
                post_process_succeeded = False
                logger.error(f"Error calling manager post-processing: {e}")
                ItemHistory.objects.create(item=item, details=f'Error calling post-processing: {str(e)}')
        
        # Mark item as Completed after transfer AND extraction AND move AND post-processing are done.
        # Only if not already marked as Failed by extraction. Bindery-managed
        # items are intentionally NOT marked Completed when post-processing
        # failed: the Bindery poll owns their state (recoverable failure or
        # Completed once imported) and retry_postprocessing re-attempts. Other
        # manager types keep the existing behavior (mark Completed and let the
        # retry task re-run post-processing).
        bindery_pp_failed = (
            post_process_succeeded is False
            and item.manager
            and item.manager.managertype == 'Bindery'
        )
        if item.status != 'Failed' and not bindery_pp_failed:
            item.status = 'Completed'
            item.save()
            ItemHistory.objects.create(item=item, details='File transfer and post-processing completed, item marked as Completed')
            
            # Send completion notification
            Notification.create_for_admin(
                f"Item completed: {item.name}",
                notification_type='item_completed',
                item_hash=item.hash
            )
        elif item.status != 'Failed':
            logger.warning(
                f"Item {item.name} post-processing failed; leaving in {item.status} "
                f"(retry scheduled)"
            )
        else:
            logger.warning(f"Item {item.name} marked as Failed during extraction, not marking as Completed")

        logger.info(f"[transfer_files_async] ========== COMPLETED successfully for {item.name} ==========")
        
    except Exception as e:
        logger.error(f"[transfer_files_async] ========== FAILED for {item_hash}: {e} ==========", exc_info=True)
        try:
            ItemHistory.objects.create(item=item, details=f'Async transfer failed: {str(e)}')
        except:
            pass


@shared_task
def postprocess_item(item_hash):
    """Post-process a completed download: mark as completed and queue async file transfer."""
    logger.info(f"[postprocess_item] Starting post-processing for item {item_hash}")
    try:
        item = Item.objects.get(hash=item_hash)
    except Item.DoesNotExist:
        logger.error(f"[postprocess_item] Item {item_hash} not found in database")
        return
    
    logger.info(f"[postprocess_item] Processing {item.name} (current_status={item.status})")
    
    if not item.downloader:
        logger.error(f"[postprocess_item] No downloader assigned to {item.name}")
        ItemHistory.objects.create(item=item, details='No downloader assigned')
        Notification.create_for_admin(
            f"No downloader assigned for '{item.name}'",
            notification_type='downloader_failure',
            item_hash=item.hash
        )
        item.status = 'Failed'
        item.save()
        return
    
    downloader = item.downloader
    seedbox = downloader.seedbox
    logger.info(f"[postprocess_item] Downloader: {downloader.name} ({downloader.downloadertype}), Seedbox: {seedbox.name if seedbox else 'None'}")
    
    if not seedbox:
        logger.error(f"[postprocess_item] No seedbox configured for downloader {downloader.name}")
        ItemHistory.objects.create(item=item, details='No seedbox configured for downloader')
        Notification.create_for_admin(
            f"No seedbox configured for downloader '{downloader.name}' - {item.name}",
            notification_type='downloader_failure',
            item_hash=item.hash
        )
        item.status = 'Failed'
        item.save()
        return
    
    try:
        logger.debug(f"[postprocess_item] Connecting to {downloader.downloadertype} client")
        client = downloader.client
        client._ensure_client()
        hash_value = item.hash
        
        # Verify download completion using downloader's verify_completion method
        logger.debug(f"[postprocess_item] Verifying download completion on {downloader.downloadertype}")
        success, message = client.verify_completion(hash_value)
        if not success:
            logger.error(f"[postprocess_item] {downloader.downloadertype} verification failed: {message}")
            ItemHistory.objects.create(item=item, details=message)
            Notification.create_for_admin(
                f"{downloader.downloadertype} verification failed: {item.name}",
                notification_type='downloader_verification_failed',
                item_hash=item.hash
            )
            item.status = 'Failed'
            item.save()
            return
        
        # Download is complete on the downloader - transition to PostProcessing (file transfer)
        # Don't mark as Completed yet - that happens after transfer completes
        logger.info(f"[postprocess_item] Download verified complete, setting status to PostProcessing for {item.name}")
        item.status = 'PostProcessing'
        item.save()
        ItemHistory.objects.create(item=item, details='Download complete on downloader, queuing file transfer')
        logger.info(f"[postprocess_item] {item.name} ready for file transfer")
        
        try:
            logger.info(f"[postprocess_item] Queuing async file transfer for {item.name} with 5-second countdown")
            transfer_files_async.apply_async(args=[item_hash], countdown=5)
            ItemHistory.objects.create(item=item, details='Queued async file transfer (5s countdown)')
            logger.info(f"[postprocess_item] Successfully queued transfer_files_async for {item.name}")
        except Exception as e:
            logger.error(f"[postprocess_item] Failed to queue async transfer: {e}", exc_info=True)
            ItemHistory.objects.create(item=item, details=f'Failed to queue transfer: {str(e)}')
        
    except Exception as e:
        logger.error(f"[postprocess_item] Error post-processing {item_hash}: {e}", exc_info=True)
        ItemHistory.objects.create(item=item, details=f'Post-processing failed: {str(e)}')
        Notification.create_for_admin(
            f"Post-processing failed for '{item.name}': {str(e)[:100]}",
            notification_type='postprocess_failure',
            item_hash=item.hash
        )
        item.status = 'Failed'
        item.save()


@shared_task
def check_downloaders():
    """Check all configured downloaders for completed downloads."""
    logger.debug(f"[check_downloaders] Starting downloader check")
    downloaders = Downloader.objects.all()
    logger.debug(f"[check_downloaders] Found {len(downloaders)} downloader(s)")
    
    for downloader in downloaders:
        try:
            logger.debug(f"[check_downloaders] Checking {downloader.downloadertype} downloader: {downloader.name}")
            client = downloader.client
            client._ensure_client()
            
            if downloader.downloadertype == 'AirDC++':
                # AirDC++ has its own processing logic in the downloader class
                client.process_completed()
            
            else:  # Generic handling for other downloader types
                # Use the downloader's get_completed method
                try:
                    logger.info(f"[check_downloaders] {downloader.downloadertype}: Calling get_completed()...")
                    completed = client.get_completed()
                    logger.info(f"[check_downloaders] {downloader.downloadertype}: get_completed() returned {len(completed)} items")
                    logger.debug(f"[check_downloaders] {downloader.downloadertype}: Found {len(completed)} completed torrent(s)")
                    for torrent_info in completed:
                        hash_value = torrent_info.get('hash', '')
                        if not hash_value:
                            continue
                        # Case-insensitive lookup
                        try:
                            item = Item.objects.get(hash__iexact=hash_value)
                            logger.debug(f"[check_downloaders] {downloader.downloadertype}: Found item {item.name} (hash={hash_value}, status={item.status})")
                            # Only call postprocess if not already processed/completed/failed AND has a downloader assigned
                            if item.status not in ['Completed', 'Failed', 'PostProcessing']:
                                if not item.downloader:
                                    logger.debug(f"[check_downloaders] {downloader.downloadertype}: Skipping {item.name} - no downloader assigned yet (will retry later)")
                                else:
                                    logger.info(f"[check_downloaders] {downloader.downloadertype}: Queueing postprocess_item for {item.name} (status={item.status})")
                                    postprocess_item.delay(hash_value)
                            else:
                                logger.debug(f"[check_downloaders] {downloader.downloadertype}: Skipping {item.name} - already in status {item.status}")
                        except Item.DoesNotExist:
                            logger.debug(f"[check_downloaders] {downloader.downloadertype}: Hash {hash_value} not in database")
                except Exception as e:
                    logger.error(f"[check_downloaders] {downloader.downloadertype}: Error getting completed downloads: {e}")
        except Exception as e:
            logger.error(f"[check_downloaders] Error checking downloader {downloader.name}: {e}", exc_info=True)


def _recover_one_item(item, now):
    """Single recovery decision for one PostProcessing item.

    Returns True when the item needs recovery (has failed or pending
    transfers), False when all transfers are completed (defer to the
    manager's own pipeline — the check_queue poll owns status) or when
    nothing is unfinished. The caller performs the recovery actions
    (last_recovery_at marker, transfer cleanup, transfer_files_async
    dispatch) so the marker-before-delay ordering stays visible in
    check_stalled_transfers' source.
    """
    transfers = FileTransfer.objects.filter(item=item)
    has_failed = transfers.filter(status='failed').exists()
    has_pending = transfers.filter(status='pending').exists()
    all_completed = transfers.exists() and not transfers.exclude(status='completed').exists()

    if all_completed:
        # Transfers are done; defer to the manager's own pipeline.
        # check_queue on Bindery (and the equivalent on other managers) owns status.
        return False

    return has_failed or has_pending


def _legacy_check_stalled_transfers():
    """Legacy Block A + Block B recovery loop (pre-Phase-5).

    Preserved under the PIPELINE_HARDENING_ENABLED=False branch for one
    release per the canary-deployment convention. Setting the env-var to
    false reverts to this two-block recovery path without code changes.
    """
    from django.utils import timezone
    from datetime import timedelta

    stall_threshold = timezone.now() - timedelta(minutes=5)
    stalled_count = 0

    # Catch "Completed but not really" items: status=Completed but the item
    # still has unfinished transfers (pending/failed/transferring). The
    # Block A / Block B recovery paths below only iterate over PostProcessing
    # items, so a prematurely-Completed item with 500+ pending transfers
    # would otherwise sit forever — observed in production by a multi-file
    # torrent where the post-processing branch briefly saw `all_completed`
    # before the remaining transfers were created. Reset to PostProcessing
    # so Block A picks it up next tick.
    completed_with_pending = Item.objects.filter(
        status='Completed'
    ).filter(
        transfers__status__in=['pending', 'failed', 'transferring']
    ).distinct()
    for item in completed_with_pending:
        unfinished = item.transfers.filter(
            status__in=['pending', 'failed', 'transferring']
        ).count()
        logger.warning(
            f"Item marked Completed but has {unfinished} unfinished transfers; "
            f"resetting to PostProcessing for recovery"
        )
        item.status = 'PostProcessing'
        item.save()
        ItemHistory.objects.create(
            item=item,
            details=(
                f'Reset from Completed to PostProcessing: '
                f'{unfinished} unfinished transfers detected by check_stalled_transfers'
            ),
        )

    # Check for transferring transfers that are stalled
    # A transfer is stalled if:
    # 1. It's been transferring for 5+ minutes, AND
    # 2. bytes_transferred hasn't changed (modified time tracks this)
    transferring = FileTransfer.objects.filter(status='transferring')
    for transfer in transferring:
        # Check if transfer started more than 5 minutes ago
        if transfer.started and transfer.started < stall_threshold:
            # Check if any progress was made in the last 5 minutes
            if transfer.modified < stall_threshold:
                logger.warning(f"Stalled transfer detected: {transfer.filename} for item {transfer.item.name[:50]} - no progress for 5+ minutes")
                
                transfer.status = 'failed'
                transfer.error_message = 'Transfer stalled - no progress for 5+ minutes'
                transfer.save()
                
                ItemHistory.objects.create(
                    item=transfer.item,
                    details=f'Transfer stalled: {transfer.filename} - no progress for 5+ minutes'
                )
                stalled_count += 1
    
    # Check for items in PostProcessing with failed or pending transfers
    post_processing_items = Item.objects.filter(status='PostProcessing')
    for item in post_processing_items:
        item_transfers = FileTransfer.objects.filter(item=item)
        has_failed = any(t.status == 'failed' for t in item_transfers)
        has_pending = any(t.status == 'pending' for t in item_transfers)
        
        # If item has failed or pending transfers that are old, reset it
        if has_failed or has_pending:
            # For items with issues, check the oldest transfer's age
            oldest_transfer = item_transfers.order_by('modified').first()
            if oldest_transfer and oldest_transfer.modified < stall_threshold:
                logger.info(f"Item {item.name} in PostProcessing with failed/pending transfers, resetting to Grabbed for retry")
                # Delete old failed/pending transfers so they can be recreated fresh
                item_transfers.filter(status__in=['failed', 'pending']).delete()
                item.status = 'Grabbed'
                item.save()
                stalled_count += 1
    
    # Check for items in PostProcessing and handle appropriately
    # This handles cases where status was manually set to PostProcessing
    pp_items = Item.objects.filter(status='PostProcessing')
    for item in pp_items:
        transfers = FileTransfer.objects.filter(item=item)
        
        if not transfers.exists():
            # No transfers - queue a new transfer
            logger.info(f"Item {item.name} is PostProcessing but has no transfers, queuing transfer")
            try:
                transfer_files_async.delay(item.hash)
                logger.info(f"Successfully queued transfer for {item.name}")
            except Exception as e:
                logger.error(f"Failed to queue transfer for {item.name}: {e}")
        else:
            # Transfers exist - check their status
            has_failed = any(t.status == 'failed' for t in transfers)
            has_pending = any(t.status == 'pending' for t in transfers)
            all_completed = all(t.status == 'completed' for t in transfers)
            
            if has_failed or has_pending:
                # Has failed/pending transfers - requeue the transfer.
                # Cooldown guard: Block B fires every 20s on PostProcessing items
                # with unfinished transfers. Without this, the chain
                # `transfers.delete() -> transfer_files_async.delay()` races with
                # itself — by the time transfer_files_async has recreated the
                # transfers and started copying, the next Block B tick sees
                # the fresh pending/transferring rows and wipes them again.
                # Observed in production as a torrent stuck oscillating at the
                # cumulative byte count of one batch of transfers (25 GB of
                # ~120 GB total), forever. A 5-min cooldown matches
                # stall_threshold and gives transfer_files_async time to
                # actually create and process the full set before we consider
                # re-recovering.
                recent_requeue = ItemHistory.objects.filter(
                    item=item,
                    details__startswith='Requeued by check_stalled_transfers',
                    created__gte=timezone.now() - timedelta(minutes=5),
                ).exists()
                if recent_requeue:
                    logger.debug(
                        f"Skipping requeue for {item.name[:50]}: "
                        f"already requeued within the last 5 minutes"
                    )
                    continue
                logger.info(f"Item {item.name} has failed/pending transfers, requeueing transfer")
                # Preserve both completed AND transferring transfers:
                # - completed: real progress we don't want to redo
                # - transferring: workers are mid-flight; killing them
                #   loses bytes already copied and resets their
                #   bytes_transferred=0, causing Block B's 5-min cycle
                #   to oscillate the byte count (44 -> 45 -> 44)
                # Only delete pending (waiting to be transferred) and
                # failed (already tried max retries). transfer_files_async
                # skips completed via per-file dedup; in-flight transfers
                # finish on their own (or get marked failed by
                # check_stalled_transfers if truly stuck).
                transfers.filter(status__in=['pending', 'failed']).delete()
                try:
                    # Write the cooldown marker BEFORE .delay(), not after.
                    # Original code: .delay() then .create(). The create is
                    # the marker the next Block B tick checks for cooldown.
                    # When .delay() comes first, there's a race window where
                    # another Block B tick can fire between .delay() and
                    # .create() and see no marker, re-requeueing. That
                    # race produced 14+ parallel transfer_files_async
                    # tasks for the same item, each running concurrently
                    # and starving the celery workers of slots for
                    # everything else. Writing the marker first closes
                    # the race: the next Block B tick (5 min later) sees
                    # the marker and skips. If .delay() fails, the next
                    # tick also fires and retries since the marker is now
                    # older than 5 min.
                    ItemHistory.objects.create(
                        item=item,
                        details='Requeued by check_stalled_transfers: transfers deleted, transfer_files_async dispatched',
                    )
                    transfer_files_async.delay(item.hash)
                except Exception as e:
                    logger.error(f"Failed to requeue transfer for {item.name}: {e}")
            elif all_completed:
                # Check if post-processing already ran recently (within the last
                # 10 minutes) to avoid re-running every 20 seconds.
                # transfer_files_async leaves failed Bindery items in
                # PostProcessing with all-completed transfers; a fresh
                # attempt marker means the retry task owns recovery.
                recent_pp = ItemHistory.objects.filter(
                    item=item,
                    details__icontains='Post-processing',
                    created__gte=timezone.now() - timedelta(minutes=10)
                ).exists()
                
                if recent_pp:
                    logger.info(f"Item {item.name} already had post-processing initiated recently, skipping")
                    continue
                
                # All transfers completed - this might be a re-run of post-processing
                # Run the post-processing (extraction) now
                logger.info(f"Item {item.name} all transfers completed, running post-processing")
                try:
                    first_transfer = transfers.filter(status='completed').first()
                    if first_transfer and first_transfer.local_path:
                        local_folder = os.path.dirname(first_transfer.local_path)
                        
                        # Process ZIP archives FIRST
                        success_zip, msg_zip = process_zip_archives(local_folder, item)
                        logger.info(f"ZIP processing: {msg_zip}")
                        
                        # Process RAR archives SECOND
                        success_rar, msg_rar = process_rar_archives(local_folder, item)
                        logger.info(f"RAR processing: {msg_rar}")
                        
                        # Run manager post-processing (send to Whisparr/Sonarr/etc)
                        if item.manager:
                            client = item.manager.client
                            if hasattr(client, 'post_process'):
                                try:
                                    logger.info(f"Calling manager post-processing for {item.name}")
                                    success_pp, pp_message = client.post_process(item, local_folder)
                                    if success_pp:
                                        logger.info(f"Manager post-processing succeeded: {pp_message}")
                                    else:
                                        logger.warning(f"Manager post-processing failed: {pp_message}")
                                except Exception as e:
                                    logger.error(f"Error calling post-processing: {e}")
                        
                        # THEN move from temp to final folder for Blackhole
                        if item.manager and item.manager.managertype == 'Blackhole':
                            import re
                            sanitized_name = re.sub(r'[<>:"/\\|?*]', '', item.name).strip()
                            temp_base = item.manager.temp_folder if item.manager.temp_folder else '/tmp'
                            final_base = item.manager.folder.folder if item.manager.folder else '/tmp'
                            category = item.category or ''
                            if category:
                                final_base = os.path.join(final_base, category)
                            final_folder = os.path.join(final_base, sanitized_name)
                            temp_folder = os.path.join(temp_base, sanitized_name)
                            
                            # Move from temp to final if needed
                            if os.path.exists(temp_folder) and not os.path.exists(final_folder):
                                os.makedirs(final_base, exist_ok=True)
                                safe_rename(temp_folder, final_folder)
                                logger.info(f"Moved from temp to final: {final_folder}")
                        
                        # Mark as completed after post-processing
                        item.status = 'Completed'
                        item.save()
                        ItemHistory.objects.create(
                            item=item,
                            details='Post-processing (extraction) completed, marked as Completed'
                        )
                except Exception as e:
                    logger.error(f"Failed post-processing for {item.name}: {e}")
    
    if stalled_count > 0:
        logger.info(f"Detected and failed {stalled_count} stalled transfers")


@shared_task
def check_stalled_transfers():
    """Check for stalled transfers and restart them if they haven't progressed in 5+ minutes."""
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        from django.conf import settings
        if not getattr(settings, 'PIPELINE_HARDENING_ENABLED', True):
            # Legacy Block A + Block B path (pre-Phase-5). Preserved for one
            # release per the canary-deployment convention; setting the env-var
            # to false reverts to the old two-block recovery loop.
            _legacy_check_stalled_transfers()
            return

        # === Unified state machine (Phase 5) ===
        now = timezone.now()
        stall_threshold = now - timedelta(seconds=STALL_THRESHOLD_SECONDS)
        stalled_count = 0

        # Phase A: reset "Completed but not really" items to PostProcessing.
        # The recovery pass below only iterates over PostProcessing items, so a
        # prematurely-Completed item with unfinished transfers would otherwise
        # sit forever. Reset so the per-item recovery pass picks it up.
        completed_with_pending = Item.objects.filter(
            status='Completed'
        ).filter(
            transfers__status__in=['pending', 'failed', 'transferring']
        ).distinct()
        for item in completed_with_pending:
            unfinished = item.transfers.filter(
                status__in=['pending', 'failed', 'transferring']
            ).count()
            logger.warning(
                f"Item marked Completed but has {unfinished} unfinished transfers; "
                f"resetting to PostProcessing for recovery"
            )
            item.status = 'PostProcessing'
            item.save()
            ItemHistory.objects.create(
                item=item,
                details=(
                    f'Reset from Completed to PostProcessing: '
                    f'{unfinished} unfinished transfers detected by check_stalled_transfers'
                ),
            )

        # Phase A: mark stalled transferring transfers as failed. A transfer is
        # stalled if it started 5+ minutes ago AND made no progress since
        # (modified time tracks bytes_transferred changes).
        transferring = FileTransfer.objects.filter(status='transferring')
        for transfer in transferring:
            if transfer.started and transfer.started < stall_threshold:
                if transfer.modified < stall_threshold:
                    logger.warning(
                        f"Stalled transfer detected: {transfer.filename} for item "
                        f"{transfer.item.name[:50]} - no progress for 5+ minutes"
                    )
                    transfer.status = 'failed'
                    transfer.error_message = 'Transfer stalled - no progress for 5+ minutes'
                    transfer.save()
                    ItemHistory.objects.create(
                        item=transfer.item,
                        details=f'Transfer stalled: {transfer.filename} - no progress for 5+ minutes'
                    )
                    stalled_count += 1

        # Phase B: per-item recovery decision for PostProcessing items whose
        # last_recovery_at is older than the 60s cooldown (or never set).
        # Item.last_recovery_at is the single source of truth for the cooldown
        # (PIPE-03) — the ItemHistory prefix-scan is gone.
        candidates = Item.objects.filter(status='PostProcessing').filter(
            Q(last_recovery_at__isnull=True) |
            Q(last_recovery_at__lt=now - timedelta(seconds=RECOVERY_COOLDOWN_SECONDS))
        )
        for item in candidates:
            with transaction.atomic():
                if _recover_one_item(item, now):
                    # Write the cooldown marker BEFORE .delay() (race fix from
                    # c41ea55). .update() runs at the SQL level, so the write is
                    # committed the moment the statement returns; a concurrent
                    # tick sees the marker and skips until the cooldown elapses.
                    Item.objects.filter(hash=item.hash).update(last_recovery_at=now)
                    ItemHistory.objects.create(
                        item=item,
                        details='Requeued by check_stalled_transfers: transfers deleted, transfer_files_async dispatched',
                    )
                    # Preserve completed AND transferring transfers (cb3c5d9):
                    # completed is real progress; transferring rows are in-flight
                    # workers whose bytes_transferred would reset to 0.
                    transfers = FileTransfer.objects.filter(item=item)
                    transfers.filter(status__in=['pending', 'failed']).delete()
                    transfer_files_async.delay(item.hash)

        if stalled_count > 0:
            logger.info(f"Detected and failed {stalled_count} stalled transfers")
    except (OperationalError, InterfaceError, DatabaseError):
        # DB unavailable / pool exhausted / connection dropped. Beat will retry
        # this task in 20s — logging WARNING (not ERROR) makes the
        # "infrastructure blip, will retry" case visually distinct from the
        # "real bug, needs human eyes" case below. exc_info=True gives us the
        # stack trace so an operator can still tell whether it's the
        # connection-refused dance or a SQL syntax error masked as
        # OperationalError.
        logger.warning(
            "check_stalled_transfers: database unavailable; "
            "skipping this tick (beat will retry in 20s)",
            exc_info=True,
        )
    except Exception:
        # Real bug we did not anticipate — keep the function failing loudly
        # so the operator sees a stack trace in the worker log instead of a
        # lone one-line error.
        logger.exception("check_stalled_transfers: unexpected error")


@shared_task
def retry_postprocessing(item_hash, attempt=1):
    """Retry post-processing for an item that failed.

    PIPE-01: cap at RETRY_CAP_ATTEMPTS (3). The 4th invocation is a no-op.
    PIPE-08: deterministic task_id=f"retry-{item_hash}-{attempt}" enables
    broker-side dedup on re-delivery.
    PIPE-03: cooldown gate via Item.last_recovery_at (60s) — same source as
    check_stalled_transfers.
    """
    from django.conf import settings

    if not getattr(settings, 'PIPELINE_HARDENING_ENABLED', True):
        # Legacy path (pre-Phase-5): no attempt cap, no deterministic
        # task_id, no last_recovery_at cooldown. Preserved for one release
        # per the canary-deployment convention; setting the env-var to false
        # reverts to this body without code changes.
        _legacy_retry_postprocessing(item_hash)
        return

    try:
        item = Item.objects.get(hash=item_hash)
    except Item.DoesNotExist:
        logger.error(f"Item {item_hash} not found for retry_postprocessing")
        return

    # PIPE-01: hard cap at RETRY_CAP_ATTEMPTS. Enforced two ways: (a) the
    # `attempt` argument carried by the deterministic task_id, and (b) the
    # persisted Item.attempt_count column (survives worker restarts). The
    # 4th invocation is a no-op.
    if attempt > RETRY_CAP_ATTEMPTS or item.attempt_count >= RETRY_CAP_ATTEMPTS:
        logger.warning(f"[retry_postprocessing] Item {item_hash[:16]} hit PIPE-01 cap at attempt {attempt}; marking Failed")
        Item.objects.filter(hash=item_hash).update(status='Failed')
        return

    # PIPE-03: cooldown gate via Item.last_recovery_at (same source as
    # check_stalled_transfers). Items recovered within the last 60s are
    # skipped — the deterministic task_id is the broker-side dedup, this
    # check is the application-side safety net for redelivered task_ids.
    if item.last_recovery_at and item.last_recovery_at > timezone.now() - timedelta(seconds=RECOVERY_COOLDOWN_SECONDS):
        logger.debug(f"[retry_postprocessing] Item {item_hash[:16]} recovered within 60s; skipping")
        return

    # Retry whenever post-processing has something to re-attempt. Arr managers
    # land in Completed even after a failed post-process; Bindery items stay
    # PostProcessing (or may be flipped to Failed by the manager poll). Only
    # skip when there's nothing to retry or the item is in a terminal state.
    if item.status in ('Grabbed', 'Archived', 'Deleted'):
        logger.debug(f"Skipping retry for {item.name} - status is {item.status}")
        return

    if not item.manager or not hasattr(item.manager, 'client'):
        logger.error(f"Item {item.name} has no manager configured")
        return

    try:
        # Get local folder from latest completed transfer
        first_transfer = FileTransfer.objects.filter(item=item, status='completed').first()
        if not first_transfer or not first_transfer.local_path:
            logger.warning(f"No completed transfers found for {item.name}, cannot retry post-processing")
            return

        local_folder = os.path.dirname(first_transfer.local_path)

        # Construct the download path with item folder name included
        sanitized_item_name = re.sub(r'[<>:"/\\|?*]', '', item.name)
        sanitized_item_name = sanitized_item_name.strip()

        # Get base folder
        if item.manager.folder:
            if item.manager.folder.remote_folder_name:
                base_remote_path = item.manager.folder.remote_folder_name
            else:
                base_remote_path = item.manager.folder.folder
            download_path = os.path.join(base_remote_path, sanitized_item_name)
        else:
            download_path = local_folder

        # Check if there's only one video file in the directory
        video_extensions = ['.mkv', '.mp4', '.avi', '.mov', '.flv', '.wmv', '.webm']
        try:
            local_item_path = os.path.join(local_folder, sanitized_item_name)
            if os.path.isdir(local_item_path):
                video_files = [f for f in os.listdir(local_item_path)
                             if os.path.isfile(os.path.join(local_item_path, f))
                             and os.path.splitext(f)[1].lower() in video_extensions]
                if len(video_files) == 1:
                    video_file = video_files[0]
                    download_path = os.path.join(download_path, video_file)
                    logger.info(f"Single video file detected: {video_file}, using file path for post-processing")
        except Exception as e:
            logger.warning(f"Could not check for single video file: {e}")

        # Only call post_process if the manager supports it
        client = item.manager.client
        if hasattr(client, 'post_process'):
            logger.info(f"Retrying post-processing for {item.name} at path: {download_path}")
            try:
                success, pp_message = client.post_process(item, download_path)

                if success:
                    logger.info(f"Retry post-processing succeeded for {item.name}: {pp_message}")
                    ItemHistory.objects.create(item=item, details=f'Post-processing retry succeeded: {pp_message}')
                else:
                    logger.error(f"Retry post-processing failed for {item.name}: {pp_message}")
                    ItemHistory.objects.create(item=item, details=f'Post-processing retry failed: {pp_message}')
                    # PIPE-01: cap the retry chain at RETRY_CAP_ATTEMPTS.
                    next_attempt = attempt + 1
                    if next_attempt > RETRY_CAP_ATTEMPTS:
                        logger.warning(f"[retry_postprocessing] Item {item_hash[:16]} hit PIPE-01 cap; marking Failed")
                        Item.objects.filter(hash=item_hash).update(status='Failed')
                        return
                    # Write attempt_count + last_recovery_at BEFORE apply_async
                    # (Pitfall 2 race fix — the next worker that reads sees the
                    # new value; the deterministic task_id is the broker-side dedup).
                    Item.objects.filter(hash=item_hash).update(
                        attempt_count=next_attempt,
                        last_recovery_at=timezone.now(),
                    )
                    # PIPE-08: deterministic task_id enables broker-side dedup.
                    logger.info(f"Scheduling another retry for {item.name} in 5 minutes (attempt {next_attempt})")
                    retry_postprocessing.apply_async(
                        args=[item_hash, next_attempt],
                        task_id=f"retry-{item_hash}-{next_attempt}",
                        countdown=300,
                    )
            except Exception as e:
                logger.error(f"Error in retry post-processing: {e}")
                ItemHistory.objects.create(item=item, details=f'Retry error: {str(e)}')

    except Exception as e:
        logger.error(f"Error in retry_postprocessing for {item_hash}: {e}")
        ItemHistory.objects.create(item=item, details=f'Retry failed with error: {str(e)}')


def _legacy_retry_postprocessing(item_hash):
    """Legacy retry_postprocessing body (pre-Phase-5).

    Preserved under the PIPELINE_HARDENING_ENABLED=False branch for one
    release per the canary-deployment convention. No attempt cap, no
    deterministic task_id, no last_recovery_at cooldown.
    """
    try:
        item = Item.objects.get(hash=item_hash)
    except Item.DoesNotExist:
        logger.error(f"Item {item_hash} not found for retry_postprocessing")
        return

    # Retry whenever post-processing has something to re-attempt. Arr managers
    # land in Completed even after a failed post-process; Bindery items stay
    # PostProcessing (or may be flipped to Failed by the manager poll). Only
    # skip when there's nothing to retry or the item is in a terminal state.
    if item.status in ('Grabbed', 'Archived', 'Deleted'):
        logger.debug(f"Skipping retry for {item.name} - status is {item.status}")
        return

    if not item.manager or not hasattr(item.manager, 'client'):
        logger.error(f"Item {item.name} has no manager configured")
        return

    try:
        # Get local folder from latest completed transfer
        first_transfer = FileTransfer.objects.filter(item=item, status='completed').first()
        if not first_transfer or not first_transfer.local_path:
            logger.warning(f"No completed transfers found for {item.name}, cannot retry post-processing")
            return

        local_folder = os.path.dirname(first_transfer.local_path)

        # Construct the download path with item folder name included
        sanitized_item_name = re.sub(r'[<>:"/\\|?*]', '', item.name)
        sanitized_item_name = sanitized_item_name.strip()

        # Get base folder
        if item.manager.folder:
            if item.manager.folder.remote_folder_name:
                base_remote_path = item.manager.folder.remote_folder_name
            else:
                base_remote_path = item.manager.folder.folder
            download_path = os.path.join(base_remote_path, sanitized_item_name)
        else:
            download_path = local_folder

        # Check if there's only one video file in the directory
        video_extensions = ['.mkv', '.mp4', '.avi', '.mov', '.flv', '.wmv', '.webm']
        try:
            local_item_path = os.path.join(local_folder, sanitized_item_name)
            if os.path.isdir(local_item_path):
                video_files = [f for f in os.listdir(local_item_path)
                             if os.path.isfile(os.path.join(local_item_path, f))
                             and os.path.splitext(f)[1].lower() in video_extensions]
                if len(video_files) == 1:
                    video_file = video_files[0]
                    download_path = os.path.join(download_path, video_file)
                    logger.info(f"Single video file detected: {video_file}, using file path for post-processing")
        except Exception as e:
            logger.warning(f"Could not check for single video file: {e}")

        # Only call post_process if the manager supports it
        client = item.manager.client
        if hasattr(client, 'post_process'):
            logger.info(f"Retrying post-processing for {item.name} at path: {download_path}")
            try:
                success, pp_message = client.post_process(item, download_path)

                if success:
                    logger.info(f"Retry post-processing succeeded for {item.name}: {pp_message}")
                    ItemHistory.objects.create(item=item, details=f'Post-processing retry succeeded: {pp_message}')
                else:
                    logger.error(f"Retry post-processing failed for {item.name}: {pp_message}")
                    ItemHistory.objects.create(item=item, details=f'Post-processing retry failed: {pp_message}')
                    # Schedule another retry in 10 minutes
                    logger.info(f"Scheduling another retry for {item.name} in 10 minutes")
                    retry_postprocessing.apply_async(args=[item_hash], countdown=600)
            except Exception as e:
                logger.error(f"Error in retry post-processing: {e}")
                ItemHistory.objects.create(item=item, details=f'Retry error: {str(e)}')

    except Exception as e:
        logger.error(f"Error in retry_postprocessing for {item_hash}: {e}")
        ItemHistory.objects.create(item=item, details=f'Retry failed with error: {str(e)}')


@shared_task
def check_downloader_failures():
    """Check downloader for failed downloads and report them to manager.
    
    This catches failures that the manager might not have reported,
    allowing the manager to search for alternative releases.
    """
    from entities.models import Downloader
    
    for downloader in Downloader.objects.all():
        if downloader.downloadertype != 'SABNzbd':
            continue  # Only checking SABnzbd for now
        
        try:
            client = downloader.client
            client._ensure_client()
            
            # Get failed downloads from SABnzbd
            history_result = client._api_call('history', {'limit': 500})
            if 'history' not in history_result:
                continue
            
            slots = history_result['history'].get('slots', [])
            
            for slot in slots:
                if slot.get('status') != 'Failed':
                    continue
                
                nzb_name = slot.get('nzb_name', '')
                fail_message = slot.get('fail_message', 'Download failed')
                
                if not nzb_name:
                    continue
                
                # Try to find matching item in Harpoon by name
                # Use icontains since names might have slight variations
                matching_items = Item.objects.filter(
                    name__icontains=nzb_name[:30],  # Match first 30 chars
                    status__in=['Grabbed', 'PostProcessing'],  # Only care about active items
                    manager__isnull=False
                )
                
                for item in matching_items:
                    if item.status in ['Failed', 'Completed']:
                        continue  # Already handled
                    
                    # Found a match - mark as failed and notify manager
                    logger.warning(f"Detected downloader failure for {item.name}: {fail_message}")
                    
                    item.status = 'Failed'
                    item.save()
                    
                    ItemHistory.objects.create(
                        item=item,
                        details=f'Downloader failure detected ({downloader.name}): {fail_message}'
                    )
                    
                    # Notify manager to search for alternative
                    if item.manager:
                        try:
                            manager_client = item.manager.client
                            reject_success, reject_msg = manager_client.reject_download(
                                item,
                                f"Download failed on {downloader.name}: {fail_message}"
                            )
                            ItemHistory.objects.create(
                                item=item,
                                details=f'Notified manager to search for alternative: {reject_msg}'
                            )
                            logger.info(f"Notified {item.manager.name} to search for alternative")
                        except Exception as e:
                            logger.error(f"Failed to notify manager: {e}")
                            ItemHistory.objects.create(
                                item=item,
                                details=f'Could not notify manager: {str(e)}'
                            )
        
        except Exception as e:
            logger.error(f"Error checking {downloader.name} for failures: {e}")
