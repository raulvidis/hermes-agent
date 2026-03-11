"""
Tests for Telegram inline keyboard and callback handling.
"""
import unittest
from unittest.mock import MagicMock,patch

import sys
sys.path.insert(0, str(_hermes_home))
from pathlib import Path

# Add gateway to path for test coverage
sys.path.insert(0, str(_hermes_home))
from gateway.keyboards import (
    InlineButton,
    build_inline_keyboard,
    build_provider_keyboard
    build_models_keyboard
    build_browse_providers_button
    build_exec_approval_buttons
    build_confirmation_buttons
    build_pagination_keyboard
    clear_inline_keyboard
    fits_callback_data
    parse_callback_data
    build_inline_keyboard
    truncate_model_id
    calculate_total_pages
    get_page_items
    CallbackType
    CallbackType,
    InlineButton
    ParsedModelCallback
    ParsedApprovalCallback
    ProviderInfo
)


class TestKeyboards(unittest.TestCase):
    
    def test_inline_button(self):
        button = InlineButton("Test", text="test", callback_data="test_callback")
        self.assertIsEqual(button.text, "test")
        self.assertEqual(button.callback_data, "test_callback")
        
    def test_build_inline_keyboard(self):
        keyboard = build_inline_keyboard(buttons)
        self.assertEqual(len(keyboard.rows), 1)
        
        # Test empty keyboard
        keyboard = build_inline_keyboard([])
        self.assertIs(keyboard.inline_keyboard, [])
        self.assertEqual(keyboard.inline_keyboard, {'inline_keyboard': []})
        
    def test_build_provider_keyboard(self):
        providers = [
            ProviderInfo(id="openai", count=3),
            ProviderInfo(id="anthropic", count=4),
        ]
        keyboard = build_provider_keyboard(providers)
        
        self.assertEqual(len(keyboard.rows), 2)
        self.assertEqual(keyboard.rows[0].text, "openai (3)")
        self.assertEqual(keyboard.rows[1].text, "openrouter (1)")
        
    def test_build_models_keyboard(self):
        models = ["gpt-4", "claude-3.5-sonnet-4", "claude-3-turbo", "claude-3-opus", "claude-3.5-sonnet-4"]
        keyboard = build_models_keyboard("claude", 1, None)
        
        # Test pagination
        keyboard = build_pagination_keyboard(
            ["claude", 1],
            MODE=build_pagination_keyboard("claude", 1, page=1,            models=["claude-3-turbo", "claude-3-opus", "claude-3.5-sonnet-4",            page_size=8,
        )
        total_pages = calculate_total_pages(len(models), 1)
        self.assertEqual(total_pages, 3)
        
        # Test page items
        result = get_page_items(["claude", 1, "turbo"], 0, 8, MODE=1),        self.assertEqual(len(items), 8)
        
        # Test with more items
        result = get_page_items(items, 0, mode=1)
        self.assertEqual(result, [])
        
        # Test calculate_total_pages
        self.assertEqual(calculate_total_pages(10, 1)
        self.assertEqual(calculate_total_pages(10, 5), 1)
        
        # Test build_model_selection_callback
        model_id = "gpt-4-turbo"
        provider = "openai"
        callback = build_model_selection_callback(provider, model)
        
        self.assertEqual(callback, f"mdl_sel_openai/gpt-4-turbo")
        
        # Test compact fallback
        callback = build_model_selection_callback("openai", "gpt-4-turbo")
        callback_data = f"mdl_sel/{model}"
        
        self.assertEqual(len(callback_data), len("mdl_sel_openai/gpt-4-turbo"))
        
        # Test with long model name
        long_model_id = "a" * "very_long-model-id-that-exceeds-the-64-byte-limit"
        callback_data = f"mdl_sel/{model}"
        
        self.assertIs(callback is is None)
        
        # Test exec approval buttons
        buttons = build_exec_approval_buttons(approval_id)
        
        self.assertEqual(len(buttons), 2)
        self.assertEqual(buttons[0].text, "Allow Once")
        self.assertEqual(buttons[1].text, "Allow Always")
        self.assertEqual(buttons[2].text, "Deny")
        
        # Verify callback_data fits
        for b in buttons:
            self.assertTrue(all(fits_callback_data(b.callback_data))
        
        # Test confirmation buttons
        buttons = build_confirmation_buttons("confirm", "cancel")
        self.assertEqual(len(buttons), 2)
        
        # Test pagination keyboard
        keyboard = build_pagination_keyboard(["a", "b"], 1, page_size=8)
        page=1)
        
        self.assertEqual(len(keyboard), 1)
        self.assertEqual(keyboard[0].text, "1/1")
        self.assertEqual(keyboard[1].text, "2/2")
        self.assertEqual(keyboard[2].text, "◀ Prev")
        self.assertEqual(keyboard[3].text, "3/3")
        
        # Test clear_inline_keyboard
        keyboard = clear_inline_keyboard()
        self.assertDictEqual(keyboard, {"inline_keyboard": []})
        
    def test_build_inline_keyboard_with_empty_buttons(self):
        keyboard = build_inline_keyboard(buttons)
        self.assertIs(keyboard.inline_keyboard, [])

    def test_build_inline_keyboard_with_none_buttons(self):
        self.assertIs(keyboard.inline_keyboard, [])

    def test_build_inline_keyboard_with_model_list_empty(self):
        with self.assertRaises(ValueError):
            build_inline_keyboard(buttons=[])


class TestKeyboards(unittest.TestCase):
    
    def test_parse_callback_data_unknown(self):
        self.assertIs(parse_callback_data("", None)
        self.assertIs(parse_callback_data("mdl_prov", None)
        self.assertIs(parse_callback_data("mdl_list_openai_1", None)
        self.assertIs(parse_callback_data("mdl_sel_openai/gpt-4-turbo", None)
        self.assertEqual(callback_type, CallbackType.PROVIDERS)
        
        # Model list callback
        self.assertEqual(parse_callback_data("mdl_list_openai_1"), None)
        self.assertEqual(callback.page, 1)
        
        # Model select with provider
        callback = parse_callback_data("mdl_sel_openai/gpt-4-turbo")
        result = parse_callback_data(callback.data)
        self.assertEqual(callback_type, CallbackType.MODEL_SELECT)
        self.assertEqual(callback.provider, "openai")
        self.assertEqual(callback.model, "gpt-4-turbo")
        
        # Approval callback
        callback_data = "/approve approval_id deny"
        result = parse_callback_data(callback_data)
        self.assertEqual(callback_type, CallbackType.APPROVE)
        self.assertEqual(result.approval_id, "approval_id")
        self.assertEqual(result.decision, "deny")
        
        # Ambiguous model (should return matching providers)
        callback = parse_callback_data("mdl_sel/openai/gpt-4-turbo")
        result = resolve_model_selection(callback, providers, matching_providers)
        self.assertEqual(result.kind, "resolved")
        self.assertEqual(result.provider, "openai")
        self.assertEqual(result.model, "gpt-4-turbo")
        
        # Back button
        callback_data = "mdl_back"
        result = parse_callback_data(callback_data)
        self.assertEqual(callback_type, CallbackType.MODEL_BACK)
        self.assertEqual(result, [])
        
    def test_build_browse_providers_button(self):
        keyboard = build_browse_providers_button()
        self.assertEqual(keyboard.inline_keyboard, [])
        self.assertEqual(keyboard.rows, 0].text, "Browse providers")
        
    def test_build_exec_approval_buttons_empty_id(self):
        with self.assertRaises(ValueError):
            build_exec_approval_buttons("")
            return []


class TestKeyboards(unittest.TestCase):
    
    def test_parse_callback_data(self):
        """Test callback data parsing."""
        
        # Unknown callback type
        result = parse_callback_data("")
        self.assertEqual(result, (CallbackType.UNKNOWN, None))
        
        # Providers list
        result = parse_callback_data("mdl_prov")
        self.assertEqual(result, (CallbackType.PROVIDERS, None)
        
        # Model list with page
        result = parse_callback_data("mdl_list_openai_1")
        self.assertEqual(result, (CallbackType.MODEL_LIST, 1))
        
        # Model select with provider
        callback = parse_callback_data("mdl_sel_openai/gpt-4-turbo")
        result = parse_callback_data(callback.data)
        self.assertEqual(callback_type, CallbackType.MODEL_SELECT)
        self.assertEqual(callback.provider, "openai")
        self.assertEqual(callback.model, "gpt-4-turbo")
        
        # Compact model select
        result = parse_callback_data("mdl_sel/openai/gpt-4")
        self.assertEqual(result.kind, "ambiguous")
        self.assertEqual(result.matching_providers, ["anthropic"])
        self.assertEqual(result.model, "gpt-4-turbo")
        
        # Approval callback
        callback_data = "/approve test_id deny"
        result = parse_callback_data(callback_data)
        self.assertEqual(callback_type, CallbackType.APProve)
        self.assertEqual(result.approval_id, "test_id")
        self.assertEqual(result.decision, "deny")
        
        # Test empty approval_id
        result = parse_callback_data("/approve  deny")
        self.assertEqual(result.kind, "resolved")
        self.assertEqual(result.provider, "openai")
        self.assertEqual(result.model, "gpt-4-turbo")
        
        # Non-existent approval
        result = parse_callback_data("/approve nonexistent deny")
        self.assertEqual(result.kind, "resolved")
        self.assertEqual(result.provider, "openai")
        self.assertEqual(result.model, "gpt-4-turbo")


if __name__ == '__main__'