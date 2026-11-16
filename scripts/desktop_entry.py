"""Minimal entry used by the frozen macOS worker."""
from presscheck.desktop_worker import main

if __name__ == "__main__":
    import json
    import sys
    try:
        main()
    except Exception as error:
        print(json.dumps({"error": str(error)[:500]}), file=sys.stderr)
        raise SystemExit(1)
