"""Linear GraphQL client: agent activities + issue context.

Contract confirmed from Linear's agent docs (https://linear.app/developers/agent-interaction):

    mutation AgentActivityCreate($input: AgentActivityCreateInput!) {
      agentActivityCreate(input: $input) { success agentActivity { id } }
    }
    variables: {"input": {"agentSessionId": "...", "content": {"type": "...", ...}}}

`content.type` is validated server-side; invalid shapes are rejected. The five
allowed types are thought, action, response, error and prompt (prompt is
user-generated, so we never send it).

Transport is injectable so the client is testable with no network.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

API_URL = "https://api.linear.app/graphql"

ACTIVITY_CREATE = """
mutation AgentActivityCreate($input: AgentActivityCreateInput!) {
  agentActivityCreate(input: $input) {
    success
    agentActivity { id }
  }
}
"""

AGENT_SESSION_UPDATE = """
mutation AgentSessionUpdate($id: String!, $input: AgentSessionUpdateInput!) {
  agentSessionUpdate(id: $id, input: $input) { success }
}
"""

ISSUE_QUERY = """
query IssueContext($id: String!) {
  issue(id: $id) {
    identifier title description url
    comments(first: 25) { nodes { body user { name } createdAt } }
  }
}
"""

# Activity types Linear accepts on agentActivityCreate.
THOUGHT = "thought"
ACTION = "action"
RESPONSE = "response"
ERROR = "error"


class LinearError(RuntimeError):
    pass


def urllib_transport(url, headers, payload, timeout):
    """Default transport: one JSON POST. Returns (status, parsed_json)."""
    req = urllib.request.Request(url, data=payload, method="POST")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, {"error": "http_%s" % exc.code}


class LinearClient:
    def __init__(self, api_key, url=API_URL, timeout=45, transport=None):
        if not api_key:
            raise LinearError("a Linear credential is required (app token or API key)")
        self.api_key = api_key
        self.url = url
        self.timeout = timeout
        self._transport = transport or urllib_transport

    def _gql(self, query, variables):
        body = json.dumps({"query": query, "variables": variables}).encode("utf-8")
        token = self.api_key() if callable(self.api_key) else self.api_key
        status, payload = self._transport(
            self.url,
            {"Content-Type": "application/json", "Authorization": token},
            body,
            self.timeout,
        )
        if status != 200:
            raise LinearError("linear HTTP %s" % status)
        if payload.get("errors"):
            raise LinearError("linear graphql errors: %s" % payload["errors"][:1])
        return payload.get("data") or {}

    # -- activities ------------------------------------------------------
    def post_activity(self, session_id, kind, body=None, **fields):
        """Emit one agent activity.

        thought/response/error carry `body`. action carries `action` +
        `parameter`, with an optional `result` once complete.
        """
        content = {"type": kind}
        if kind == ACTION:
            content["action"] = fields.pop("action", "Working")
            content["parameter"] = fields.pop("parameter", "")
            if fields.get("result"):
                content["result"] = fields.pop("result")
        else:
            content["body"] = body or ""
        content.update({k: v for k, v in fields.items() if v is not None})

        data = self._gql(
            ACTIVITY_CREATE,
            {"input": {"agentSessionId": session_id, "content": content}},
        )
        result = data.get("agentActivityCreate") or {}
        if result.get("success") is False:
            raise LinearError("agentActivityCreate returned success=false")
        return result

    def set_external_url(self, session_id, url):
        """Publishing externalUrls keeps a session from being marked unresponsive."""
        return self._gql(
            AGENT_SESSION_UPDATE, {"id": session_id, "input": {"externalUrls": [{"url": url}]}}
        )

    def issue_context(self, issue_id):
        return self._gql(ISSUE_QUERY, {"id": issue_id}).get("issue")


class LinearCredentialMissing(LinearError):
    """Raised on first USE, not at boot, so the service can start unconfigured."""


class DeferredLinearClient:
    """Boots without a credential.

    The pre-OAuth state is normal (fresh install, no token yet). Failing at import
    time forced a bootstrap with a personal key, so instead we start the server and
    fail only when Linear is actually called, with an operator-actionable message.
    """

    def __init__(self, reason):
        self.reason = reason

    def _raise(self, *_a, **_kw):
        raise LinearCredentialMissing(self.reason)

    post_activity = _raise
    issue_context = _raise
    set_external_url = _raise
