from __future__ import annotations

import json
import os

import boto3


def main() -> None:
    function_name = os.environ.get("COMPILER_FUNCTION_NAME")
    if not function_name:
        raise SystemExit("COMPILER_FUNCTION_NAME is required")
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    if not region:
        raise SystemExit("AWS_REGION is required")

    client = boto3.client("lambda", region_name=region)
    response = client.invoke(
        FunctionName=function_name,
        InvocationType="RequestResponse",
        Payload=json.dumps({}).encode("utf-8"),
    )
    payload = response.get("Payload")
    if payload:
        print(payload.read().decode("utf-8"))


if __name__ == "__main__":
    main()
