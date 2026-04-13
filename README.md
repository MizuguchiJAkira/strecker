# Basal Informatics

Ground-truth ecological verification platform. Processes trail camera photos from hunters (via Strecker), transforms them into parcel-level nature-risk assessments for insurers and lenders facing TNFD/EU CSRD compliance requirements.

## Setup

```bash
# Copy environment config
cp .env.example .env

# Start PostGIS
docker-compose up -d db

# Install Python dependencies
pip install -r requirements.txt

# Initialize database schema
python manage.py db init

# Seed demo data (when available)
python manage.py db seed
```

## Pipeline Stages

1. **Strecker** — Hunter-facing photo processing (ingest, detect, classify, sort, report)
2. **Bias Correction** — Camera placement bias correction (Kolowski & Forrester 2017)
3. **Habitat** — Habitat delineation and unit modeling
4. **Risk** — Risk synthesis engine for parcel-level assessments
5. **Report** — Enterprise PDF report generator
6. **Web** — Flask interface for uploads and results

## CLI

```bash
python manage.py --help          # Show all commands
python manage.py db init         # Initialize PostGIS schema
python manage.py web run         # Start Flask dev server
```
