"""Critical public authentication entry journey in a real browser."""

import re

from playwright.sync_api import Page, expect


def test_login_to_registration_validation(page: Page, live_test_url: str) -> None:
    page.goto(f"{live_test_url}/login")

    expect(page).to_have_title(re.compile(r"Login.*Pull All The Things"))
    expect(page.get_by_role("heading", name="Welcome Back")).to_be_visible()
    expect(page.locator("#discord_username")).to_be_focused()

    page.get_by_role("link", name="Register with an invite code").click()
    expect(page).to_have_url(f"{live_test_url}/register")
    expect(page).to_have_title(re.compile(r"Register.*Pull All The Things"))

    page.locator("#code").fill("SYNTHETIC-CODE")
    page.locator("#discord_username").fill("synthetic-member")
    page.locator("#password").fill("correct-horse")
    page.locator("#password2").fill("different-horse")
    expect(page.locator("#pw-match-msg")).to_contain_text("Passwords do not match")

    page.get_by_role("button", name="Create Account").click()
    expect(page.locator(".flash-bar--error")).to_contain_text("Passwords do not match")


def test_member_login_cookie_identity_and_logout(page: Page, live_test_url: str) -> None:
    page.goto(f"{live_test_url}/login?next=/api/v1/auth/me")
    page.locator("#discord_username").fill("synthetic-member")
    page.locator("#password").fill("synthetic-password")
    page.get_by_role("button", name="Sign In").click()

    expect(page).to_have_url(f"{live_test_url}/api/v1/auth/me")
    expect(page.locator("body")).to_contain_text("Synthetic Member")

    cookie = next(c for c in page.context.cookies() if c["name"] == "patt_token")
    assert cookie["httpOnly"] is True
    assert cookie["sameSite"] == "Lax"
    assert cookie["secure"] is False
    assert 6.9 * 24 * 60 * 60 <= cookie["expires"] - __import__("time").time() <= 7 * 24 * 60 * 60

    response = page.request.post(
        f"{live_test_url}/logout",
        headers={"Origin": live_test_url},
        max_redirects=0,
    )
    assert response.status == 302
    assert not any(c["name"] == "patt_token" for c in page.context.cookies())

    page.goto(f"{live_test_url}/api/v1/auth/me")
    assert page.locator("body").text_content() is not None
    assert "Not authenticated" in page.locator("body").text_content()
