# Deployment Reference

Automate builds, deploy to S3, and keep data fresh using GitHub Actions. Zeeker's CLI handles
the S3 mechanics — the workflows handle when and how those commands run.

## Zeeker CLI Commands

These are the building blocks. The workflows below orchestrate them.

```bash
# Build the database locally from all resources
uv run zeeker build

# Build a specific resource only
uv run zeeker build headlines

# Download existing DB from S3 first, then build incrementally (key for daily runs)
uv run zeeker build --sync-from-s3

# Build with full-text search index setup
uv run zeeker build --setup-fts

# Deploy the database to S3
uv run zeeker deploy

# Create a dated backup archive in S3
uv run zeeker backup
```

The critical command is `build --sync-from-s3`. Without it, every build starts from scratch.
With it, the existing database is downloaded from S3 first, so `fetch_data()` receives the
`existing_table` with all previously collected records. This is what makes incremental updates
work across machines and CI runs.

## S3 Bucket Structure

After deployment, your S3 bucket looks like this:

```
s3://your-bucket/
├── latest/
│   └── your-project.db              # Current database (overwritten each deploy)
├── assets/
│   └── databases/
│       └── your-project/
│           └── metadata.json         # Datasette metadata
└── archives/
    ├── 2026-03-20/
    │   └── your-project.db           # Dated backup
    ├── 2026-03-21/
    │   └── your-project.db
    └── 2026-03-22/
        └── your-project.db
```

Datasette serves from `latest/`. Archives give you rollback capability.

## GitHub Secrets

Every workflow needs these secrets configured in `Settings > Secrets and variables > Actions`:

```
# S3 deployment (required)
S3_BUCKET=your-bucket-name
AWS_ACCESS_KEY_ID=your-access-key
AWS_SECRET_ACCESS_KEY=your-secret-key
S3_ENDPOINT_URL=https://your-endpoint    # Only for non-AWS S3 (Contabo, DigitalOcean, etc.)

# API keys (varies by resource)
JINA_API_TOKEN=your-jina-token           # For RSS content extraction
LLM_BASE_URL=http://localhost:11434/v1   # OpenAI-compatible LLM server
LLM_API_KEY=your-api-key               # Optional for local servers (Ollama, vLLM)
LLM_MODEL=llama3.1                     # Model name for summaries and filtering
DOCLING_SERVE_URL=http://your-server     # For PDF extraction
```

## Workflow Templates by Cadence

### Daily Incremental Sync (Tier 1)

For resources that update frequently — news feeds, judgment databases, daily publications.
This is the workhorse workflow. It syncs the existing database from S3, builds only new
records, deploys, and creates a backup.

From the reference project's `sync-headlines.yml`:

```yaml
name: Sync <Resource> Database

on:
  schedule:
    - cron: '0 3 * * *'  # Daily at 3 AM UTC
  workflow_dispatch:
    inputs:
      force_rebuild:
        description: 'Force full rebuild (ignore existing data)'
        required: false
        default: false
        type: boolean
      setup_fts:
        description: 'Set up full-text search (FTS) indexes'
        required: false
        default: false
        type: boolean

jobs:
  sync:
    runs-on: ubuntu-latest
    timeout-minutes: 30

    steps:
    - name: Checkout repository
      uses: actions/checkout@v4

    - name: Set up Python
      uses: actions/setup-python@v4
      with:
        python-version: '3.12'

    - name: Install uv
      uses: astral-sh/setup-uv@v3
      with:
        version: "latest"

    - name: Install dependencies
      run: uv sync

    - name: Build database
      env:
        JINA_API_TOKEN: ${{ secrets.JINA_API_TOKEN }}
        LLM_BASE_URL: ${{ secrets.LLM_BASE_URL }}
        LLM_API_KEY: ${{ secrets.LLM_API_KEY }}
        LLM_MODEL: ${{ secrets.LLM_MODEL }}
        S3_BUCKET: ${{ secrets.S3_BUCKET }}
        AWS_ACCESS_KEY_ID: ${{ secrets.AWS_ACCESS_KEY_ID }}
        AWS_SECRET_ACCESS_KEY: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
        S3_ENDPOINT_URL: ${{ secrets.S3_ENDPOINT_URL }}
      run: |
        FTS_FLAG=""
        if [ "${{ github.event.inputs.setup_fts }}" = "true" ]; then
          FTS_FLAG="--setup-fts"
        fi

        if [ "${{ github.event.inputs.force_rebuild }}" = "true" ]; then
          echo "Force rebuild — building from scratch"
          uv run zeeker build <resource_name> $FTS_FLAG
        else
          echo "Incremental sync from S3"
          uv run zeeker build --sync-from-s3 <resource_name> $FTS_FLAG
        fi

        # Validate
        if [ ! -f "<project>.db" ]; then
          echo "Error: Database file not created"
          exit 1
        fi

        DB_SIZE=$(du -h <project>.db | cut -f1)
        echo "Database updated successfully (${DB_SIZE})"

    - name: Deploy to S3
      env:
        S3_BUCKET: ${{ secrets.S3_BUCKET }}
        AWS_ACCESS_KEY_ID: ${{ secrets.AWS_ACCESS_KEY_ID }}
        AWS_SECRET_ACCESS_KEY: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
        S3_ENDPOINT_URL: ${{ secrets.S3_ENDPOINT_URL }}
      run: |
        uv run zeeker deploy
        echo "Deployment completed"

    - name: Create backup archive
      env:
        S3_BUCKET: ${{ secrets.S3_BUCKET }}
        AWS_ACCESS_KEY_ID: ${{ secrets.AWS_ACCESS_KEY_ID }}
        AWS_SECRET_ACCESS_KEY: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
        S3_ENDPOINT_URL: ${{ secrets.S3_ENDPOINT_URL }}
      run: uv run zeeker backup

    - name: Upload artifacts
      uses: actions/upload-artifact@v4
      if: always()
      with:
        name: sync-${{ github.run_number }}
        path: |
          <project>.db
          metadata.json
        retention-days: 7

    - name: Summary
      if: success()
      run: |
        echo "## ✅ Sync Successful" >> $GITHUB_STEP_SUMMARY
        echo "**Trigger:** ${{ github.event_name }}" >> $GITHUB_STEP_SUMMARY
        echo "**Timestamp:** $(date -u)" >> $GITHUB_STEP_SUMMARY

        uv run python -c "
        import sqlite3, os
        conn = sqlite3.connect('<project>.db')
        cursor = conn.cursor()
        cursor.execute('SELECT name FROM sqlite_master WHERE type=\"table\" AND name NOT LIKE \"sqlite_%\"')
        for (table,) in cursor.fetchall():
            cursor.execute(f'SELECT COUNT(*) FROM {table}')
            count = cursor.fetchone()[0]
            print(f'- {table}: {count:,} records')
        size_mb = os.path.getsize('<project>.db') / (1024*1024)
        print(f'- **Database size:** {size_mb:.1f} MB')
        " >> $GITHUB_STEP_SUMMARY
```

**Key points:**

- `--sync-from-s3` is the default — only force rebuild when explicitly requested
- All API keys passed as env vars from secrets
- Database validated before deployment (file exists check)
- Backup created after every successful deploy
- Artifacts uploaded for debugging even on failure (`if: always()`)
- Step summary shows record counts and database size

### Manual Build and Deploy (Tier 3/4)

For resources that update infrequently — reference content, legislation, PDF collections.
Also useful for first-time full builds or one-off rebuilds.

From the reference project's `build-and-deploy.yml`:

```yaml
name: Build and Deploy Database

on:
  workflow_dispatch:
    inputs:
      resource:
        description: 'Resource to build (leave empty for all)'
        required: false
        default: ''
      force_rebuild:
        description: 'Force full rebuild'
        required: false
        default: false
        type: boolean
      setup_fts:
        description: 'Set up FTS indexes'
        required: false
        default: false
        type: boolean

jobs:
  build-and-deploy:
    runs-on: ubuntu-latest
    timeout-minutes: 45

    steps:
    - name: Checkout repository
      uses: actions/checkout@v4

    - name: Set up Python
      uses: actions/setup-python@v4
      with:
        python-version: '3.12'

    - name: Install uv
      uses: astral-sh/setup-uv@v3
      with:
        version: "latest"

    - name: Install dependencies
      run: uv sync

    - name: Build database
      env:
        JINA_API_TOKEN: ${{ secrets.JINA_API_TOKEN }}
        LLM_BASE_URL: ${{ secrets.LLM_BASE_URL }}
        LLM_API_KEY: ${{ secrets.LLM_API_KEY }}
        LLM_MODEL: ${{ secrets.LLM_MODEL }}
        DOCLING_SERVE_URL: ${{ secrets.DOCLING_SERVE_URL }}
        S3_BUCKET: ${{ secrets.S3_BUCKET }}
        AWS_ACCESS_KEY_ID: ${{ secrets.AWS_ACCESS_KEY_ID }}
        AWS_SECRET_ACCESS_KEY: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
        S3_ENDPOINT_URL: ${{ secrets.S3_ENDPOINT_URL }}
      run: |
        FTS_FLAG=""
        if [ "${{ github.event.inputs.setup_fts }}" = "true" ]; then
          FTS_FLAG="--setup-fts"
        fi

        if [ -n "${{ github.event.inputs.resource }}" ]; then
          echo "Building: ${{ github.event.inputs.resource }}"
          uv run zeeker build ${{ github.event.inputs.resource }} $FTS_FLAG
        else
          echo "Building all resources"
          uv run zeeker build $FTS_FLAG
        fi

    - name: Validate database
      run: |
        uv run python -c "
        import sqlite3, sys
        conn = sqlite3.connect('<project>.db')
        cursor = conn.cursor()
        cursor.execute('SELECT name FROM sqlite_master WHERE type=\"table\"')
        tables = [row[0] for row in cursor.fetchall()]
        print(f'Tables: {tables}')
        total = 0
        for t in tables:
            if not t.startswith('sqlite_'):
                cursor.execute(f'SELECT COUNT(*) FROM {t}')
                count = cursor.fetchone()[0]
                print(f'{t}: {count}')
                total += count
        if total == 0:
            print('Error: No data')
            sys.exit(1)
        print(f'Validation passed: {total} records')
        "

    - name: Deploy to S3
      env:
        S3_BUCKET: ${{ secrets.S3_BUCKET }}
        AWS_ACCESS_KEY_ID: ${{ secrets.AWS_ACCESS_KEY_ID }}
        AWS_SECRET_ACCESS_KEY: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
        S3_ENDPOINT_URL: ${{ secrets.S3_ENDPOINT_URL }}
      run: uv run zeeker deploy

    - name: Create backup
      env:
        S3_BUCKET: ${{ secrets.S3_BUCKET }}
        AWS_ACCESS_KEY_ID: ${{ secrets.AWS_ACCESS_KEY_ID }}
        AWS_SECRET_ACCESS_KEY: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
        S3_ENDPOINT_URL: ${{ secrets.S3_ENDPOINT_URL }}
      run: uv run zeeker backup

    - name: Upload artifacts
      uses: actions/upload-artifact@v4
      if: always()
      with:
        name: build-${{ github.run_number }}
        path: |
          <project>.db
          metadata.json
        retention-days: 14
```

**Differences from daily sync:**

- Manual trigger only (`workflow_dispatch`) — no cron schedule
- Can target a specific resource or build all
- Longer timeout (45 min vs 30 min) for heavier builds
- No `--sync-from-s3` by default — manual builds are typically full rebuilds
- Longer artifact retention (14 days vs 7)

### Health Check (Post-Deploy)

Validates the deployed database after any successful deployment. Triggers automatically
via `workflow_run`.

```yaml
name: Database Health Check

on:
  workflow_dispatch:
  workflow_run:
    workflows: ["Sync <Resource> Database", "Build and Deploy Database"]
    types: [completed]

jobs:
  health-check:
    runs-on: ubuntu-latest
    timeout-minutes: 10

    steps:
    - name: Checkout repository
      uses: actions/checkout@v4

    - name: Set up Python
      uses: actions/setup-python@v4
      with:
        python-version: '3.12'

    - name: Install uv
      uses: astral-sh/setup-uv@v3
      with:
        version: "latest"

    - name: Install dependencies
      run: uv sync

    - name: Download database from S3
      env:
        S3_BUCKET: ${{ secrets.S3_BUCKET }}
        AWS_ACCESS_KEY_ID: ${{ secrets.AWS_ACCESS_KEY_ID }}
        AWS_SECRET_ACCESS_KEY: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
        S3_ENDPOINT_URL: ${{ secrets.S3_ENDPOINT_URL }}
      run: |
        uv run python -c "
        import os, boto3
        session = boto3.Session(
            aws_access_key_id=os.environ['AWS_ACCESS_KEY_ID'],
            aws_secret_access_key=os.environ['AWS_SECRET_ACCESS_KEY']
        )
        s3_kwargs = {}
        if os.environ.get('S3_ENDPOINT_URL'):
            s3_kwargs['endpoint_url'] = os.environ['S3_ENDPOINT_URL']
        s3 = session.client('s3', **s3_kwargs)
        s3.download_file(os.environ['S3_BUCKET'], 'latest/<project>.db', '<project>.db')
        print('Downloaded database from S3')
        "

    - name: Run health checks
      run: |
        uv run python -c "
        import sqlite3, sys

        conn = sqlite3.connect('<project>.db')
        cursor = conn.cursor()

        # Check tables exist
        cursor.execute('SELECT name FROM sqlite_master WHERE type=\"table\" AND name NOT LIKE \"sqlite_%\"')
        tables = [row[0] for row in cursor.fetchall()]
        print(f'✅ Tables: {tables}')

        # Check data exists
        total = 0
        for table in tables:
            cursor.execute(f'SELECT COUNT(*) FROM {table}')
            count = cursor.fetchone()[0]
            print(f'  {table}: {count:,} records')
            total += count

        if total == 0:
            print('❌ No data found')
            sys.exit(1)

        # Check sample data quality
        for table in tables:
            cursor.execute(f'SELECT * FROM {table} LIMIT 1')
            row = cursor.fetchone()
            if row:
                print(f'✅ {table} has valid data')
            else:
                print(f'❌ {table} is empty')
                sys.exit(1)

        print(f'✅ All checks passed ({total:,} total records)')
        "
```

**What health checks verify:**

- Database file downloaded successfully from S3
- Expected tables exist
- Tables have data (not empty after a failed build)
- Sample rows look valid

## Choosing Workflows for Your Project

The cadence tier you recommended during resource creation maps to which workflows to generate:

| Cadence | Workflows to generate |
|---------|----------------------|
| Tier 1 (Daily) | Daily sync + health check |
| Tier 2 (Weekly) | Weekly sync (cron `0 3 * * 1`) + health check |
| Tier 3 (Monthly) | Manual build-and-deploy + health check |
| Tier 4 (One-shot) | Manual build-and-deploy only |

For projects with multiple resources at different cadences (e.g., daily headlines + monthly
reference content), generate separate sync workflows for each resource, plus one manual
build-and-deploy for the full project.

## Batch Crawl Deployment

For large archive crawls using the checkpoint/resume pattern (see `scraping-strategy.md`),
the daily sync workflow doubles as the batch runner. Each day it:

1. Syncs the existing DB from S3
2. Runs the resource — which picks up from its checkpoint and processes the next batch
3. Deploys the updated DB back to S3
4. Creates a dated backup

The database grows incrementally over days/weeks. Once the archive crawl completes (checkpoint
clears itself), the same workflow seamlessly transitions to incremental mode — fetching only
new items.

Example: an eLitigation judgments resource with `MAX_PAGES_PER_RUN=50`:

- **Week 1–3**: Daily workflow processes ~50 listing pages per run, discovering ~500 judgments
- **Week 4**: Discovery completes, checkpoint clears, workflow shifts to content backfill
- **Week 5+**: Content backfill processes ~50 PDFs per run via Docling
- **After backfill**: Same workflow now runs in pure incremental mode — checks page 1 for new
  judgments, processes any found, deploys

No workflow changes needed. The resource code handles the mode transition via its checkpoint
and backfill logic.

## Timeout Guidelines

Set `timeout-minutes` based on what the workflow does:

| Workflow type | Timeout | Rationale |
|---------------|---------|-----------|
| Daily sync (RSS, API) | 30 min | Feed fetch + Jina + LLM inference for ~20 items |
| Daily sync (scraping batch) | 45 min | 50 pages of listing + detail fetching |
| Manual full build | 60 min | All resources, potentially from scratch |
| Health check | 10 min | Download + validation only |
| Content backfill (Docling) | 90 min | 50 PDFs through Docling can be slow |

If a workflow consistently hits its timeout, increase the limit or reduce the batch size in
the resource configuration.

## Workflow File Location

Place workflow files in `.github/workflows/`:

```
.github/
└── workflows/
    ├── sync-<resource>.yml           # Daily/weekly sync for each Tier 1/2 resource
    ├── build-and-deploy.yml          # Manual full build for all resources
    └── health-check.yml              # Post-deploy validation
```

The skill should generate these alongside the resource module. Add `.github/workflows/` to
the project scaffold when creating a new project.

## What to Generate

When creating a new resource, generate the appropriate workflow based on the cadence tier:

1. **Always**: Add the workflow file to `.github/workflows/`
2. **Always**: Document the required GitHub Secrets in CLAUDE.md
3. **Tier 1/2**: Generate a sync workflow with the appropriate cron schedule
4. **Tier 3/4**: Generate a manual build-and-deploy workflow
5. **All tiers**: Generate a health check workflow (or update the existing one to include the
   new resource's workflow in its `workflow_run` triggers)

Remind the user to configure GitHub Secrets before the first workflow run.
