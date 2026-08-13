"""Platform adapters for market validation data sources.

Each adapter is fail-open: network or parsing trouble yields empty results
(logged), never an exception to the caller. Adding a platform means adding a
module here; the orchestrator consumes whatever adapters return.
"""
