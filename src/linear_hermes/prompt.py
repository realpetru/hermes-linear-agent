"""Prompt construction: Linear context -> one self-contained Hermes instruction."""
from __future__ import annotations

TEMPLATE = """You were woken as a Linear agent by {author}.

Issue {identifier}: {title}
{url}

Description:
{description}

Recent comments:
{comments}

Instructions:
- Do the work the request asks for. If you need to read or update the issue,
  use the Linear API with the credentials the bridge already holds.
- Reply in Plain Markdown, no preamble, no sign-off. This text is posted
  verbatim as your agent activity on the issue.
"""


def render(issue, author="a teammate", max_comments=10, max_chars=6000):
    comments = ""
    for c in (issue or {}).get("comments", {}).get("nodes", [])[-max_comments:]:
        who = (c.get("user") or {}).get("name") or "someone"
        comments += "- %s: %s\n" % (who, (c.get("body") or "").strip()[:400])
    out = TEMPLATE.format(
        author=author,
        identifier=(issue or {}).get("identifier", "?"),
        title=(issue or {}).get("title", ""),
        url=(issue or {}).get("url", ""),
        description=((issue or {}).get("description") or "")[:max_chars],
        comments=comments or "- (none)",
    )
    return out
