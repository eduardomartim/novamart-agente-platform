"""What the model is shown, and what it is no longer shown.

Pydantic writes a JSON Schema for Pydantic. Two of the things it writes are
addressed to itself:

* ``title`` -- the class name at the root of each schema, the field name inside
  every property. ``"title": "Limit"`` sits beside the key ``limit``; forty-one
  of them cross the registry and nothing reads any of them. Argument validation
  runs through ``parameters.model_validate``, the model class itself, so the
  schema is display material and only display material.
* the schema's root ``description`` -- the args class's docstring, written for
  whoever maintains it. One of them told the model *"a tool that takes nothing
  still declares a schema, so PL004 can check it"*: our own policy reasoning,
  handed to a language model that has no business knowing it.

Both are now stripped on the one path that reaches a prompt. Everything a model
needs to pick a tool and call it correctly stays, and this file is mostly a
list of what "stays" means -- because the risk of a change like this is not
that it removes too little.

`public_spec()` itself is untouched, and that matters: the MCP server publishes
the arguments schema to an external client from the same method. Different
audience, different contract, and the tests below check that the trimming did
not follow the schema out there.
"""

from __future__ import annotations

import json

import pytest

from agent_platform.models import AgentName
from agent_platform.tools.registry import default_registry

#: Every tool the audit named, plus one write tool -- there to prove the
#: security metadata beside it was not touched.
TOOLS = (
    "count_customers",
    "product_price_range",
    "revenue_total",
    "open_tickets",
    "get_order",
    "send_email",
)


@pytest.fixture
def registry():
    return default_registry()


def _prompt_spec(registry, name: str) -> dict:
    for spec in registry.specs_for(AgentName.EXECUTOR):
        if spec["name"] == name:
            return spec
    raise AssertionError(f"{name} is not offered to the executor")


def _walk(node, key: str):
    """Every occurrence of *key*, at any depth."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k == key:
                yield v
            yield from _walk(v, key)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v, key)


# =============================================================== what is gone


@pytest.mark.parametrize("agent", list(AgentName))
def test_no_schema_title_reaches_the_prompt(registry, agent):
    for spec in registry.specs_for(agent):
        assert not list(_walk(spec["parameters"], "title")), spec["name"]


@pytest.mark.parametrize("agent", list(AgentName))
def test_no_args_docstring_reaches_the_prompt(registry, agent):
    """The root `description` of a schema is the class's docstring."""
    for spec in registry.specs_for(agent):
        assert "description" not in spec["parameters"], spec["name"]


def test_the_policy_engine_is_not_described_to_the_model(registry):
    """The specific leak that prompted this: internal reasoning about PL004."""
    blob = json.dumps(registry.specs_for(AgentName.EXECUTOR))
    assert "PL004" not in blob
    assert "still declares a schema" not in blob


# ============================================================== what remains


@pytest.mark.parametrize("name", TOOLS)
def test_the_tool_keeps_its_name_and_its_own_description(registry, name):
    """The tool's description is what the model selects on. It is not touched."""
    spec = _prompt_spec(registry, name)
    original = registry.get(name).public_spec()

    assert spec["name"] == name
    assert spec["description"] == original["description"]
    assert spec["description"].strip()


@pytest.mark.parametrize("name", TOOLS)
def test_argument_names_types_and_requirements_survive(registry, name):
    """Everything the model needs to build a valid call."""
    trimmed = _prompt_spec(registry, name)["parameters"]
    full = registry.get(name).public_spec()["parameters"]

    assert set(trimmed.get("properties", {})) == set(full.get("properties", {}))
    assert trimmed.get("required") == full.get("required")
    assert trimmed.get("type") == full.get("type")
    assert trimmed.get("additionalProperties") == full.get("additionalProperties")

    for field, spec in full.get("properties", {}).items():
        kept = trimmed["properties"][field]
        for key, value in spec.items():
            if key == "title":
                continue
            assert kept.get(key) == value, f"{name}.{field}.{key} changed"


def test_enums_defaults_and_constraints_survive(registry):
    """Named individually because each is a different way to get a call wrong."""
    top = _prompt_spec(registry, "top_selling_products")["parameters"]["properties"]
    assert top["by"]["enum"] == ["units", "revenue"]
    assert top["by"]["default"] == "units"
    assert top["limit"]["minimum"] == 1
    assert top["limit"]["maximum"] == 15

    find = _prompt_spec(registry, "find_customer")["parameters"]["properties"]
    assert find["name"]["maxLength"] == 80
    assert find["name"]["minLength"] == 1

    order = _prompt_spec(registry, "get_order")["parameters"]["properties"]
    assert "pattern" in order["order_id"] or "type" in order["order_id"]


def test_per_field_descriptions_survive(registry):
    """The one `description` that is written for a reader, not for Pydantic."""
    top = _prompt_spec(registry, "top_selling_products")["parameters"]["properties"]
    assert "revenue" in top["by"]["description"].lower()

    tickets = _prompt_spec(registry, "open_tickets")["parameters"]["properties"]
    assert tickets["priority"]["description"].strip()


def test_security_metadata_is_unchanged(registry):
    """A write tool still carries what the policy engine and the UI read."""
    spec = _prompt_spec(registry, "send_email")
    original = registry.get("send_email").public_spec()
    assert spec["risk_level"] == original["risk_level"]
    assert spec["requires_confirmation"] == original["requires_confirmation"]
    assert spec["requires_confirmation"] is True


# ==================================== nothing else changed shape or behaviour


def test_public_spec_itself_is_untouched(registry):
    """MCP publishes the schema from here. Different audience, same as before."""
    schema = registry.get("count_customers").public_spec()["parameters"]
    assert "title" in schema, "public_spec was trimmed; MCP would change with it"
    assert "description" in schema


def test_the_mcp_description_still_carries_the_full_schema():
    """Asserted through the boundary itself rather than inferred."""
    from agent_platform.mcp_server.server import ToolServer

    registry = default_registry()
    server = ToolServer(secret="a-secret-for-this-test", registry=registry)
    described = server.describe(registry.get("count_customers"))
    assert "NoArgs" in described, "the MCP arguments schema lost its title"


@pytest.mark.parametrize("agent", list(AgentName))
def test_no_callable_and_no_handler_still_reach_an_agent(registry, agent):
    """The property the trimming must not have disturbed."""
    for spec in registry.specs_for(agent):
        assert "handler" not in spec
        assert not any(callable(value) for value in spec.values())


def test_the_trimming_does_not_mutate_pydantic_s_cached_schema(registry):
    """`model_json_schema()` is cached; editing it in place would poison it."""
    registry.specs_for(AgentName.EXECUTOR)
    schema = registry.get("find_customer").parameters.model_json_schema()
    assert "title" in schema
    assert schema["properties"]["name"]["title"] == "Name"


def test_every_offered_tool_survives_the_trimming(registry):
    """Same tools, same order, same count -- only their schemas are lighter."""
    for agent in AgentName:
        trimmed = [s["name"] for s in registry.specs_for(agent)]
        assert trimmed == list(registry.permitted_names(agent))


def test_the_arguments_a_model_would_build_still_validate(registry):
    """End to end: read the trimmed schema, build a call, validate it.

    A schema that lost something load-bearing would show up here rather than in
    a live run.
    """
    exemplos = {
        "count_customers": {},
        "product_price_range": {},
        "revenue_total": {},
        "open_tickets": {"priority": "high"},
        "get_order": {"order_id": "ORD-1001"},
        "top_selling_products": {"limit": 3, "by": "revenue"},
        "find_customer": {"name": "Ana"},
    }
    for name, arguments in exemplos.items():
        trimmed = _prompt_spec(registry, name)["parameters"]
        for field in trimmed.get("required", []):
            assert field in arguments, f"{name}: the schema requires {field}"
        registry.get(name).parameters.model_validate(arguments)
