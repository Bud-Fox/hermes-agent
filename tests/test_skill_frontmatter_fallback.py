"""Tests for the line-by-line fallback path of parse_frontmatter() in agent/skill_utils.py."""


import logging

import pytest

from agent.skill_utils import parse_frontmatter, skill_matches_platform, skill_matches_platform_list


def test_valid_yaml_uses_primary_path():
    frontmatter, body = parse_frontmatter(
        "---\nname: test\nplatforms: [linux, macos]\n---\nbody text\n"
    )
    assert frontmatter == {"name": "test", "platforms": ["linux", "macos"]}
    assert body == "body text\n"


def test_invalid_yaml_fallback_platforms_is_list(caplog):
    # Unquoted colon in description makes yaml.safe_load fail -> fallback.
    content = (
        "---\n"
        "name: broken\n"
        "description: A skill: with a colon\n"
        "platforms: [linux, macos, windows]\n"
        "---\n"
        "body\n"
    )
    with caplog.at_level(logging.WARNING, logger="agent.skill_utils"):
        frontmatter, _ = parse_frontmatter(content)
    assert frontmatter["platforms"] == ["linux", "macos", "windows"]
    assert skill_matches_platform(frontmatter) is skill_matches_platform_list(["linux"])
    assert skill_matches_platform_list(frontmatter["platforms"])
    assert any("line-by-line fallback" in r.message for r in caplog.records)


def test_invalid_yaml_fallback_flow_mapping_and_scalars(caplog):
    with caplog.at_level(logging.WARNING, logger="agent.skill_utils"):
        frontmatter, _ = parse_frontmatter(
            "---\n"
            "description: bad: value\n"
            "metadata: {hermes: {icon: robot}}\n"
            "enabled: true\n"
            "disabled: false\n"
            "missing: null\n"
            "count: 42\n"
            "ratio: 1.5\n"
            "quoted: 'hello world'\n"
            "---\n"
            "body\n"
        )
    assert frontmatter["metadata"] == {"hermes": {"icon": "robot"}}
    assert frontmatter["enabled"] is True
    assert frontmatter["disabled"] is False
    assert frontmatter["missing"] is None
    assert frontmatter["count"] == 42
    assert frontmatter["ratio"] == 1.5
    assert frontmatter["quoted"] == "hello world"


def test_invalid_yaml_fallback_logs_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="agent.skill_utils"):
        parse_frontmatter("---\ndescription: bad: value\n---\nbody\n")
    assert any(r.levelno == logging.WARNING and "fallback" in r.message for r in caplog.records)


def test_valid_yaml_no_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="agent.skill_utils"):
        parse_frontmatter("---\nname: ok\n---\nbody\n")
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]


@pytest.mark.parametrize("value,expected", [
    ("[a, b, , c]", ["a", "b", "c"]),
    ("[]", []),
    ("42", 42),
    ("true", True),
    ("null", None),
    ("just text", "just text"),
    ("'quoted'", "quoted"),
])
def test_fallback_scalar_types(value, expected):
    from agent.skill_utils import _fallback_scalar
    assert _fallback_scalar(value) == expected
