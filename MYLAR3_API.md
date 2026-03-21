# Mylar3 API Reference

## Overview
Mylar3 is an automated Comic Book (cbr/cbz) downloader that integrates with NZB and torrent clients. It's similar in architecture to Sonarr/Radarr/Whisparr.

## API Base URL
```
http://localhost:8090 + HTTP_ROOT + /api?apikey=$apikey&cmd=$command
```

## Key Endpoints

### Series Management
- `getIndex` - Fetch all series in watchlist (returns: ComicName, ComicID, Status, DateAdded, etc.)
- `getComic&id=$comicid` - Fetch single series data with all issues
- `findComic&name=$comicname` - Search for comics by name
- `addComic&id=$comicid` - Add comic to watchlist
- `delComic&id=$comicid` - Remove comic from watchlist
- `refreshComic&id=$comicid` - Refresh series info
- `pauseComic&id=$comicid` - Pause series
- `resumeComic&id=$comicid` - Resume series

### Issue Management
- `getIssue&id=$comicid` - Fetch issues for a series
- `getUpcoming[&include_downloaded_issues=Y]` - Get upcoming releases
- `getWanted` - Get wanted (missing) issues
- `queueIssue&id=$issueid` - Mark issue as wanted and search
- `unqueueIssue&id=$issueid` - Unmark issue (mark as skipped)

### History
- `getHistory` - Get download history (returns: Status, DateAdded, Title, URL, etc.)

### Story Arcs
- `getStoryArc` - List all story arcs
- `getStoryArc&customOnly=1` - List custom story arcs only
- `getStoryArc&id=$arcid` - Show story arc issues
- `addStoryArc&id=$arcid&issues=$issues` - Add issues to existing arc
- `addStoryArc&storyarcname=$name&issues=$issues` - Create new arc

### System
- `getVersion` - Get version info
- `checkGithub` - Check for updates
- `shutdown` - Shutdown Mylar3
- `restart` - Restart Mylar3
- `update` - Update Mylar3

## Harpoon2 Integration Points

### Similar to Existing Managers
- Follows same pattern as Sonarr/Radarr/Whisparr
- API key authentication
- Monitor watchlist for "wanted" issues
- Grab completed downloads
- Post-process downloaded comics

### Data Model Additions Needed
- Manager type: "Mylar3"
- Track: ComicID, IssueID, Status
- Use same Item model with category="comic"

### Webhook/Callback Pattern
- Mylar3 likely uses download client callbacks
- Need to implement similar to other managers
