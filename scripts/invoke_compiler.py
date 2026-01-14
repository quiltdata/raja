from __future__ import annotations

import os
from urllib import request



def main() -> None:
    api_url = os.environ.get("RAJA_API_URL")
    if not api_url:
        raise SystemExit("RAJA_API_URL is required")

    url = f"{api_url.rstrip('/')}/compile"
    req = request.Request(url, method="POST")
    with request.urlopen(req) as response:
        body = response.read().decode("utf-8")
    print(body)


if __name__ == "__main__":
    main()
