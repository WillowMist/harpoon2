# Harpoon2 Agent Guidelines

## Project Overview
Harpoon2 is a Django-based download manager that integrates with Sonarr/Radarr/Whisparr, multiple downloaders (RTorrent, SABnzbd, QBittorrent, AirDC++), and handles file transfers with post-processing.

## Key Conventions

### Authentication
- Dashboard, config pages (settings, managers, downloaders), queue, history, and search all require `@login_required` decorator
- Use Django's `get_user_model()` for user operations
- Custom login view at `/login/` that shows "Create Account" form if no superuser exists
- `LOGIN_URL = '/login/'` in settings to redirect to custom login

### URL Naming
- URL names use **underscores** (e.g., `archive_item`, `update_item_status`)
- URL paths use **slashes** (e.g., `/archive/<str:item_hash>/`)
- Always match URL names between `urls.py` and templates

### CSS/Theming
- Theme overrides live in `static/css/overrides/{theme}.css`
- Use overrides for theme-specific styling issues
- Modal styles may differ from page styles - test both

### Database
- Uses PostgreSQL in Docker, SQLite for local dev
- Uses Django ORM with `get_user_model()` for auth
- Boolean fields: use Python `True`/`False`, not SQLite integers

### Downloaders
- Downloader-specific logic lives in `entities/downloaders/{name}.py`
- Each downloader implements: `get_completed()`, `verify_completion()`, `get_download_info()`
- **Important**: Methods must be properly indented inside the class, not in wrapper functions

### Docker Workflow
1. Make code changes locally
2. Commit and push to GitHub
3. User will pull the updated image and restart the container
4. For celery workers after restart: `pkill -HUP -f 'celery.*worker'` to reload code (if needed)

### Testing in Container
- Access the running harpoon2 container with: `ssh docker` (no credentials required)
- Once inside: `docker exec harpoon2-app python manage.py shell` for Django shell
- Use this for database queries, testing code changes, and debugging

### Git Workflow
- Commit often with clear messages
- Push after each logical change
- Don't commit debug code (print statements, debug files)

## Documentation

### Using Context7 for Library Documentation
- **Always use Context7** when working with external libraries or frameworks (Django, Celery, PostgreSQL, etc.)
- Call `context7_resolve-library-id` first to get the library ID, then `context7_query-docs` for specific questions
- Context7 provides up-to-date documentation and code examples
- Only fall back to web search if Context7 doesn't have the library

### Common Tasks

### Adding a new downloader
1. Create `entities/downloaders/{name}.py` with class inheriting from `BaseDownloader`
2. Implement `get_completed()`, `verify_completion()`, `get_download_info()` methods
3. Add to `DOWNLOADER_TYPES` in settings
4. Add to downloader cache and check tasks
5. Export in `__init__.py`

### Fixing indentation bugs in downloaders
- Check that class methods are properly indented inside the class
- Use `grep -n "^def \|^class "` to find function/class definitions
- Methods incorrectly placed in wrapper functions will cause "not implemented" errors

### Adding protected pages
1. Add `@login_required` decorator to view function
2. Ensure `LOGIN_URL` setting points to custom login
3. Test: logout and verify redirect to `/login/`
