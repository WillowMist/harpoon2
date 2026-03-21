from django.shortcuts import get_object_or_404, render, redirect
from django.contrib import messages
from django.contrib.sessions.backends.db import SessionStore
from django.utils.dateparse import parse_datetime
from django.http import JsonResponse
from django.db.models import Prefetch, Count, Q
from django.contrib.auth.decorators import login_required
from django.contrib.auth import authenticate, login, get_user_model
from django.views.decorators.csrf import csrf_protect
from entities.models import Manager, Downloader, CachedDownloaderStatus
from itemqueue.models import Item, FileTransfer, ItemHistory
import requests

User = get_user_model()


@csrf_protect
def login_view(request):
    """Custom login view that shows registration form if no superuser exists."""
    if request.user.is_authenticated:
        return redirect('home')
    
    try:
        superuser_count = User.objects.filter(is_superuser=True).count()
        no_superuser = superuser_count == 0
    except Exception:
        no_superuser = True
    
    if request.method == 'POST':
        if no_superuser:
            username = request.POST.get('username', '').strip()
            password = request.POST.get('password', '')
            password2 = request.POST.get('password2', '')
            
            if not username or not password:
                messages.error(request, 'Username and password are required.')
                return render(request, 'registration/login.html', {
                    'form': None,
                    'no_superuser': True,
                })
            
            if password != password2:
                messages.error(request, 'Passwords do not match.')
                return render(request, 'registration/login.html', {
                    'form': None,
                    'no_superuser': True,
                })
            
            if len(password) < 8:
                messages.error(request, 'Password must be at least 8 characters.')
                return render(request, 'registration/login.html', {
                    'form': None,
                    'no_superuser': True,
                })
            
            user = User.objects.create_superuser(username=username, password=password)
            user = authenticate(request, username=username, password=password)
            if user:
                login(request, user)
                messages.success(request, f'Welcome, {user.username}! Superuser account created.')
                return redirect('home')
        else:
            username = request.POST.get('username', '')
            password = request.POST.get('password', '')
            user = authenticate(request, username=username, password=password)
            if user:
                login(request, user)
                messages.success(request, f'Welcome back, {user.username}!')
                next_url = request.GET.get('next', 'home')
                return redirect(next_url)
            else:
                messages.error(request, 'Invalid username or password.')
    
    return render(request, 'registration/login.html', {
        'form': None,
        'no_superuser': no_superuser,
    })


@login_required
def home(request):
    """Dashboard - shows active downloads (grabbing), file transfers, and quick summary by manager."""
    managers = Manager.objects.all()
    
    # Get counts by manager (exclude archived items)
    manager_summary = []
    for m in managers:
        grabbing = Item.objects.filter(status='Grabbed', manager=m, archived=False).count()
        postprocessing = Item.objects.filter(status='PostProcessing', manager=m, archived=False).count()
        completed = Item.objects.filter(status='Completed', manager=m, archived=False).count()
        failed = Item.objects.filter(status='Failed', manager=m, archived=False).count()
        manager_summary.append({
            'name': m.name,
            'grabbing': grabbing,
            'postprocessing': postprocessing,
            'completed': completed,
            'failed': failed,
            'total': grabbing + postprocessing,
        })
    
    # Get active download info from cached downloader status (fast!)
    grabbing_downloads = []
    for cache in CachedDownloaderStatus.objects.select_related('downloader').all():
        for torrent in cache.active_downloads[:10]:
            grabbing_downloads.append({
                'name': torrent.get('name', ''),
                'hash': torrent.get('hash', ''),
                'size': torrent.get('size', 0),
                'completed': torrent.get('completed', 0),
                'percent': torrent.get('percent', 0),
                'downloader': cache.downloader.name,
                'status': 'Grabbing',
            })
    
    # Get active SFTP file transfers - aggregate by item for total progress
    # Include completed files to show total size of entire transfer operation
    from django.utils import timezone
    from datetime import timedelta
    
    active_transfers = FileTransfer.objects.filter(status__in=['pending', 'transferring', 'completed']).select_related('item')
    
    # CRITICAL: Pre-calculate total sizes for each item BEFORE the loop
    # This ensures sizes remain constant even as new files are added during transfer
    item_total_sizes = {}
    for transfer in active_transfers:
        item_hash = transfer.item.hash
        if item_hash not in item_total_sizes:
            all_transfers_for_item = active_transfers.filter(item__hash=item_hash)
            item_total_sizes[item_hash] = sum(t.file_size for t in all_transfers_for_item)
    
    # Group transfers by item_hash and aggregate totals
    transfers_by_item = {}
    
    for transfer in active_transfers:
        item_hash = transfer.item.hash
        if item_hash not in transfers_by_item:
            transfers_by_item[item_hash] = {
                'item_name': transfer.item.name,
                'item': transfer.item,  # Store the item object
                'total_size': item_total_sizes[item_hash],  # Use pre-calculated size
                'total_completed': 0,
                'file_count': 0,
                'earliest_start': None,
                'latest_update': None,
            }
        
        transfers_by_item[item_hash]['total_completed'] += transfer.bytes_transferred
        transfers_by_item[item_hash]['file_count'] += 1
        
        # Track earliest start time for speed calculation
        if transfer.started:
            if transfers_by_item[item_hash]['earliest_start'] is None:
                transfers_by_item[item_hash]['earliest_start'] = transfer.started
            else:
                transfers_by_item[item_hash]['earliest_start'] = min(
                    transfers_by_item[item_hash]['earliest_start'],
                    transfer.started
                )
        
        # Track latest update time for speed calculation
        if transfer.modified:
            if transfers_by_item[item_hash]['latest_update'] is None:
                transfers_by_item[item_hash]['latest_update'] = transfer.modified
            else:
                transfers_by_item[item_hash]['latest_update'] = max(
                    transfers_by_item[item_hash]['latest_update'],
                    transfer.modified
                )
    
    # Convert to list format for template
    # Only show transfers that have pending or transferring files
    # Sort by active status first (items with active transfers come first)
    transfers_list = []
    total_speed_mbps = 0
    
    # Sort transfers_by_item to prioritize items with active (pending/transferring) transfers
    sorted_transfers = sorted(transfers_by_item.items(), 
        key=lambda x: (
            FileTransfer.objects.filter(
                item__hash=x[0],
                status__in=['pending', 'transferring']
            ).exists() == False,  # False sorts before True, so active items first
            -x[1]['total_completed']  # Then by most recently progressed
        )
    )
    
    for item_hash, data in sorted_transfers[:10]:  # Limit to 10 items
        # Check if this item has any pending/transferring transfers
        has_active = FileTransfer.objects.filter(
            item__hash=item_hash,
            status__in=['pending', 'transferring']
        ).exists()
        
        if not has_active:
            continue  # Skip items with no active transfers
        
        if data['total_size'] > 0:
            percent = int((data['total_completed'] / data['total_size']) * 100)
        else:
            percent = 0
        
        # Calculate speed using session-based delta for accurate transfer speed
        speed_mbps = 0
        prev_poll = request.session.get('transfer_poll_state', {})
        prev_item = prev_poll.get(item_hash)
        now = timezone.now()
        
        if prev_item and data['latest_update']:
            prev_completed = prev_item.get('completed', 0)
            prev_time_str = prev_item.get('timestamp')
            if prev_time_str:
                prev_time = parse_datetime(prev_time_str)
                if prev_time and data['total_completed'] > prev_completed:
                    delta_bytes = data['total_completed'] - prev_completed
                    delta_seconds = (now - prev_time).total_seconds()
                    if delta_seconds > 0:
                        speed_mbps = (delta_bytes / delta_seconds) / (1024 * 1024)
        
        if speed_mbps == 0 and data['earliest_start']:
            elapsed_seconds = (now - data['earliest_start']).total_seconds()
            if elapsed_seconds > 0 and data['total_completed'] > 0:
                speed_mbps = (data['total_completed'] / elapsed_seconds) / (1024 * 1024)
        
        if speed_mbps > 0:
            total_speed_mbps += speed_mbps
        
        transfer_info = {
            'name': data['item_name'],
            'item_name': data['item_name'],
            'size': data['total_size'],
            'completed': data['total_completed'],
            'percent': percent,
            'file_count': data['file_count'],
            'status': 'SFTP Transfer',
            'speed_mbps': speed_mbps,
            'extraction_status': data['item'].extraction_status if data['item'].extraction_status else None,
            'extraction_progress': data['item'].extraction_progress if data['item'].extraction_status else 0,
        }
        transfers_list.append(transfer_info)
    
    # Update session with current poll state for next poll's speed calculation
    poll_state = {}
    now = timezone.now()
    for item_hash, data in transfers_by_item.items():
        poll_state[item_hash] = {
            'completed': data['total_completed'],
            'timestamp': now.isoformat(),
        }
    request.session['transfer_poll_state'] = poll_state
    request.session.modified = True
    
    return render(request, 'home.html', {
        'managers': managers,
        'manager_summary': manager_summary,
        'grabbing_downloads': grabbing_downloads,
        'active_transfers': transfers_list,
        'total_queued': sum(m['total'] for m in manager_summary),
        'total_speed_mbps': total_speed_mbps,
    })


@login_required
def queue(request):
    """Queue page - shows all queued/grabbing/postprocessing items."""
    grabbing_items = Item.objects.filter(status='Grabbed', archived=False).select_related('manager', 'downloader').order_by('-created')
    postprocessing_items = Item.objects.filter(status='PostProcessing', archived=False).select_related('manager', 'downloader').order_by('-modified')
    
    return render(request, 'queue.html', {
        'grabbing_items': grabbing_items,
        'postprocessing_items': postprocessing_items,
    })


from django.db.models import Prefetch


@login_required
def history(request):
    """History page - shows completed and failed items."""
    show_archived = request.GET.get('show_archived', 'false').lower() == 'true'
    
    completed_items = list(Item.objects.filter(
        status='Completed', archived=show_archived
    ).select_related('manager', 'downloader').order_by(
        '-modified' if not show_archived else '-archived_at'
    )[:50])
    
    failed_items = list(Item.objects.filter(
        status='Failed', archived=show_archived
    ).select_related('manager', 'downloader').order_by(
        '-modified' if not show_archived else '-archived_at'
    )[:50])
    
    completed_count = len(completed_items)
    failed_count = len(failed_items)
    completed_archived_count = Item.objects.filter(status='Completed', archived=True).count()
    failed_archived_count = Item.objects.filter(status='Failed', archived=True).count()
    
    downloaders = Downloader.objects.all().order_by('name')
    
    return render(request, 'history.html', {
        'completed_items': completed_items,
        'failed_items': failed_items,
        'show_archived': show_archived,
        'completed_count': completed_count,
        'completed_archived_count': completed_archived_count,
        'failed_count': failed_count,
        'failed_archived_count': failed_archived_count,
        'downloaders': downloaders,
    })


def cancel_download(request, item_hash):
    """Cancel a download and mark it as failed."""
    if request.method == 'POST':
        try:
            item = Item.objects.get(hash=item_hash)
            
            # Mark item as failed
            old_status = item.status
            item.status = 'Failed'
            item.save()
            
            # Create history entry
            ItemHistory.objects.create(
                item=item,
                details=f'Download cancelled by user (was {old_status})'
            )
            
            # Send notification
            from users.models import Notification
            Notification.create_for_admin(
                f"Download cancelled by user: {item.name}",
                notification_type='downloader_failure',
                item_hash=item.hash
            )
            
            messages.success(request, f'Download cancelled: {item.name}')
        except Item.DoesNotExist:
            messages.error(request, 'Item not found')
        except Exception as e:
            messages.error(request, f'Error cancelling download: {str(e)}')
    
    return redirect('queue')


def cancel_transfer(request, item_name):
    """Cancel an SFTP transfer."""
    if request.method == 'POST':
        try:
            # Find the item by name
            item = Item.objects.get(name=item_name)
            
            # Mark all its transfers as cancelled/failed
            from itemqueue.models import FileTransfer
            transfers = FileTransfer.objects.filter(item=item, status__in=['pending', 'transferring'])
            transfer_count = transfers.count()
            transfers.update(status='failed')
            
            # Mark item as failed
            old_status = item.status
            item.status = 'Failed'
            item.save()
            
            # Create history entry
            ItemHistory.objects.create(
                item=item,
                details=f'Transfer cancelled by user - {transfer_count} file(s) cancelled (was {old_status})'
            )
            
            # Send notification
            from users.models import Notification
            Notification.create_for_admin(
                f"Transfer cancelled by user: {item.name}",
                notification_type='transfer_failure',
                item_hash=item.hash
            )
            
            return redirect('home')
        except Item.DoesNotExist:
            messages.error(request, 'Item not found')
            return redirect('home')
        except Exception as e:
            messages.error(request, f'Error cancelling transfer: {str(e)}')
            return redirect('home')
    
    return redirect('home')


def cancel_postprocessing(request, item_hash):
    """Cancel/reset a stuck PostProcessing item."""
    if request.method == 'POST':
        try:
            from itemqueue.models import FileTransfer
            item = Item.objects.get(hash=item_hash)
            
            if item.status != 'PostProcessing':
                messages.error(request, 'Only PostProcessing items can be cancelled this way')
                return redirect('queue')
            
            # Get action: 'failed' to mark as failed, 'completed' to force complete, 'retry' to requeue
            action = request.POST.get('action', 'failed')
            
            if action == 'failed':
                # Mark item as failed
                item.status = 'Failed'
                item.save()
                ItemHistory.objects.create(
                    item=item,
                    details='PostProcessing cancelled by user - marked as Failed'
                )
                Notification.create_for_admin(
                    f"PostProcessing cancelled by user: {item.name}",
                    notification_type='postprocess_failure',
                    item_hash=item.hash
                )
                messages.success(request, f'Item marked as Failed: {item.name}')
                
            elif action == 'completed':
                # Mark item as completed (manual override)
                item.status = 'Completed'
                item.save()
                ItemHistory.objects.create(
                    item=item,
                    details='PostProcessing manually marked as Completed by user'
                )
                Notification.create_for_admin(
                    f"PostProcessing manually completed: {item.name}",
                    notification_type='item_completed',
                    item_hash=item.hash
                )
                messages.success(request, f'Item marked as Completed: {item.name}')
                
            elif action == 'retry':
                # Reset to Grabbed to try again
                item.status = 'Grabbed'
                item.save()
                ItemHistory.objects.create(
                    item=item,
                    details='PostProcessing reset to Grabbed by user for retry'
                )
                messages.success(request, f'Item reset to Grabbed: {item.name}')
            
            return redirect('queue')
            
        except Item.DoesNotExist:
            messages.error(request, 'Item not found')
            return redirect('queue')
        except Exception as e:
            messages.error(request, f'Error cancelling PostProcessing: {str(e)}')
            return redirect('queue')
    
    return redirect('queue')


def retry_postprocessing_transfer(request, item_hash):
    """Retry a stuck PostProcessing item by re-queuing the transfer task."""
    if request.method == 'POST':
        try:
            from itemqueue.tasks import transfer_files_async
            item = Item.objects.get(hash=item_hash)
            
            if item.status != 'PostProcessing':
                messages.error(request, 'Only PostProcessing items can be retried this way')
                return redirect('queue')
            
            # Clear ALL existing transfers to restart fresh (including completed/failed)
            from itemqueue.models import FileTransfer
            FileTransfer.objects.filter(item=item).delete()
            
            # Queue the transfer task
            transfer_files_async.delay(item_hash)
            
            messages.success(request, f'Requeued transfer for: {item.name}')
            
        except Item.DoesNotExist:
            messages.error(request, 'Item not found')
        except Exception as e:
            messages.error(request, f'Error retrying transfer: {str(e)}')
    
    return redirect('queue')


def archive_item(request, item_hash):
    """Archive a single item."""
    if request.method == 'POST':
        try:
            item = Item.objects.get(hash=item_hash)
            
            # Archive the item
            from django.utils import timezone
            item.archived = True
            item.archived_at = timezone.now()
            item.save()
            
            messages.success(request, f'Item archived: {item.name}')
        except Item.DoesNotExist:
            messages.error(request, 'Item not found')
        except Exception as e:
            messages.error(request, f'Error archiving item: {str(e)}')
    
    return redirect('history')


def unarchive_item(request, item_hash):
    """Unarchive a single item."""
    if request.method == 'POST':
        try:
            item = Item.objects.get(hash=item_hash)
            
            # Unarchive the item
            item.archived = False
            item.archived_at = None
            item.save()
            
            messages.success(request, f'Item unarchived: {item.name}')
        except Item.DoesNotExist:
            messages.error(request, 'Item not found')
        except Exception as e:
            messages.error(request, f'Error unarchiving item: {str(e)}')
    
    return redirect('history')


def archive_all_failed(request):
    """Archive all failed items."""
    if request.method == 'POST':
        try:
            from django.utils import timezone
            failed_items = Item.objects.filter(status='Failed', archived=False)
            count = failed_items.count()
            
            failed_items.update(archived=True, archived_at=timezone.now())
            
            messages.success(request, f'Archived {count} failed item(s)')
        except Exception as e:
            messages.error(request, f'Error archiving failed items: {str(e)}')
    
    return redirect('history')


def archive_all_completed(request):
    """Archive all completed items."""
    if request.method == 'POST':
        try:
            from django.utils import timezone
            completed_items = Item.objects.filter(status='Completed', archived=False)
            count = completed_items.count()
             
            completed_items.update(archived=True, archived_at=timezone.now())
            
            messages.success(request, f'Archived {count} completed item(s)')
        except Exception as e:
            messages.error(request, f'Error archiving completed items: {str(e)}')
    
    return redirect('history')


def update_item_status(request, item_hash):
    """Update item status via dropdown."""
    if request.method == 'POST':
        new_status = request.POST.get('status', '').strip()
        
        # Validate status
        valid_statuses = ['Grabbed', 'PostProcessing', 'Completed', 'Failed']
        if new_status not in valid_statuses:
            messages.error(request, 'Invalid status')
            return redirect('history')
        
        try:
            item = Item.objects.get(hash=item_hash)
            old_status = item.status
            item.status = new_status
            item.save()
            
            ItemHistory.objects.create(
                item=item,
                details=f'Status changed by user: {old_status} → {new_status}'
            )
            
            messages.success(request, f'Status updated: {old_status} → {new_status}')
        except Item.DoesNotExist:
            messages.error(request, 'Item not found')
        except Exception as e:
            messages.error(request, f'Error updating status: {str(e)}')
    
    return redirect('history')


def update_item_downloader(request, item_hash):
    """Update item downloader via dropdown."""
    if request.method == 'POST':
        downloader_id = request.POST.get('downloader', '').strip()
        
        try:
            item = Item.objects.get(hash=item_hash)
            
            if downloader_id == '':
                old_dl = item.downloader.name if item.downloader else 'None'
                item.downloader = None
                new_dl = 'None'
            else:
                downloader = Downloader.objects.get(id=downloader_id)
                old_dl = item.downloader.name if item.downloader else 'None'
                item.downloader = downloader
                new_dl = downloader.name
            
            item.save()
            
            ItemHistory.objects.create(
                item=item,
                details=f'Downloader changed by user: {old_dl} → {new_dl}'
            )
            
            messages.success(request, f'Downloader updated: {old_dl} → {new_dl}')
        except Downloader.DoesNotExist:
            messages.error(request, 'Downloader not found')
        except Item.DoesNotExist:
            messages.error(request, 'Item not found')
        except Exception as e:
            messages.error(request, f'Error updating downloader: {str(e)}')
    
    return redirect('history')


def retry_failed_item(request, item_hash):
    """Retry a failed item - reset to Grabbed and clear transfers."""
    if request.method == 'POST':
        try:
            item = Item.objects.get(hash=item_hash)
            
            if item.status != 'Failed':
                messages.error(request, 'Only failed items can be retried')
                return redirect('history')
            
            # Delete all transfers for this item
            transfer_count = FileTransfer.objects.filter(item=item).count()
            FileTransfer.objects.filter(item=item).delete()
            
            # Reset to Grabbed status
            item.status = 'Grabbed'
            item.save()
            
            ItemHistory.objects.create(
                item=item,
                details=f'Retried by user - reset to Grabbed (deleted {transfer_count} transfers)'
            )
            
            messages.success(request, f'Item retried: reset to Grabbed status')
        except Item.DoesNotExist:
            messages.error(request, 'Item not found')
        except Exception as e:
            messages.error(request, f'Error retrying item: {str(e)}')
    
    return redirect('history')


def api_dashboard(request):
    """JSON API for dashboard data - used for AJAX polling."""
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        from django.http import JsonResponse
        from django.utils import timezone
        from datetime import timedelta
        
        managers = Manager.objects.all()
        
        # Get counts by manager
        manager_summary = []
        for m in managers:
            grabbing = Item.objects.filter(status='Grabbed', manager=m, archived=False).count()
            postprocessing = Item.objects.filter(status='PostProcessing', manager=m, archived=False).count()
            completed = Item.objects.filter(status='Completed', manager=m, archived=False).count()
            failed = Item.objects.filter(status='Failed', manager=m, archived=False).count()
            manager_summary.append({
                'name': m.name,
                'grabbing': grabbing,
                'postprocessing': postprocessing,
                'completed': completed,
                'failed': failed,
                'total': grabbing + postprocessing,
            })
        
        # Get active downloads from cache (fast!)
        grabbing_downloads = []
        for cache in CachedDownloaderStatus.objects.select_related('downloader').all():
            for torrent in cache.active_downloads[:10]:
                grabbing_downloads.append({
                    'name': torrent.get('name', ''),
                    'hash': torrent.get('hash', ''),
                    'size': torrent.get('size', 0),
                    'completed': torrent.get('completed', 0),
                    'percent': torrent.get('percent', 0),
                    'downloader': cache.downloader.name,
                    'status': 'Grabbing',
                })
        
        # Get active transfers
        active_transfers_query = FileTransfer.objects.filter(
            status__in=['pending', 'transferring', 'completed']
        ).select_related('item')
        
        # Track data for speed calculation
        transfers_by_item = {}
        item_total_sizes = {}
        
        # First pass: collect total sizes
        for transfer in active_transfers_query:
            if not transfer.item:
                continue
            item_hash = transfer.item.hash
            if item_hash not in item_total_sizes:
                all_transfers = active_transfers_query.filter(item__hash=item_hash)
                item_total_sizes[item_hash] = sum(t.file_size for t in all_transfers)
        
        # Second pass: collect transfer data with timing for speed calculation
        for transfer in active_transfers_query:
            if not transfer.item:
                continue
            item_hash = transfer.item.hash
            if item_hash not in transfers_by_item:
                transfers_by_item[item_hash] = {
                    'item_name': transfer.item.name,
                    'item': transfer.item,
                    'total_size': item_total_sizes.get(item_hash, 0),
                    'total_completed': 0,
                    'file_count': 0,
                    'earliest_start': None,
                    'latest_update': None,
                }
            
            transfers_by_item[item_hash]['total_completed'] += transfer.bytes_transferred
            transfers_by_item[item_hash]['file_count'] += 1
            
            # Track earliest start time for speed calculation
            if transfer.started:
                if transfers_by_item[item_hash]['earliest_start'] is None:
                    transfers_by_item[item_hash]['earliest_start'] = transfer.started
                else:
                    transfers_by_item[item_hash]['earliest_start'] = min(
                        transfers_by_item[item_hash]['earliest_start'],
                        transfer.started
                    )
            
            # Track latest update time for speed calculation
            if transfer.modified:
                if transfers_by_item[item_hash]['latest_update'] is None:
                    transfers_by_item[item_hash]['latest_update'] = transfer.modified
                else:
                    transfers_by_item[item_hash]['latest_update'] = max(
                        transfers_by_item[item_hash]['latest_update'],
                        transfer.modified
                    )
        
        active_transfers = []
        total_speed_mbps = 0
        now = timezone.now()
        
        for item_hash, data in transfers_by_item.items():
            if data['total_completed'] < data['total_size']:
                percent = int((data['total_completed'] / data['total_size']) * 100) if data['total_size'] > 0 else 0
                
                # Calculate speed using session-based delta for accurate transfer speed
                speed_mbps = 0
                prev_poll = request.session.get('transfer_poll_state', {})
                prev_item = prev_poll.get(item_hash)
                
                if prev_item and data['latest_update']:
                    prev_completed = prev_item.get('completed', 0)
                    prev_time_str = prev_item.get('timestamp')
                    if prev_time_str:
                        prev_time = parse_datetime(prev_time_str)
                        if prev_time and data['total_completed'] > prev_completed:
                            delta_bytes = data['total_completed'] - prev_completed
                            delta_seconds = (now - prev_time).total_seconds()
                            if delta_seconds > 0:
                                speed_mbps = (delta_bytes / delta_seconds) / (1024 * 1024)
                
                # Fallback: use elapsed time since earliest start
                if speed_mbps == 0 and data['earliest_start']:
                    elapsed_seconds = (now - data['earliest_start']).total_seconds()
                    if elapsed_seconds > 0 and data['total_completed'] > 0:
                        speed_mbps = (data['total_completed'] / elapsed_seconds) / (1024 * 1024)
                
                if speed_mbps > 0:
                    total_speed_mbps += speed_mbps
                
                active_transfers.append({
                    'name': data['item_name'],
                    'item_name': data['item_name'],
                    'size': data['total_size'],
                    'completed': data['total_completed'],
                    'percent': percent,
                    'file_count': data['file_count'],
                    'speed_mbps': speed_mbps,
                    'extraction_status': '',
                    'extraction_progress': 0,
                })
        
        # Get item extraction statuses
        items_with_transfers = Item.objects.filter(transfers__status__in=['pending', 'transferring']).distinct()
        for item in items_with_transfers:
            if item.hash in transfers_by_item:
                transfers_by_item[item.hash]['extraction_status'] = item.extraction_status
                transfers_by_item[item.hash]['extraction_progress'] = item.extraction_progress
        
        for t in active_transfers:
            item_hash = next((k for k, v in transfers_by_item.items() if v['item_name'] == t['item_name']), None)
            if item_hash:
                t['extraction_status'] = transfers_by_item[item_hash].get('extraction_status', '')
                t['extraction_progress'] = transfers_by_item[item_hash].get('extraction_progress', 0)
        
        # Update session with current poll state for next poll's speed calculation
        poll_state = {}
        for item_hash, data in transfers_by_item.items():
            poll_state[item_hash] = {
                'completed': data['total_completed'],
                'timestamp': now.isoformat(),
            }
        request.session['transfer_poll_state'] = poll_state
        request.session.modified = True
        
        total_queued = Item.objects.filter(status='Grabbed', archived=False).count()
        
        return JsonResponse({
            'manager_summary': manager_summary,
            'grabbing_downloads': grabbing_downloads,
            'active_transfers': active_transfers,
            'total_speed_mbps': total_speed_mbps,
            'total_queued': total_queued,
        })
    except Exception as e:
        import traceback
        logger.error(f"api_dashboard error: {e}\n{traceback.format_exc()}")
        return JsonResponse({'error': str(e)}, status=500)


def api_queue(request):
    """JSON API for queue data - used for AJAX polling."""
    from django.http import JsonResponse
    from itemqueue.models import FileTransfer, ItemHistory
    
    items = Item.objects.filter(status__in=['Grabbed', 'PostProcessing'], archived=False).select_related('manager', 'downloader').order_by('-modified')
    
    queue_items = []
    for item in items:
        item_data = {
            'name': item.name,
            'hash': item.hash,
            'status': item.status,
            'manager': item.manager.name if item.manager else '',
            'downloader': item.downloader.name if item.downloader else '',
            'size': item.size,
            'created': item.created.isoformat(),
            'modified': item.modified.isoformat(),
        }
        
        # Add transfer info for PostProcessing items
        if item.status == 'PostProcessing':
            transfers = FileTransfer.objects.filter(item=item).order_by('-created')
            item_data['transfers'] = [{
                'filename': t.filename,
                'status': t.status,
                'bytes_transferred': t.bytes_transferred,
                'file_size': t.file_size,
                'error_message': t.error_message or '',
            } for t in transfers]
            
            # Get recent history (last 5 entries) - DON'T slice before filtering!
            # In PostgreSQL, you can't call .filter() on a sliced QuerySet
            all_history = ItemHistory.objects.filter(item=item).order_by('-created')
            history_list = list(all_history[:5])  # Convert to list after filtering
            item_data['history'] = [{
                'details': h.details,
                'created': h.created.isoformat(),
            } for h in history_list]
            
            # Check if this is a folder download from AirDC++ - use queryset, not list
            folder_history = all_history.filter(details__icontains='Folder bundle detected').first()
            if folder_history:
                # Extract item count from history detail string
                import re
                match = re.search(r'(\d+)\s+items', folder_history.details)
                if match:
                    item_count = int(match.group(1))
                    item_data['is_folder_download'] = True
                    item_data['folder_item_count'] = item_count
        
        queue_items.append(item_data)
    
    return JsonResponse({'items': queue_items})


def api_history(request):
    """JSON API for history data - used for AJAX polling."""
    from django.http import JsonResponse
    
    items = Item.objects.filter(status__in=['Completed', 'Failed'], archived=False).select_related('manager', 'downloader').order_by('-modified')[:100]
    
    history_items = []
    for item in items:
        history_items.append({
            'name': item.name,
            'hash': item.hash,
            'status': item.status,
            'manager': item.manager.name if item.manager else '',
            'downloader': item.downloader.name if item.downloader else '',
            'size': item.size,
            'modified': item.modified.isoformat(),
        })
    
    return JsonResponse({'items': history_items})


@login_required
def api_item_history(request, item_hash):
    """JSON API for fetching ItemHistory records for a specific item.
    
    Used for lazy-loading history when expanded in the UI.
    """
    try:
        item = Item.objects.get(hash=item_hash)
        history = item.history.order_by('-created')[:50]
        
        history_items = []
        for h in history:
            history_items.append({
                'id': h.id,
                'created': h.created.isoformat() if h.created else None,
                'details': h.details,
            })
        
        return JsonResponse({
            'hash': item_hash,
            'name': item.name,
            'history': history_items,
            'total': item.history.count(),
        })
    except Item.DoesNotExist:
        return JsonResponse({'error': 'Item not found'}, status=404)


@login_required
def api_item_transfers(request, item_hash):
    """JSON API for fetching FileTransfer records for a specific item.
    
    Used for lazy-loading transfers when expanded in the UI.
    """
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        item = Item.objects.get(hash=item_hash)
        transfers = list(item.transfers.all())
        
        transfer_items = []
        for t in transfers:
            try:
                started = t.started.isoformat() if t.started else None
            except Exception as e:
                logger.error(f"Error serializing started: {e}, value={t.started}")
                started = str(t.started) if t.started else None
            try:
                completed = t.completed.isoformat() if t.completed else None
            except Exception as e:
                logger.error(f"Error serializing completed: {e}, value={t.completed}")
                completed = str(t.completed) if t.completed else None
            
            transfer_items.append({
                'id': t.id,
                'filename': t.filename,
                'file_size': t.file_size,
                'bytes_transferred': t.bytes_transferred,
                'percent_complete': t.percent_complete,
                'status': t.status,
                'started': started,
                'completed': completed,
                'error_message': t.error_message,
            })
        
        return JsonResponse({
            'hash': item_hash,
            'name': item.name,
            'transfers': transfer_items,
            'total': len(transfers),
        })
    except Item.DoesNotExist:
        return JsonResponse({'error': 'Item not found'}, status=404)
    except Exception as e:
        logger.error(f"api_item_transfers error: {e}", exc_info=True)
        return JsonResponse({'error': str(e)}, status=500)


def api_version_check(request):
    """Check for latest version from GitHub and Docker."""
    import os
    from _version import __version__
    
    result = {
        'local_version': __version__,
        'docker_tag': os.environ.get('DOCKER_TAG', 'unknown'),
        'github_latest': None,
        'github_url': 'https://api.github.com/repos/WillowMist/harpoon2/releases/latest',
        'update_available': False,
    }
    
    try:
        response = requests.get('https://api.github.com/repos/WillowMist/harpoon2/releases/latest', timeout=5)
        if response.status_code == 200:
            data = response.json()
            result['github_latest'] = data.get('tag_name', '').lstrip('v')
            result['github_url'] = data.get('html_url', 'https://github.com/WillowMist/harpoon2/releases')
            result['github_name'] = data.get('name', '')
            result['github_published'] = data.get('published_at', '')
            
            # Compare versions
            if result['github_latest'] and result['local_version']:
                try:
                    local_parts = [int(x) for x in result['local_version'].split('.')]
                    github_parts = [int(x) for x in result['github_latest'].split('.')]
                    # Pad shorter list with zeros
                    while len(local_parts) < len(github_parts):
                        local_parts.append(0)
                    while len(github_parts) < len(local_parts):
                        github_parts.append(0)
                    
                    result['update_available'] = github_parts > local_parts
                except (ValueError, AttributeError):
                    # If version parsing fails, assume no update
                    result['update_available'] = False
    except Exception as e:
        result['error'] = str(e)
    
    return JsonResponse(result)
