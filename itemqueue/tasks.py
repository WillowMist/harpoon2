from celery import shared_task
from itemqueue.models import Item, ItemHistory, FileTransfer
from entities.models import Downloader
import logging
import os
import shutil
import paramiko
import django.utils.timezone
import subprocess
import glob

logger = logging.getLogger(__name__)


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


@shared_task(time_limit=3600, soft_time_limit=3300)
def transfer_files_async(item_hash):
    """Async task to transfer files from seedbox to local storage.
    
    This runs in the background and can take a long time for large files.
    Creates all FileTransfer records UPFRONT, then transfers them.
    """
    try:
        item = Item.objects.get(hash=item_hash)
    except Item.DoesNotExist:
        logger.error(f"Item {item_hash} not found for async transfer")
        return
    
    if not item.downloader or not item.downloader.seedbox:
        logger.error(f"Item {item_hash} has no downloader/seedbox")
        return
    
    downloader = item.downloader
    seedbox = downloader.seedbox
    
    try:
        # Get the download path from the downloader
        client = downloader.client
        client._ensure_client()
        hash_value = item.hash
        
        # Get torrent/download info to know what files to copy
        if downloader.downloadertype == 'RTorrent':
            torrent_info = client.find(hash_value)
            if not torrent_info:
                return
            
            remote_dir = torrent_info.get('directory', '')
            torrent_name = torrent_info.get('name', '')
            
            if not remote_dir:
                return
            
            # For RTorrent, we copy all files from the directory
            files_to_copy = None
            
        elif downloader.downloadertype == 'SABNzbd':
            status_info = client.get_status(hash_value)
            if not status_info or not status_info.get('completed'):
                return
            
            storage_path = status_info.get('storage', '')
            if not storage_path:
                return
            
            remote_dir = storage_path
            files_to_copy = None
        else:
            return
        
        # Connect to seedbox via SFTP
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        if seedbox.auth_type == 'password':
            ssh.connect(seedbox.host, port=seedbox.port, username=seedbox.username, password=seedbox.password, timeout=10)
        else:
            pkey = paramiko.RSAKey.from_private_key_string(seedbox.ssh_key)
            ssh.connect(seedbox.host, port=seedbox.port, username=seedbox.username, pkey=pkey, timeout=10)
        
        sftp = ssh.open_sftp()
        sftp.get_channel().settimeout(60)
        
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
            base_folder = item.manager.folder.folder
        else:
            base_folder = '/tmp'
        
        # Create a subfolder for this item using a sanitized name
        import re
        sanitized_name = re.sub(r'[<>:"/\\|?*]', '', item.name)
        sanitized_name = sanitized_name.strip()
        item_folder = os.path.join(base_folder, sanitized_name)
        
        os.makedirs(item_folder, exist_ok=True)
        logger.info(f"Created folder for item: {item_folder}")
        
        # Get list of files to copy
        try:
            remote_files = sftp.listdir(remote_dir)
        except Exception as e:
            logger.error(f"Cannot access remote directory {remote_dir}: {e}")
            sftp.close()
            ssh.close()
            return
        
        # Build transfer list by recursively traversing directories
        # Preserve folder structure in the destination
        transfer_list = []  # List of (remote_path, relative_path) tuples
        import stat as stat_module_list
        
        def walk_remote_sftp(sftp_obj, remote_path, base_remote_dir, relative_prefix=''):
            """Recursively walk remote directory and collect files while preserving structure."""
            try:
                remote_items = sftp_obj.listdir(remote_path)
            except Exception as e:
                logger.warning(f"Cannot access remote directory {remote_path}: {e}")
                return
            
            for item_name in remote_items:
                remote_item_path = os.path.join(remote_path, item_name)
                relative_item_path = os.path.join(relative_prefix, item_name) if relative_prefix else item_name
                
                # Skip hidden files, images, HTML
                if item_name.startswith('.') or item_name.endswith('.jpg') or item_name.endswith('.html'):
                    continue
                
                # Check if it's a directory or file
                try:
                    item_stat = sftp_obj.stat(remote_item_path)
                    if stat_module_list.S_ISDIR(item_stat.st_mode):
                        # Recursively walk subdirectories
                        walk_remote_sftp(sftp_obj, remote_item_path, base_remote_dir, relative_item_path)
                    else:
                        # It's a file, add to transfer list
                        transfer_list.append((remote_item_path, relative_item_path))
                except Exception as e:
                    logger.warning(f"Cannot stat {remote_item_path}: {e}")
                    continue
        
        walk_remote_sftp(sftp, remote_dir, remote_dir)
        logger.info(f"Found {len(transfer_list)} files to transfer (including nested directories)")
        
        # STEP 1: Create FileTransfer records UPFRONT for ALL files
        # This ensures the dashboard shows correct total size from the start
        # transfer_list now contains (remote_path, relative_path) tuples
        transfer_records = {}
        skipped_count = 0
        
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
            
            # Check if file exists locally and verify file size
            if os.path.exists(local_path):
                local_size = os.path.getsize(local_path)
                if local_size == file_size:
                    # File exists and has correct size - skip it
                    logger.info(f"Skipped (complete): {local_path} ({local_size / (1024*1024):.1f}MB)")
                    skipped_count += 1
                    continue
                else:
                    # File exists but size mismatch - delete and retransfer
                    logger.warning(f"File size mismatch for {local_path}: local={local_size}, remote={file_size}. Deleting and retransferring.")
                    try:
                        os.remove(local_path)
                    except Exception as e:
                        logger.error(f"Failed to delete incomplete file {local_path}: {e}")
                        continue
            
            # Create FileTransfer record with 'pending' status
            try:
                transfer = FileTransfer.objects.create(
                    item=item,
                    filename=relative_path,  # Store relative path for display
                    remote_path=remote_file_path,
                    local_path=local_path,
                    file_size=file_size,
                    status='pending'
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
                    
                    # Download file with progress tracking
                    sftp.get(remote_file_path, local_path, callback=progress_callback)
                    
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
                        failed_count += 1
                        transfer_successful = True  # Exit retry loop
        
        sftp.close()
        ssh.close()
        
        logger.info(f"Async transfer complete for {item.name} ({copied_count} files)")
        ItemHistory.objects.create(item=item, details=f'Async file transfer complete ({copied_count} files)')
        
        # Post-transfer processing: extract RAR archives if present
        if copied_count > 0:
            try:
                first_transfer = FileTransfer.objects.filter(item=item, status='completed').first()
                if first_transfer and first_transfer.local_path:
                    local_folder = os.path.dirname(first_transfer.local_path)
                    logger.info(f"Processing RAR archives in: {local_folder}")
                    
                    success, message = process_rar_archives(local_folder, item)
                    if not success:
                        logger.warning(f"RAR processing encountered issues: {message}")
                    else:
                        logger.info(f"RAR processing completed: {message}")
            except Exception as e:
                logger.error(f"Error during RAR archive processing: {e}")
                ItemHistory.objects.create(item=item, details=f'RAR processing error: {str(e)}')
        
    except Exception as e:
        logger.error(f"Error in async file transfer {item_hash}: {e}")
        ItemHistory.objects.create(item=item, details=f'Async transfer failed: {str(e)}')


@shared_task
def postprocess_item(item_hash):
    """Post-process a completed download: mark as completed and queue async file transfer."""
    try:
        item = Item.objects.get(hash=item_hash)
    except Item.DoesNotExist:
        logger.error(f"Item {item_hash} not found")
        return
    
    if not item.downloader:
        ItemHistory.objects.create(item=item, details='No downloader assigned')
        item.status = 'Failed'
        item.save()
        return
    
    downloader = item.downloader
    seedbox = downloader.seedbox
    
    if not seedbox:
        ItemHistory.objects.create(item=item, details='No seedbox configured for downloader')
        item.status = 'Failed'
        item.save()
        return
    
    try:
        client = downloader.client
        client._ensure_client()
        hash_value = item.hash
        
        if downloader.downloadertype == 'RTorrent':
            torrent_info = client.find(hash_value)
            if not torrent_info:
                ItemHistory.objects.create(item=item, details='Torrent not found')
                item.status = 'Failed'
                item.save()
                return
            
            if not torrent_info.get('completed'):
                ItemHistory.objects.create(item=item, details='Torrent not complete')
                item.status = 'Failed'
                item.save()
                return
                
        elif downloader.downloadertype == 'SABNzbd':
            status_info = client.get_status(hash_value)
            if not status_info or not status_info.get('completed'):
                ItemHistory.objects.create(item=item, details='Download not complete')
                item.status = 'Failed'
                item.save()
                return
        else:
            ItemHistory.objects.create(item=item, details='Unknown downloader type')
            item.status = 'Failed'
            item.save()
            return
        
        item.status = 'Completed'
        item.save()
        ItemHistory.objects.create(item=item, details='Download complete, queuing file transfer')
        logger.info(f"Item {item.name} marked completed")
        
        try:
            logger.info(f"Queuing async file transfer for {item.name}")
            transfer_files_async.apply_async(args=[item_hash], countdown=5)
            ItemHistory.objects.create(item=item, details='Queued async file transfer')
        except Exception as e:
            logger.warning(f"Could not queue async transfer (will retry later): {e}")
            ItemHistory.objects.create(item=item, details=f'File transfer queued (async): {str(e)}')
        
    except Exception as e:
        logger.error(f"Error post-processing {item_hash}: {e}")
        ItemHistory.objects.create(item=item, details=f'Post-processing failed: {str(e)}')
        item.status = 'Failed'
        item.save()


@shared_task
def check_downloaders():
    """Check all configured downloaders for completed downloads."""
    downloaders = Downloader.objects.all()
    for downloader in downloaders:
        try:
            client = downloader.client
            client._ensure_client()
            
            if downloader.downloadertype == 'RTorrent':
                # Check for completed torrents
                completed = client.get_completed()
                for torrent_info in completed:
                    hash_value = torrent_info.get('hash')
                    try:
                        item = Item.objects.get(hash=hash_value)
                        if item.status != 'Completed' and item.status != 'Failed':
                            postprocess_item.delay(hash_value)
                    except Item.DoesNotExist:
                        pass
                        
            elif downloader.downloadertype == 'SABNzbd':
                # Check for completed and failed downloads
                # NOTE: This only tracks failures for NZO IDs that exist in our database.
                # If the manager re-grabs with a new NZO ID after a failure, we won't know
                # about it unless the manager reports the failure event. Ideally, the manager
                # should report all failures via downloadFailed events.
                client._ensure_client()
                history_result = client._api_call('history', {'limit': 500})
                if 'history' in history_result:
                    for item_info in history_result['history'].get('slots', []):
                        nzo_id = item_info.get('nzo_id')
                        status = item_info.get('status', '')
                        
                        try:
                            item = Item.objects.get(hash=nzo_id)
                            
                            if status == 'Completed':
                                if item.status != 'Completed' and item.status != 'Failed':
                                    postprocess_item.delay(nzo_id)
                            
                            elif status == 'Failed':
                                # Download failed on downloader - mark as failed if not already
                                if item.status != 'Failed':
                                    fail_message = item_info.get('fail_message', 'Download failed on downloader')
                                    item.status = 'Failed'
                                    item.save()
                                    ItemHistory.objects.create(
                                        item=item,
                                        details=f'Download failed on {downloader.name}: {fail_message}'
                                    )
                                    logger.warning(f"Marked {item.name} as failed: {fail_message}")
                        
                        except Item.DoesNotExist:
                            pass
        except Exception as e:
            logger.error(f"Error checking downloader {downloader.name}: {e}")


@shared_task
def check_stalled_transfers():
    """Check for stalled transfers and restart them if they haven't progressed in 5+ minutes."""
    from django.utils import timezone
    from datetime import timedelta
    
    transferring = FileTransfer.objects.filter(status='transferring')
    stall_threshold = timezone.now() - timedelta(minutes=5)
    stalled_count = 0
    
    for transfer in transferring:
        if transfer.modified < stall_threshold:
            logger.warning(f"Stalled transfer detected: {transfer.filename} for item {transfer.item.name[:50]}")
            
            transfer.status = 'failed'
            transfer.error_message = 'Transfer stalled - no progress for 5+ minutes'
            transfer.save()
            stalled_count += 1
            
            ItemHistory.objects.create(
                item=transfer.item,
                details=f'Stalled transfer detected and failed: {transfer.filename}'
            )
            
            item = transfer.item
            item_transfers = FileTransfer.objects.filter(item=item)
            all_failed = all(t.status in ['failed', 'completed'] for t in item_transfers)
            
            if all_failed and item.status == 'Completed':
                logger.info(f"All transfers failed for {item.name}, resetting to Grabbed for retry")
                item.status = 'Grabbed'
                item.save()
                
                ItemHistory.objects.create(
                    item=item,
                    details='All transfers failed/stalled, resetting for retry'
                )
    
    if stalled_count > 0:
        logger.info(f"Detected and failed {stalled_count} stalled transfers")
