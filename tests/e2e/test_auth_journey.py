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
