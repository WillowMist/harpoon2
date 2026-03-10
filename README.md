**Harpoon 2**

***Modernization in Progress - Django 5.2 / Python 3.14***

**Description**
---------
A modern Django-based rewrite of the Harpoon application designed to automatically send and/or monitor torrents and nzb files to remote torrent/nzb clients, monitor for completion, and retrieve completed files back to the local machine for automatic post-processing with media management clients (Sonarr, Radarr, etc.).

**Current Status**
----------
- ✅ Django 5.2.12 with Python 3.14 venv
- ✅ Bootstrap 5 UI (Bootswatch Vapor theme)
- ✅ Entity Management (Managers, Downloaders, Seedboxes, Download Folders)
- ✅ AJAX Modal Forms for CRUD operations
- 🔄 Queue System (in development)
- ⏳ Download Integration & SFTP Retrieval
- ⏳ History & Logging
- ⏳ Task Scheduling

**Requirements**
----------
- LINUX only
- Python 3.14+ (developed with 3.14.0)
- Django 5.2.12+
- pip
- Virtual Environment
- RTorrent client (optional, running remotely on seedbox)
- SABNzbd client (optional, running remotely on seedbox)
- Sonarr (optional)
- Radarr (optional)
- Lidarr (optional)
- Readarr (optional)
- Whisparr (optional)

**Installation**
Install Python 3.14+, pip, and create a virtual environment:

Download Harpoon2:
`git clone https://github.com/DarkSir23/harpoon2`

Go to harpoon2 folder: `cd harpoon2`

Create virtual environment:
`python3.14 -m venv harpoon2-venv`

Activate virtual environment:
`source harpoon2-venv/bin/activate`

Install dependencies:
`pip install -r requirements.txt`

Apply migrations:
`python manage.py migrate`

Run development server:
`python manage.py runserver 0.0.0.0:8000`



