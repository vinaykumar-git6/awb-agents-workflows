"""Verify the AWB tables exist on the server."""
from __future__ import annotations

import subprocess

import psycopg
from azure.identity import AzureCliCredential

HOST = "devpostgresvinay.postgres.database.azure.com"
SCOPE = "https://ossrdbms-aad.database.windows.net/.default"

token = AzureCliCredential().get_token(SCOPE).token
user = subprocess.check_output(
    [
        "az", "postgres", "flexible-server", "microsoft-entra-admin", "list",
        "-g", "azure-vk-rg", "-s", "devpostgresvinay",
        "--query", "[0].principalName", "-o", "tsv",
    ],
    text=True, shell=True,
).strip()

with psycopg.connect(
    host=HOST, dbname="postgres", user=user, port=5432,
    sslmode="require", password=token, autocommit=True,
) as conn:
    rows = conn.execute(
        """
        SELECT table_schema, table_name FROM information_schema.tables
        WHERE table_schema = 'skycargo'
          AND table_name IN ('awb_processing', 'awb_analytics')
        ORDER BY table_name
        """
    ).fetchall()
    print("Tables found:", [f"{r[0]}.{r[1]}" for r in rows])
