"""M3 agent runtime: skill loader, model providers and (stage 6) the explicit orchestrator.

The model is never a security boundary. This package only *asks* a model for structured answers;
permissions, tenant isolation, tool allowlists, calculations and validation stay in trusted code
(services, MCP server, database).
"""
