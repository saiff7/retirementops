# MCP Inspector transcript (M2 acceptance)

Recorded on 2026-11-26 (re-recorded after the independent review fixes) with the official **MCP Inspector CLI 2.14.0**
(`npx -y @modelcontextprotocol/inspector@2.14.0 --cli`) against the Service Operations server and
the dev database seeded with seed 42. The token is a 15-minute mock-identity dev token for `op.alex`,
redacted as `<dev-token>`. Timestamps are replaced with `<timestamp>`. Output is abbreviated where
marked. Regenerate with the commands in [mcp-service-ops.md](mcp-service-ops.md#verify-with-mcp-inspector).

## 1. stdio: `tools/list`

```text
@modelcontextprotocol/inspector@2.14.0 --cli -e RETIREMENTOPS_MCP_TOKEN=<dev-token> --cwd "$PWD" --method tools/list -- uv run python -m retirementops.mcp_servers.service_ops
```
Exit 0. 10 tools advertised (no close, send, approve or financial-write tool):

```json
[
  {
    "name": "search_tickets",
    "readOnlyHint": true,
    "destructiveHint": false
  },
  {
    "name": "get_ticket_details",
    "readOnlyHint": true,
    "destructiveHint": false
  },
  {
    "name": "get_plan_details",
    "readOnlyHint": true,
    "destructiveHint": false
  },
  {
    "name": "get_participant_enrollment",
    "readOnlyHint": true,
    "destructiveHint": false
  },
  {
    "name": "get_payroll_contributions",
    "readOnlyHint": true,
    "destructiveHint": false
  },
  {
    "name": "get_applicable_plan_rule",
    "readOnlyHint": true,
    "destructiveHint": false
  },
  {
    "name": "get_policy_snippets",
    "readOnlyHint": true,
    "destructiveHint": false
  },
  {
    "name": "create_reply_draft",
    "readOnlyHint": false,
    "destructiveHint": false
  },
  {
    "name": "propose_ticket_assignment",
    "readOnlyHint": false,
    "destructiveHint": false
  },
  {
    "name": "get_ticket_action_status",
    "readOnlyHint": true,
    "destructiveHint": false
  }
]
```

Input schema of `create_reply_draft` (as advertised; every object has `additionalProperties: false`):

```json
{
  "type": "object",
  "properties": {
    "ticket_id": {
      "description": "Ticket id, e.g. ZD-1001",
      "pattern": "^ZD-\\d{4,6}$",
      "title": "Ticket Id",
      "type": "string"
    },
    "body": {
      "maxLength": 10000,
      "minLength": 1,
      "title": "Body",
      "type": "string"
    },
    "evidence_refs": {
      "description": "Must include the ticket being answered",
      "items": {
        "$ref": "#/$defs/EvidenceRefIn"
      },
      "maxItems": 50,
      "minItems": 1,
      "title": "Evidence Refs",
      "type": "array"
    },
    "skill_version": {
      "description": "e.g. service-ticket-triage@1.0.0",
      "pattern": "^[a-z0-9-]{1,64}@\\d+\\.\\d+\\.\\d+$",
      "title": "Skill Version",
      "type": "string"
    },
    "idempotency_key": {
      "anyOf": [
        {
          "pattern": "^[A-Za-z0-9:_-]{8,128}$",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "description": "Optional. A retry with the same key and content returns the original draft.",
      "title": "Idempotency Key"
    }
  },
  "required": [
    "ticket_id",
    "body",
    "evidence_refs",
    "skill_version"
  ],
  "$defs": {
    "EvidenceRefIn": {
      "additionalProperties": false,
      "properties": {
        "system": {
          "pattern": "^[a-z_]{1,32}$",
          "title": "System",
          "type": "string"
        },
        "record_type": {
          "pattern": "^[a-z_]{1,32}$",
          "title": "Record Type",
          "type": "string"
        },
        "record_id": {
          "pattern": "^[A-Za-z0-9#-]{1,64}$",
          "title": "Record Id",
          "type": "string"
        },
        "content_hash": {
          "pattern": "^[0-9a-f]{64}$",
          "title": "Content Hash",
          "type": "string"
        }
      },
      "required": [
        "system",
        "record_type",
        "record_id",
        "content_hash"
      ],
      "title": "EvidenceRefIn",
      "type": "object"
    }
  },
  "additionalProperties": false,
  "title": "CreateReplyDraftIn"
}
```

## 2. stdio: `tools/call get_plan_details`

```text
@modelcontextprotocol/inspector@2.14.0 --cli -e RETIREMENTOPS_MCP_TOKEN=<dev-token> --cwd "$PWD" --method tools/call --tool-name get_plan_details --tool-arg plan_id=PLAN-001 -- uv run python -m retirementops.mcp_servers.service_ops
```
Exit 0. Structured result:

```json
{
  "plan_id": "PLAN-001",
  "name": "Alder Ridge 401(k) Demo Plan",
  "currency": "USD",
  "source": {
    "system": "planadmin_mock",
    "record_type": "plan",
    "record_id": "PLAN-001",
    "record_version": "1",
    "retrieved_at": "<timestamp>",
    "content_hash": "fa791c116917b84ffb73d4e1f68f7645b352b98163f949c34800002470e3994d"
  }
}
```

## 3. stdio: `tools/call close_ticket`

```text
@modelcontextprotocol/inspector@2.14.0 --cli -e RETIREMENTOPS_MCP_TOKEN=<dev-token> --cwd "$PWD" --method tools/call --tool-name close_ticket --tool-arg ticket_id=ZD-1001 -- uv run python -m retirementops.mcp_servers.service_ops
```
Exit 5. The tool does not exist, so the Inspector refuses before sending:

```json
{"error":{"code":"tool_not_found","message":"Tool 'close_ticket' not found on server."}}
```
(The server's own refusal of unknown tool names, with an audit record, is covered by
`test_unknown_and_forbidden_tool_names_are_refused_and_audited`.)

## 4. stdio: extra argument rejected by the server

```text
@modelcontextprotocol/inspector@2.14.0 --cli -e RETIREMENTOPS_MCP_TOKEN=<dev-token> --cwd "$PWD" --method tools/call --tool-name get_ticket_details --tool-arg ticket_id=ZD-1001 tenant_id=TEN-COBALT -- uv run python -m retirementops.mcp_servers.service_ops
```
Exit 5. The caller tried to widen its own tenant scope with an extra field:

```json
"{\n  \"content\": [\n    {\n      \"type\": \"text\",\n      \"text\": \"{\\\"error\\\": \\\"invalid_arguments\\\", \\\"message\\\": \\\"invalid: tenant_id:extra_forbidden\\\"}\"\n    }\n  ],\n  \"structuredContent\": {\n    \"error\": \"invalid_arguments\",\n    \"message\": \"invalid: tenant_id:extra_forbidden\"\n  },\n  \"isError\": true\n}\n\n{\"correlation_id\": \"<correlation-id>\", \"event\": \"mcp_tool_call\", \"latency_ms\": 3.96, \"level\": \"info\", \"outcome\": \"invalid_arguments\", \"policy_decision\": \"deny\", \"principal\": \"op.alex\", \"tenants\": [\"TEN-ALDER\", \"TEN-BIRCH\"], \"timestamp\": \"<timestamp>\", \"tool\": \"get_ticket_details\"}\n{\"error\":{\"code\":\"tool_is_error\",\"message\":\"Tool 'get_ticket_details' returned isError:true.\"}}"
```

## 5. Streamable HTTP: `tools/call get_policy_snippets` with a bearer token

```text
@modelcontextprotocol/inspector@2.14.0 --cli http://127.0.0.1:8010/mcp --transport http --header "Authorization: Bearer <dev-token>" --method tools/call --tool-name get_policy_snippets --tool-arg category=ESCALATION
```
Exit 0. Only policy versions in force are returned:

```json
{
  "snippets": [
    {
      "snippet_id": "POL-013",
      "category": "ESCALATION",
      "title": "Legal matters and complaints",
      "text": "Court orders, legal questions and formal complaints go to human triage without a drafted reply.",
      "version": "v1",
      "effective_from": "2025-01-01",
      "effective_to": null,
      "fictional": true,
      "source": {
        "system": "servicekb_mock",
        "record_type": "policy_snippet",
        "record_id": "POL-013",
        "record_version": "1",
        "retrieved_at": "<timestamp>",
        "content_hash": "dd6eac6cca0bc8091be0b918995c67df9867eaf7a128fad54786a7c0e04f66a2"
      }
    },
    {
      "snippet_id": "POL-014",
      "category": "ESCALATION",
      "title": "Ticket closure",
      "text": "Tickets are closed only by a person in the ticketing system, never by automation.",
      "version": "v1",
      "effective_from": "2025-01-01",
      "effective_to": null,
      "fictional": true,
      "source": {
        "system": "servicekb_mock",
        "record_type": "policy_snippet",
        "record_id": "POL-014",
        "record_version": "1",
        "retrieved_at": "<timestamp>",
        "content_hash": "ae850dc09953a74c577df412adf86fece008c707255a15f394814987d36592fc"
      }
    }
  ]
}
```

## 6. Streamable HTTP: no token

```text
@modelcontextprotocol/inspector@2.14.0 --cli http://127.0.0.1:8010/mcp --transport http --method tools/list
```
Exit 3. The server answers `401` with `WWW-Authenticate: Bearer`, so the Inspector
starts an OAuth flow, which this local endpoint does not offer (OAuth arrives at the Cloudflare edge
in M6):

```json
{"error":{"code":"auth_required","message":"Interactive OAuth requires a TTY on stdin or stderr (or MCP_AUTO_OPEN_ENABLED=true). For CI/non-interactive runs use --stored-auth-only."}}
```
