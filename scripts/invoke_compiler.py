#!/usr/bin/env python3
"""Trigger the policy compiler Lambda function via API Gateway."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
import json
from urllib import request
from urllib.error import HTTPError, URLError


def main() -> None:
    """Trigger policy compiler Lambda function."""
    api_url = os.environ.get("RAJA_API_URL")
    if not api_url:
        repo_root = Path(__file__).resolve().parents[1]
        outputs_path = repo_root / "infra" / "cdk-outputs.json"
        if outputs_path.is_file():
            try:
                outputs = json.loads(outputs_path.read_text())
                api_url = outputs.get("RajaServicesStack", {}).get("ApiUrl")
            except json.JSONDecodeError:
                api_url = None
    if not api_url:
        print("✗ RAJA_API_URL environment variable is required", file=sys.stderr)
        sys.exit(1)

    url = f"{api_url.rstrip('/')}/compile"

    print(f"{'='*60}")
    print("Triggering policy compiler")
    print(f"URL: {url}")
    print(f"{'='*60}\n")

    print("→ Sending compile request...")

    start_time = time.time()

    try:
        req = request.Request(url, method="POST")
        with request.urlopen(req, timeout=30) as response:
            if response.status != 200:
                print(f"✗ Unexpected status code: {response.status}", file=sys.stderr)
                sys.exit(1)

            body = response.read().decode("utf-8")

        elapsed = time.time() - start_time

        print(f"✓ Compilation completed in {elapsed:.2f}s\n")
        print(f"{'='*60}")
        print("Response:")
        print(f"{'='*60}")
        print(body)

    except HTTPError as e:
        elapsed = time.time() - start_time
        print(f"✗ HTTP error after {elapsed:.2f}s: {e.code} {e.reason}", file=sys.stderr)
        try:
            error_body = e.read().decode("utf-8")
            print(f"\nResponse body:\n{error_body}", file=sys.stderr)
        except Exception:
            pass
        sys.exit(1)
    except URLError as e:
        elapsed = time.time() - start_time
        print(f"✗ Network error after {elapsed:.2f}s: {e.reason}", file=sys.stderr)
        sys.exit(1)
    except TimeoutError:
        print("✗ Request timed out after 30s", file=sys.stderr)
        print("  The compiler may still be running. Check CloudWatch logs.", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        elapsed = time.time() - start_time
        print(f"✗ Unexpected error after {elapsed:.2f}s: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
