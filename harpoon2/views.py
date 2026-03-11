from django.shortcuts import get_object_or_404, render, redirect
from django.contrib import messages
from entities.models import Manager, Downloader
from itemqueue.models import Item, FileTransfer, ItemHistory
import requests

def home(request):
    """Dashboard - shows active downloads (grabbing), file transfers, and quick summary by manager."""
    managers = Manager.objects.all()
    
    # Get counts by manager
    manager_summary = []
    for m in managers:
        grabbing = Item.objects.filter(status='Grabbed', manager=m).count()
        postprocessing = Item.objects.filter(status='PostProcessing', manager=m).count()
        completed = Item.objects.filter(status='Completed', manager=m).count()
        failed = Item.objects.filter(status='Failed', manager=m).count()
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
                                'name': slot.get('name', ''),
                                'hash': slot.get('nzo_id', ''),
                                'size': slot.get('mb', 0) * 1024 * 1024,
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
        
        # Calculate speed
        speed_mbps = 0
        if data['earliest_start']:
            elapsed_seconds = (timezone.now() - data['earliest_start']).total_seconds()
            if elapsed_seconds > 0 and data['total_completed'] > 0:
                speed_mbps = (data['total_completed'] / elapsed_seconds) / (1024 * 1024)
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
    grabbing_items = Item.objects.filter(status='Grabbed').select_related('manager', 'downloader').order_by('-created')
    postprocessing_items = Item.objects.filter(status='PostProcessing').select_related('manager', 'downloader').order_by('-modified')
    
    return render(request, 'queue.html', {
        'grabbing_items': grabbing_items,
        'postprocessing_items': postprocessing_items,
    })


def history(request):
    """History page - shows completed and failed items."""
    completed_items = Item.objects.filter(status='Completed').select_related('manager', 'downloader').order_by('-modified')[:50]
    failed_items = Item.objects.filter(status='Failed').select_related('manager', 'downloader').order_by('-modified')[:50]
    
    return render(request, 'history.html', {
        'completed_items': completed_items,
        'failed_items': failed_items,
    })


def cancel_download(request, item_id):
    """Cancel a download and mark it as failed."""
    if request.method == 'POST':
        try:
            item = Item.objects.get(id=item_id)
            
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
