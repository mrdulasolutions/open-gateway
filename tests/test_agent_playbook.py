"""Public agent playbook / skill helpers."""

from opengateway.agent_playbook import playbook, skill_markdown


def test_playbook_topics():
    assert "device key" in playbook("connect").lower() or "token" in playbook("connect").lower()
    assert "wait_for_messages" in playbook("radio")
    assert "opengateway im" in playbook("im")
    assert "Unknown" in playbook("nope")


def test_skill_markdown_has_frontmatter_or_title():
    s = skill_markdown()
    assert "opengateway" in s.lower()
    assert len(s) > 40
