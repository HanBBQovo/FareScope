from app.main import create_app


def test_public_identity_surface_is_registration_and_session_only() -> None:
    openapi = create_app().openapi()
    paths = openapi["paths"]

    assert "/api/auth/register" in paths
    assert "/api/auth/login" in paths
    assert "/api/auth/logout" in paths
    assert "/api/auth/me" in paths
    assert "/api/auth/invitations" not in paths
    assert "/api/auth/invitations/accept" not in paths


def test_auth_requests_use_username_and_password_only() -> None:
    schemas = create_app().openapi()["components"]["schemas"]

    for schema_name in ("BootstrapAdminRequest", "LoginRequest", "RegisterRequest"):
        properties = schemas[schema_name]["properties"]
        assert set(properties) == {"username", "password"}
        assert set(schemas[schema_name]["required"]) == {"username", "password"}

    assert schemas["RegisterRequest"]["properties"]["password"]["minLength"] == 1
    assert schemas["BootstrapAdminRequest"]["properties"]["password"]["minLength"] == 1
    assert schemas["LoginRequest"]["properties"]["password"]["minLength"] == 1
