# PostgreSQL Migration Guide

Harpoon2 now supports PostgreSQL as the database backend. This guide covers both Docker and non-Docker deployments.

## Docker Deployment (Recommended)

The `docker-compose.yml` now includes a PostgreSQL service. PostgreSQL is automatically used when you run Harpoon2 with Docker.

### Setup

1. **Set database password** (optional, defaults to `harpoon-default-password`):
   ```bash
   export DB_PASSWORD=your-secure-password
   ```

2. **Start the services**:
   ```bash
   docker-compose up -d
   ```

PostgreSQL will automatically:
- Create a database named `harpoon`
- Create a user named `harpoon` with the specified password
- Be used as the backend for Harpoon2

### One-Time Migration from SQLite to PostgreSQL

If you're migrating an existing Harpoon2 installation from SQLite to PostgreSQL:

#### Option 1: Using Django's dumpdata/loaddata (Recommended)

1. **Export data from SQLite**:
   ```bash
   docker-compose exec harpoon2 python manage.py dumpdata > dump.json
   ```

2. **Stop containers**:
   ```bash
   docker-compose down
   ```

3. **Update docker-compose.yml** to include PostgreSQL (if not already done)

4. **Start new PostgreSQL container**:
   ```bash
   docker-compose up -d postgres
   ```
   Wait for it to be healthy

5. **Run migrations** (this creates the database schema):
   ```bash
   docker-compose up --no-deps -d harpoon2
   docker-compose exec harpoon2 python manage.py migrate
   ```

6. **Load the exported data**:
   ```bash
   docker-compose exec harpoon2 python manage.py loaddata dump.json
   ```

7. **Verify the data**:
   ```bash
   docker-compose exec harpoon2 python manage.py shell
   # In the shell:
   # >>> from itemqueue.models import Item
   # >>> Item.objects.count()  # Should show your items count
   ```

#### Option 2: Manual Field-by-Field Transfer

If dumpdata/loaddata doesn't work for your use case, you can manually transfer data:

1. Export from SQLite and import to PostgreSQL using custom Python scripts
2. Ensure all relationships are maintained
3. Verify data integrity after transfer

## Non-Docker Deployment

### Using SQLite (Default, Backward Compatible)

Harpoon2 still supports SQLite by default for backward compatibility:

```bash
python manage.py runserver
```

This uses the SQLite database at `/data/harpoon2.db`.

### Using PostgreSQL

To use PostgreSQL in a non-Docker environment:

1. **Install PostgreSQL**:
   ```bash
   # Ubuntu/Debian
   sudo apt-get install postgresql postgresql-contrib

   # macOS
   brew install postgresql
   ```

2. **Install Python dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Create database and user**:
   ```bash
   sudo -u postgres psql
   ```
   In the PostgreSQL shell:
   ```sql
   CREATE DATABASE harpoon;
   CREATE USER harpoon WITH PASSWORD 'your-secure-password';
   ALTER ROLE harpoon SET client_encoding TO 'utf8';
   ALTER ROLE harpoon SET default_transaction_isolation TO 'read committed';
   ALTER ROLE harpoon SET default_transaction_deferrable TO on;
   ALTER ROLE harpoon SET timezone TO 'UTC';
   GRANT ALL PRIVILEGES ON DATABASE harpoon TO harpoon;
   \q
   ```

4. **Configure Django settings**:
   ```bash
   # Copy the template to settings.py (settings.py is in .gitignore)
   cp harpoon2/settings_template.py harpoon2/settings.py
   ```

5. **Edit `harpoon2/settings.py`** and update the DATABASES section:
   ```python
   DATABASES = {
       'default': {
           'ENGINE': 'django.db.backends.postgresql',
           'NAME': 'harpoon',
           'USER': 'harpoon',
           'PASSWORD': 'your-secure-password',
           'HOST': 'localhost',
           'PORT': '5432',
       }
   }
   ```

6. **Run migrations**:
   ```bash
   python manage.py migrate
   ```

7. **Create superuser** (if needed):
   ```bash
   python manage.py createsuperuser
   ```

8. **Run Harpoon2**:
   ```bash
   python manage.py runserver
   ```

## Environment Variables

For Docker deployments, you can customize database settings via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `DB_PASSWORD` | `harpoon-default-password` | PostgreSQL password for harpoon user |
| `DB_HOST` | `postgres` | PostgreSQL host (container name in Docker) |
| `DB_PORT` | `5432` | PostgreSQL port |
| `DB_NAME` | `harpoon` | Database name |
| `DB_USER` | `harpoon` | PostgreSQL username |
| `USE_POSTGRES` | `true` | Enable PostgreSQL backend (set automatically in docker-compose.yml) |

## Important Notes

- **Backup your data**: Before migrating from SQLite to PostgreSQL, create a backup of your current database
- **settings.py is not tracked**: When using non-Docker setup, `settings.py` is in `.gitignore` so your configuration won't be committed
- **SQLite still works**: If you don't set PostgreSQL environment variables, Harpoon2 will fall back to SQLite
- **Docker is recommended**: For production deployments, using Docker with PostgreSQL is recommended for better scalability and data persistence

## Troubleshooting

### "Can't connect to PostgreSQL"
- Ensure the PostgreSQL service is running and healthy: `docker-compose ps`
- Check that the password matches in both docker-compose.yml and your connection
- Verify the PostgreSQL container logs: `docker-compose logs postgres`

### "Database does not exist"
- Run migrations: `docker-compose exec harpoon2 python manage.py migrate`

### "Migration failed"
- Check for conflicts with existing data
- Try removing the PostgreSQL volume and starting fresh: `docker volume rm harpoon2-postgres-data`
- Then run migrations again

## Rollback to SQLite

If you need to revert to SQLite:

1. **Stop services**:
   ```bash
   docker-compose down
   ```

2. **Comment out PostgreSQL service** in `docker-compose.yml`

3. **Remove PostgreSQL environment variables** from harpoon2 service

4. **Start services**:
   ```bash
   docker-compose up -d
   ```

Harpoon2 will use the SQLite database if PostgreSQL is not configured.
