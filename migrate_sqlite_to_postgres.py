#!/usr/bin/env python3
"""
Direct SQLite to PostgreSQL migration script.
Copies application data while skipping Django system tables.
"""
import sqlite3
import psycopg2
from psycopg2.extras import execute_batch
import os
import sys

# Configuration
SQLITE_DB = '/data/harpoon2.db'
PG_HOST = os.environ.get('DB_HOST', 'postgres')
PG_PORT = os.environ.get('DB_PORT', '5432')
PG_NAME = os.environ.get('DB_NAME', 'harpoon')
PG_USER = os.environ.get('DB_USER', 'harpoon')
PG_PASS = os.environ.get('DB_PASSWORD', 'harpoon-default-password')

# Tables to migrate (in order to respect foreign keys)
# NOTE: Skipping itemhistory - it's just audit logs and can be rebuilt
TABLES_TO_MIGRATE = [
    'entities_downloadfolder',
    'entities_seedbox',
    'entities_manager',
    'entities_downloader',
    'itemqueue_item',
    'itemqueue_filetransfer',
    'users_customuser',
]

# Define which fields need boolean conversion (SQLite stores as 0/1, PostgreSQL expects true/false)
BOOLEAN_FIELDS = {
    'entities_manager': ['delete_source', 'monitor_subdirectories', 'move_on_complete', 'enabled', 'scan_on_startup'],
    'itemqueue_item': ['archived'],
    'users_customuser': ['is_superuser', 'is_staff', 'is_active'],
}

def convert_row_values(table_name, columns, row):
    """Convert SQLite values to PostgreSQL values (e.g., 0/1 to boolean)"""
    if table_name not in BOOLEAN_FIELDS:
        return row
    
    row_list = list(row)
    boolean_cols = BOOLEAN_FIELDS[table_name]
    
    for i, col in enumerate(columns):
        if col in boolean_cols:
            # Convert SQLite integer 0/1 to PostgreSQL boolean True/False
            row_list[i] = bool(row_list[i]) if row_list[i] is not None else None
    
    return tuple(row_list)

def copy_table(sqlite_cursor, pg_cursor, pg_conn, table_name):
    """Copy a single table from SQLite to PostgreSQL"""
    print(f"\nMigrating {table_name}...")
    
    try:
        # Get data from SQLite
        sqlite_cursor.execute(f"SELECT * FROM {table_name}")
        columns = [desc[0] for desc in sqlite_cursor.description]
        rows = sqlite_cursor.fetchall()
        
        if not rows:
            print(f"  No data to migrate")
            return 0
        
        # Convert rows (e.g., boolean fields)
        converted_rows = [convert_row_values(table_name, columns, row) for row in rows]
        
        # Build insert query
        placeholders = ', '.join(['%s'] * len(columns))
        col_names = ', '.join(columns)
        query = f"INSERT INTO {table_name} ({col_names}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"
        
        # Insert into PostgreSQL in batches
        batch_size = 100
        inserted = 0
        
        for i in range(0, len(converted_rows), batch_size):
            batch = converted_rows[i:i+batch_size]
            try:
                execute_batch(pg_cursor, query, batch, page_size=batch_size)
                pg_conn.commit()
                inserted += len(batch)
                print(f"  Inserted {inserted}/{len(converted_rows)} rows...", end='\r')
            except psycopg2.Error as e:
                print(f"  Error inserting batch: {e}")
                pg_conn.rollback()
                # Try individual inserts to skip duplicates
                for row in batch:
                    try:
                        pg_cursor.execute(query, row)
                        pg_conn.commit()
                        inserted += 1
                    except psycopg2.Error:
                        pg_conn.rollback()
        
        print(f"  ✓ Migrated {inserted}/{len(converted_rows)} rows")
        return inserted
        
    except Exception as e:
        print(f"  ✗ Error migrating {table_name}: {e}")
        return 0

def main():
    print("=" * 60)
    print("SQLite to PostgreSQL Direct Migration")
    print("=" * 60)
    
    # Connect to SQLite
    try:
        sqlite_conn = sqlite3.connect(SQLITE_DB)
        sqlite_cursor = sqlite_conn.cursor()
        print(f"\n✓ Connected to SQLite: {SQLITE_DB}")
    except Exception as e:
        print(f"\n✗ Failed to connect to SQLite: {e}")
        sys.exit(1)
    
    # Connect to PostgreSQL
    try:
        pg_conn = psycopg2.connect(
            host=PG_HOST,
            port=PG_PORT,
            database=PG_NAME,
            user=PG_USER,
            password=PG_PASS,
            connect_timeout=10
        )
        pg_cursor = pg_conn.cursor()
        print(f"✓ Connected to PostgreSQL: {PG_USER}@{PG_HOST}:{PG_PORT}/{PG_NAME}")
    except Exception as e:
        print(f"✗ Failed to connect to PostgreSQL: {e}")
        sqlite_conn.close()
        sys.exit(1)
    
    # Migrate tables
    print("\n" + "=" * 60)
    print("Starting migration...")
    print("=" * 60)
    
    total_migrated = 0
    failed_tables = []
    
    for table in TABLES_TO_MIGRATE:
        try:
            count = copy_table(sqlite_cursor, pg_cursor, pg_conn, table)
            total_migrated += count
        except Exception as e:
            print(f"  ✗ Failed to migrate {table}: {e}")
            failed_tables.append(table)
    
    # Summary
    print("\n" + "=" * 60)
    print("Migration Summary")
    print("=" * 60)
    print(f"Total rows migrated: {total_migrated}")
    
    if failed_tables:
        print(f"\nFailed tables ({len(failed_tables)}):")
        for table in failed_tables:
            print(f"  - {table}")
    else:
        print("\n✓ All tables migrated successfully!")
    
    # Verify
    print("\n" + "=" * 60)
    print("Verification")
    print("=" * 60)
    
    for table in TABLES_TO_MIGRATE:
        try:
            pg_cursor.execute(f"SELECT COUNT(*) FROM {table}")
            count = pg_cursor.fetchone()[0]
            print(f"{table}: {count} rows")
        except Exception as e:
            print(f"{table}: Error - {e}")
    
    # Cleanup
    sqlite_conn.close()
    pg_conn.close()
    
    print("\n" + "=" * 60)
    print("✓ Migration complete!")
    print("=" * 60)

if __name__ == '__main__':
    main()
