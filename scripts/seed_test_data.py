from __future__ import annotations

import os

import boto3


def _get_region() -> str:
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    if not region:
        raise SystemExit("AWS_REGION is required")
    return region


def main() -> None:
    table_name = os.environ.get("PRINCIPAL_TABLE")
    if not table_name:
        raise SystemExit("PRINCIPAL_TABLE is required")

    dynamodb = boto3.resource("dynamodb", region_name=_get_region())
    table = dynamodb.Table(table_name)

    principals = {
        "alice": ["Document:doc123:read", "Document:doc123:write"],
        "admin": ["Document:doc123:delete"],
    }

    for principal, scopes in principals.items():
        table.put_item(Item={"principal": principal, "scopes": scopes})


if __name__ == "__main__":
    main()
