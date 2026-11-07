"""Choose which profile answers a given Linear issue.

Precedence, highest first:

1. an explicit ``routing`` entry (label -> profile) for odd cases
2. **zero-config**: a label whose name *is* an existing Hermes profile
   (``cody``, ``appy``, ``studio`` ...). Adding a label in Linear is two clicks;
   editing a container's environment is a support ticket.
3. ``agent_profile`` (the configured default for this bridge)
4. the empty string, meaning "let the gateway decide" (its default profile)

A label that matches nothing falls through silently: no error, and never a run that
would fail because the profile does not exist.
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
    flat = issue.get("labelNames")
    if isinstance(flat, list):
        names.extend(str(v).lower() for v in flat if isinstance(v, str))
    seen, unique = set(), []
    for name in names:
        if name not in seen:
            seen.add(name)
            unique.append(name)
    return unique


def profile_for_labels(labels, available):
    """First label that names a real profile, or "" when none does."""
    index = {}
    for name in available or []:
        if name:
            index[str(name).strip().lower()] = str(name).strip().lower()
    for label in labels:
        if label in index:
            return index[label]
    return ""


def choose_profile(payload, agent_profile="", routing=None, available=None):
    """Return the profile to run as, or "" for the gateway default. Never raises."""
    labels = issue_labels(payload)
    for label, profile in (routing or {}).items():
        if str(label).lower() in labels and profile:
            return str(profile).strip().lower()
    matched = profile_for_labels(labels, available)
    if matched:
        return matched
    return (agent_profile or "").strip().lower()
