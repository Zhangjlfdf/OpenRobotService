"""High-level browser actions for the UI business-chain scenario."""

from __future__ import annotations

from datetime import datetime, timedelta

from playwright.sync_api import Locator, Page, expect


def default_end_time(days: int = 7) -> str:
    """Return the date format accepted by the stage DatePicker."""

    return (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d %H:%M")


class UiPageActions:
    """Wrap real page interactions without bypassing the frontend."""

    def __init__(self, page: Page, base_url: str):
        self.page = page
        self.base_url = base_url.rstrip("/")

    def login(self, username: str, password: str) -> None:
        self.page.goto(f"{self.base_url}/login?debug=true", wait_until="domcontentloaded")
        self.page.get_by_test_id("login-username").fill(username)
        self.page.get_by_test_id("login-password").fill(password)
        self.page.get_by_test_id("login-submit").click()
        expect(self.page).not_to_have_url(lambda url: "/login" in url, timeout=20_000)

    def start_new_conversation(self) -> None:
        self.page.get_by_test_id("chat-new-conversation").click()

    def send_question(self, question: str) -> None:
        self._fill_test_id("chat-input", question)
        self.page.get_by_test_id("chat-send").first.click()
        expect(self.page.get_by_test_id("chat-transfer-ticket")).to_be_visible(
            timeout=30_000
        )

    def ensure_ticket_draft_modal(self) -> None:
        modal = self.page.get_by_test_id("chat-ticket-draft-modal")
        if modal.count() and modal.is_visible():
            return
        self.page.get_by_test_id("chat-transfer-ticket").click()
        expect(modal).to_be_visible(timeout=30_000)

    def confirm_ticket(self, expected_title: str) -> int:
        expect(self.page.get_by_test_id("chat-ticket-title")).to_have_value(
            expected_title,
            timeout=20_000,
        )
        with self.page.expect_response(
            lambda response: "/api/ai/qa/ticket/confirm" in response.url
        ) as response_info:
            self.page.get_by_test_id("chat-ticket-confirm").click()
        response = response_info.value
        if response.status != 200:
            raise AssertionError(f"确认提单失败: HTTP {response.status}")
        data = response.json().get("data") or {}
        ticket_id = data.get("db_id")
        if not ticket_id:
            raise AssertionError("确认提单响应缺少 db_id")
        return int(ticket_id)

    def open_system_tasks(self) -> None:
        self.page.get_by_test_id("nav-item-tasks").click()
        expect(self.page.get_by_test_id("tasks-search")).to_be_visible(
            timeout=20_000
        )

    def search_ticket(self, ticket_id: int) -> None:
        self.page.get_by_test_id("tasks-search").fill(str(ticket_id))
        expect(self.page.get_by_test_id(f"task-card-{ticket_id}")).to_be_visible(
            timeout=20_000
        )

    def open_ticket(self, ticket_id: int) -> None:
        self.page.get_by_test_id(f"task-card-{ticket_id}").click()
        expect(self.page.get_by_test_id("task-status")).to_be_visible(
            timeout=20_000
        )

    def accept_current_step(self) -> None:
        with self.page.expect_response(
            lambda response: "/respond" in response.url
        ) as response_info:
            self.page.get_by_test_id("task-accept").first.click()
        if response_info.value.status != 200:
            raise AssertionError(
                f"确认接单失败: HTTP {response_info.value.status}"
            )

    def complete_current_step(
        self,
        *,
        next_step_name: str | None = None,
        end_time: str | None = None,
    ) -> None:
        self.page.get_by_test_id("task-complete-step").first.click()
        step_select = self.page.get_by_test_id("task-step-next")
        expect(step_select).to_be_visible(timeout=10_000)
        if next_step_name:
            step_select.select_option(label=next_step_name)
        else:
            options = step_select.locator("option:not([disabled])")
            values = options.evaluate_all(
                "(nodes) => nodes.map((node) => node.value).filter(Boolean)"
            )
            if not values:
                raise AssertionError("没有可选的下一阶段")
            step_select.select_option(value=values[0])

        self._fill_test_id("task-step-endtime", end_time or default_end_time())
        with self.page.expect_response(
            lambda response: "/complete-step" in response.url
        ) as response_info:
            self.page.get_by_test_id("task-step-submit").click()
        if response_info.value.status != 200:
            raise AssertionError(
                f"推进阶段失败: HTTP {response_info.value.status}"
            )

    def resolve_ticket(self, summary: str) -> None:
        self.page.get_by_test_id("task-resolve").first.click()
        self._fill_test_id("task-resolution-summary", summary)
        with self.page.expect_response(
            lambda response: "/status" in response.url
        ) as response_info:
            self.page.get_by_test_id("task-resolve-confirm").click()
        if response_info.value.status != 200:
            raise AssertionError(
                f"提交已解决失败: HTTP {response_info.value.status}"
            )

    def close_ticket(self) -> None:
        with self.page.expect_response(
            lambda response: "/status" in response.url
        ) as response_info:
            self.page.get_by_test_id("task-close").click()
        if response_info.value.status != 200:
            raise AssertionError(
                f"确认关闭失败: HTTP {response_info.value.status}"
            )

    def status_text(self) -> str:
        return self.page.get_by_test_id("task-status").inner_text().strip()

    def _fill_test_id(self, test_id: str, value: str) -> None:
        locator = self.page.get_by_test_id(test_id).first
        tag_name = locator.evaluate("(element) => element.tagName.toLowerCase()")
        target: Locator = locator
        if tag_name not in {"input", "textarea"}:
            target = locator.locator("input, textarea").first
        target.fill(value)
