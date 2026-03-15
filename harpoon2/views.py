from django.shortcuts import get_object_or_404, render, redirect
from django.contrib import messages
from django.contrib.sessions.backends.db import SessionStore
from django.utils.dateparse import parse_datetime
from entities.models import Manager, Downloader
from itemqueue.models import Item, FileTransfer, ItemHistory
import requests

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
    
    # Get active download info from downloaders - labeled as "Grabbing"
    grabbing_downloads = []
    for downloader in Downloader.objects.all():
        try:
            wrapper = downloader.client
            if downloader.downloadertype == 'RTorrent':
                # Use efficient multicall to get active downloads
                active_torrents = wrapper.get_active_downloads(limit=10)
                for torrent in active_torrents:
                    grabbing_downloads.append({
                        'name': torrent['name'],
                        'hash': torrent['hash'],
                        'size': torrent['size'],
                        'completed': torrent['completed'],
                        'percent': torrent['percent'],
                        'downloader': downloader.name,
                        'status': 'Grabbing',
                    })
            elif downloader.downloadertype == 'SABNzbd':
                wrapper._ensure_client()
                result = wrapper._api_call('queue')
                if 'queue' in result:
                    slots = result['queue'].get('slots', [])
                    for slot in slots[:10]:  # Limit to 10 items
                        if slot.get('status') != 'Completed':
                            grabbing_downloads.append({
                                'name': slot.get('filename', ''),
                                'hash': slot.get('nzo_id', ''),
                                'size': float(slot.get('mb', 0)) * 1024 * 1024,
                                'completed': 0,
                                'percent': float(slot.get('percentage', 0)),
                                'downloader': downloader.name,
                                'status': 'Grabbing',
                            })
        except Exception as e:
            pass
    
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


def queue(request):
    """Queue page - shows all queued/grabbing/postprocessing items."""
    grabbing_items = Item.objects.filter(status='Grabbed', archived=False).select_related('manager', 'downloader').order_by('-created')
    postprocessing_items = Item.objects.filter(status='PostProcessing', archived=False).select_related('manager', 'downloader').order_by('-modified')
    
    return render(request, 'queue.html', {
        'grabbing_items': grabbing_items,
        'postprocessing_items': postprocessing_items,
    })


def history(request):
    """History page - shows completed and failed items."""
    # Get show_archived parameter from query string
    show_archived = request.GET.get('show_archived', 'false').lower() == 'true'
    
    # Base queryset
    completed_base = Item.objects.filter(status='Completed').select_related('manager', 'downloader')
    failed_base = Item.objects.filter(status='Failed').select_related('manager', 'downloader')
    
    # Filter by archive status
    if show_archived:
        completed_items = completed_base.filter(archived=True).order_by('-archived_at')[:50]
        failed_items = failed_base.filter(archived=True).order_by('-archived_at')[:50]
    else:
        completed_items = completed_base.filter(archived=False).order_by('-modified')[:50]
        failed_items = failed_base.filter(archived=False).order_by('-modified')[:50]
    
    # Get counts for display
    completed_count = Item.objects.filter(status='Completed', archived=False).count()
    completed_archived_count = Item.objects.filter(status='Completed', archived=True).count()
    failed_count = Item.objects.filter(status='Failed', archived=False).count()
    failed_archived_count = Item.objects.filter(status='Failed', archived=True).count()
    
    # Get all downloaders for dropdown
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
            
            return redirect('home')
        except Item.DoesNotExist:
            messages.error(request, 'Item not found')
            return redirect('home')
        except Exception as e:
            messages.error(request, f'Error cancelling transfer: {str(e)}')
            return redirect('home')
    
    return redirect('home')


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
        
        # Get active downloads
        grabbing_downloads = []
        for downloader in Downloader.objects.all():
            try:
                wrapper = downloader.client
                if downloader.downloadertype == 'RTorrent':
                    active_torrents = wrapper.get_active_downloads(limit=10)
                    for torrent in active_torrents:
                        grabbing_downloads.append({
                            'name': torrent.get('name', ''),
                            'hash': torrent.get('hash', ''),
                            'size': torrent.get('size', 0),
                            'completed': torrent.get('completed', 0),
                            'percent': torrent.get('percent', 0),
                            'downloader': downloader.name,
                            'status': 'Grabbing',
                        })
                elif downloader.downloadertype == 'SABNzbd':
                    wrapper._ensure_client()
                    result = wrapper._api_call('queue')
                    if 'queue' in result:
                        slots = result['queue'].get('slots', [])
                        for slot in slots[:10]:
                            if slot.get('status') != 'Completed':
                                grabbing_downloads.append({
                                    'name': slot.get('filename', ''),
                                    'hash': slot.get('nzo_id', ''),
                                    'size': float(slot.get('mb', 0)) * 1024 * 1024,
                                    'completed': 0,
                                    'percent': float(slot.get('percentage', 0)),
                                    'downloader': downloader.name,
                                    'status': 'Grabbing',
                                })
            except Exception as e:
                logger.warning(f"Error fetching downloads from {downloader.name}: {e}")
        
        # Get active transfers
        active_transfers_query = FileTransfer.objects.filter(
            status__in=['pending', 'transferring', 'completed']
        ).select_related('item')
        
        item_total_sizes = {}
        for transfer in active_transfers_query:
            if not transfer.item:
                continue
            item_hash = transfer.item.hash
            if item_hash not in item_total_sizes:
                all_transfers = active_transfers_query.filter(item__hash=item_hash)
                item_total_sizes[item_hash] = sum(t.file_size for t in all_transfers)
        
        transfers_by_item = {}
        for transfer in active_transfers_query:
            if not transfer.item:
                continue
            item_hash = transfer.item.hash
            if item_hash not in transfers_by_item:
                transfers_by_item[item_hash] = {
                    'item_name': transfer.item.name,
                    'total_size': item_total_sizes.get(item_hash, 0),
                    'total_completed': 0,
                    'file_count': 0,
                }
            
            transfers_by_item[item_hash]['total_completed'] += transfer.bytes_transferred
            transfers_by_item[item_hash]['file_count'] += 1
        
        active_transfers = []
        for item_hash, data in transfers_by_item.items():
            if data['total_completed'] < data['total_size']:
                percent = int((data['total_completed'] / data['total_size']) * 100) if data['total_size'] > 0 else 0
                active_transfers.append({
                    'name': data['item_name'],
                    'item_name': data['item_name'],
                    'size': data['total_size'],
                    'completed': data['total_completed'],
                    'percent': percent,
                    'file_count': data['file_count'],
                    'speed_mbps': 0,
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
        
        total_queued = Item.objects.filter(status='Grabbed', archived=False).count()
        
        return JsonResponse({
            'manager_summary': manager_summary,
            'grabbing_downloads': grabbing_downloads,
            'active_transfers': active_transfers,
            'total_speed_mbps': 0,
            'total_queued': total_queued,
        })
    except Exception as e:
        import traceback
        logger.error(f"api_dashboard error: {e}\n{traceback.format_exc()}")
        return JsonResponse({'error': str(e)}, status=500)


def api_queue(request):
    """JSON API for queue data - used for AJAX polling."""
    from django.http import JsonResponse
    
    items = Item.objects.filter(status__in=['Grabbed', 'PostProcessing'], archived=False).select_related('manager', 'downloader').order_by('-modified')
    
    queue_items = []
    for item in items:
        queue_items.append({
            'name': item.name,
            'hash': item.hash,
            'status': item.status,
            'manager': item.manager.name if item.manager else '',
            'downloader': item.downloader.name if item.downloader else '',
            'size': item.size,
            'created': item.created.isoformat(),
            'modified': item.modified.isoformat(),
        })
    
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
