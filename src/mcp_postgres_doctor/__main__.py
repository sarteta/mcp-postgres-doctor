"""CLI entry point: `python -m mcp_postgres_doctor` or `mcp-postgres-doctor`."""
from .server import main

if __name__ == "__main__":
    main()
else:
    # Allow `mcp-postgres-doctor` script to import this
    pass
