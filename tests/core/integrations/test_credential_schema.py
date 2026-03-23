"""Tests for CredentialField and CredentialSchema models."""

from __future__ import annotations

from core.integrations.base import CredentialField, CredentialSchema


def test_credential_field_defaults() -> None:
    field = CredentialField(label="Username")
    assert field.field_type == "text"
    assert field.required is True
    assert field.placeholder == ""
    assert field.help_text == ""
    assert field.transient is False


def test_credential_field_password() -> None:
    field = CredentialField(label="Secret", field_type="password", transient=True)
    assert field.field_type == "password"
    assert field.transient is True


def test_credential_schema_empty() -> None:
    schema = CredentialSchema(fields={})
    assert schema.fields == {}
    data = schema.model_dump()
    assert data == {"fields": {}}


def test_credential_schema_with_fields() -> None:
    schema = CredentialSchema(fields={
        "username": CredentialField(label="Email", placeholder="you@example.com"),
        "password": CredentialField(label="Password", field_type="password"),
    })
    assert len(schema.fields) == 2
    assert schema.fields["username"].label == "Email"
    assert schema.fields["password"].field_type == "password"


def test_credential_schema_serialization() -> None:
    schema = CredentialSchema(fields={
        "url": CredentialField(label="URL", field_type="url", required=True),
    })
    data = schema.model_dump()
    assert data["fields"]["url"]["label"] == "URL"
    assert data["fields"]["url"]["field_type"] == "url"
    assert data["fields"]["url"]["transient"] is False
