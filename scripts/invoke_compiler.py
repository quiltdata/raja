from __future__ import annotations

import json
import os

import boto3


def main() -> None:
    function_name = os.environ.get("COMPILER_FUNCTION_NAME")
    if not function_name:
        raise SystemExit("COMPILER_FUNCTION_NAME is required")

    client = boto3.client("lambda")
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
