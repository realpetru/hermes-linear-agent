"""Choose which profile answers a given Linear issue.

Precedence, highest first:

1. a label route from ``routing`` (first matching label wins, config order)
2. ``agent_profile`` (the configured default for this bridge)
3. the empty string, meaning "let the gateway decide" (its default profile)
"""
from __future__ import annotations


def issue_labels(payload):
    """Label names from an AgentSessionEvent payload, best effort.

    Linear includes labels in different places depending on the event shape, so all
    known spots are checked. A payload without labels simply routes by default.
    """
    names = []
    session = (payload or {}).get("agentSession") or {}
    issue = session.get("issue") or (payload or {}).get("issue") or {}
    labels = issue.get("labels")
    if isinstance(labels, dict):
        for node in labels.get("nodes") or []:
            if isinstance(node, dict) and node.get("name"):
                names.append(str(node["name"]).lower())
    elif isinstance(labels, list):
        for node in labels:
            if isinstance(node, dict) and node.get("name"):
                names.append(str(node["name"]).lower())
            elif isinstance(node, str):
                names.append(node.lower())
    # ``labels`` was already consumed above; only the flat name list is left to read.
    flat = issue.get("labelNames")
    if isinstance(flat, list):
        names.extend(str(v).lower() for v in flat if isinstance(v, str))
    # de-duplicate while keeping order, so "first matching route wins" stays predictable
    seen, unique = set(), []
    for name in names:
        if name not in seen:
            seen.add(name)
            unique.append(name)
    return unique


def choose_profile(payload, agent_profile="", routing=None):
    """Return the profile to run as, or "" for the gateway default.

    Never raises: an unknown label simply does not match.
    """
    routing = routing or {}
    labels = issue_labels(payload)
    for label, profile in routing.items():
        if str(label).lower() in labels and profile:
            return str(profile).strip().lower()
    return (agent_profile or "").strip().lower()
