"""
Drop-in replacement for utils/airtable_helper.upsert_live_records.
Appends market-depth snapshot records to a BigQuery table instead of
Airtable, so the full time series persists without hitting a record cap.

Setup (one-time):
    pip install google-cloud-bigquery
    Reuses the same GOOGLE_CREDENTIALS service account secret your
    Sheets-writing screener already uses — just make sure that service
    account also has the "BigQuery Data Editor" and "BigQuery Job User"
    roles in your GCP project (IAM & Admin > your project).

Usage in your existing script:
    from utils.bigquery_helper import write_snapshot_records
    ...
    write_snapshot_records(all_records)   # was: upsert_live_records(all_records)
"""
import os
import json
from google.cloud import bigquery
from google.oauth2.service_account import Credentials

PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "").strip()
DATASET_ID = os.environ.get("BQ_DATASET_ID", "market_depth").strip()
TABLE_ID   = os.environ.get("BQ_TABLE_ID", "snapshots").strip()

SCHEMA = [
    bigquery.SchemaField("Snapshot_Time", "TIMESTAMP"),
    bigquery.SchemaField("Session", "STRING"),
    bigquery.SchemaField("Category", "STRING"),
    bigquery.SchemaField("SYMBOL", "STRING"),
    bigquery.SchemaField("security_id", "STRING"),
    bigquery.SchemaField("Bid", "FLOAT64"),
    bigquery.SchemaField("Ask", "FLOAT64"),
    bigquery.SchemaField("Spread", "FLOAT64"),
    bigquery.SchemaField("LTP", "FLOAT64"),
    bigquery.SchemaField("Volume", "INT64"),
]


def _get_client() -> bigquery.Client:
    creds_json = os.environ.get("GOOGLE_CREDENTIALS", "").strip()
    if not creds_json:
        raise RuntimeError("Missing GOOGLE_CREDENTIALS secret")
    creds_dict = json.loads(creds_json)
    credentials = Credentials.from_service_account_info(
        creds_dict,
        scopes=["https://www.googleapis.com/auth/bigquery"]
    )
    project = PROJECT_ID or creds_dict.get("project_id")
    return bigquery.Client(project=project, credentials=credentials)


def _ensure_table(client: bigquery.Client) -> str:
    dataset_ref = bigquery.DatasetReference(client.project, DATASET_ID)
    try:
        client.get_dataset(dataset_ref)
    except Exception:
        print(f"[BIGQUERY] Creating dataset {DATASET_ID}...", flush=True)
        client.create_dataset(bigquery.Dataset(dataset_ref))

    table_ref = dataset_ref.table(TABLE_ID)
    try:
        client.get_table(table_ref)
    except Exception:
        print(f"[BIGQUERY] Creating table {TABLE_ID}...", flush=True)
        table = bigquery.Table(table_ref, schema=SCHEMA)
        # Partition by day so queries over a date range only scan what's
        # needed — keeps you well inside the free 1 TB/month query tier
        # even as history grows.
        table.time_partitioning = bigquery.TimePartitioning(
            type_=bigquery.TimePartitioningType.DAY,
            field="Snapshot_Time"
        )
        client.create_table(table)

    return f"{client.project}.{DATASET_ID}.{TABLE_ID}"


def write_snapshot_records(records: list[dict]):
    """
    Append snapshot records to BigQuery. Same input shape as
    upsert_live_records: a list of dicts with keys matching SCHEMA above.
    """
    if not records:
        print("[BIGQUERY] No records to write.", flush=True)
        return

    client = _get_client()
    full_table_id = _ensure_table(client)

    # Normalize timestamps to ISO strings; BigQuery's JSON loader expects that.
    rows = []
    for r in records:
        row = dict(r)
        ts = row.get("Snapshot_Time")
        if hasattr(ts, "isoformat"):
            row["Snapshot_Time"] = ts.isoformat()
        rows.append(row)

    job_config = bigquery.LoadJobConfig(
        schema=SCHEMA,
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
    )
    load_job = client.load_table_from_json(rows, full_table_id, job_config=job_config)
    load_job.result()  # wait for completion, raises on error

    print(f"[BIGQUERY] Appended {len(rows)} rows to {full_table_id}", flush=True)
