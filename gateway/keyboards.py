"""
Telegram inline keyboard builders and callback data parsers.

Implements OpenClaw-style inline keyboard patterns:
- Model selection with pagination
- Exec approval buttons (Allow Once/Always/Deny)
- Generic button builders

Callback data patterns (max 64 bytes for Telegram):
- mdl_prov              - show providers list
- mdl_list_{prov}_{pg}  - show models for provider (page N, 1-indexed)
- mdl_sel_{provider/id} - select model (standard)
- mdl_sel/{model}       - select model (compact fallback when standard >64 bytes)
- mdl_back              - back to providers list
- /approve {id} {dec}   - exec approval decision (allow-once|allow-always|deny)
"""

from dataclasses import dataclass
from enum import Enum
from typing import List, Dict, Optional, Any, Tuple, Callable
import re


# Telegram's callback_data limit
MAX_CALLBACK_DATA_BYTES = 64
MODELS_PAGE_SIZE = 8


class CallbackType(Enum):
    """Types of callback queries we handle."""
    PROVIDERS = "providers"
    MODEL_LIST = "model_list"
    MODEL_SELECT = "model_select"
    MODEL_BACK = "model_back"
    APPROVE = "approve"
    UNKNOWN = "unknown"


@dataclass
class InlineButton:
    """A single inline keyboard button."""
    text: str
    callback_data: str
    
    def to_dict(self) -> Dict[str, str]:
        return {"text": self.text, "callback_data": self.callback_data}


@dataclass
class ParsedModelCallback:
    """Parsed model selection callback data."""
    type: CallbackType
    provider: Optional[str] = None
    page: Optional[int] = None
    model: Optional[str] = None


@dataclass
class ParsedApprovalCallback:
    """Parsed exec approval callback data."""
    approval_id: str
    decision: str  # "allow-once", "allow-always", "deny"


@dataclass
class ProviderInfo:
    """Provider info for keyboard display."""
    id: str
    count: int


def fits_callback_data(value: str) -> bool:
    """Check if a string fits within Telegram's 64-byte callback_data limit."""
    return len(value.encode('utf-8')) <= MAX_CALLBACK_DATA_BYTES


def truncate_model_id(model_id: str, max_len: int = 38) -> str:
    """Truncate model ID for display, preserving end if too long."""
    if len(model_id) <= max_len:
        return model_id
    # Show last part with ellipsis prefix
    return f"...{model_id[-(max_len - 3):]}"


# ─────────────────────────────────────────────────────────────────────────────
# Callback Data Parsing
# ─────────────────────────────────────────────────────────────────────────────

CALLBACK_PREFIX = {
    "providers": "mdl_prov",
    "back": "mdl_back",
    "list": "mdl_list_",
    "select_standard": "mdl_sel_",
    "select_compact": "mdl_sel/",
    "approve": "/approve",
}


def parse_callback_data(data: str) -> Tuple[CallbackType, Any]:
    """
    Parse callback data string into type and payload.
    
    Returns:
        (CallbackType, parsed_data) tuple
    """
    if not data:
        return (CallbackType.UNKNOWN, None)
    
    trimmed = data.strip()
    
    # Providers list
    if trimmed == CALLBACK_PREFIX["providers"]:
        return (CallbackType.PROVIDERS, None)
    
    # Back button
    if trimmed == CALLBACK_PREFIX["back"]:
        return (CallbackType.MODEL_BACK, None)
    
    # Model list: mdl_list_{provider}_{page}
    list_match = re.match(r'^mdl_list_([a-z0-9_-]+)_(\d+)$', trimmed, re.IGNORECASE)
    if list_match:
        provider, page_str = list_match.groups()
        page = int(page_str)
        if page >= 1:
            parsed = ParsedModelCallback(
                type=CallbackType.MODEL_LIST,
                provider=provider,
                page=page,
            )
            return (CallbackType.MODEL_LIST, parsed)
    
    # Model select compact: mdl_sel/{model}
    compact_match = re.match(r'^mdl_sel/(.+)$', trimmed)
    if compact_match:
        model = compact_match.group(1)
        parsed = ParsedModelCallback(
            type=CallbackType.MODEL_SELECT,
            model=model,
        )
        return (CallbackType.MODEL_SELECT, parsed)
    
    # Model select standard: mdl_sel_{provider/model}
    sel_match = re.match(r'^mdl_sel_(.+)$', trimmed)
    if sel_match:
        model_ref = sel_match.group(1)
        slash_idx = model_ref.find('/')
        if slash_idx > 0 and slash_idx < len(model_ref) - 1:
            provider = model_ref[:slash_idx]
            model = model_ref[slash_idx + 1:]
            parsed = ParsedModelCallback(
                type=CallbackType.MODEL_SELECT,
                provider=provider,
                model=model,
            )
            return (CallbackType.MODEL_SELECT, parsed)
    
    # Approval: /approve {id} {decision}
    approve_match = re.match(r'^/approve\s+(\S+)\s+(allow-once|allow-always|deny)$', trimmed)
    if approve_match:
        approval_id, decision = approve_match.groups()
        parsed = ParsedApprovalCallback(
            approval_id=approval_id,
            decision=decision,
        )
        return (CallbackType.APPROVE, parsed)
    
    return (CallbackType.UNKNOWN, None)


# ─────────────────────────────────────────────────────────────────────────────
# Keyboard Builders
# ─────────────────────────────────────────────────────────────────────────────

def build_inline_keyboard(buttons: List[List[InlineButton]]) -> Optional[Dict[str, Any]]:
    """
    Build Telegram InlineKeyboardMarkup from button rows.
    
    Args:
        buttons: List of button rows, each row is a list of InlineButton
        
    Returns:
        InlineKeyboardMarkup dict or None if no valid buttons
    """
    if not buttons:
        return None
    
    rows = []
    for row in buttons:
        button_row = []
        for button in row:
            if button.text and button.callback_data:
                button_row.append(button.to_dict())
        if button_row:
            rows.append(button_row)
    
    if not rows:
        return None
    
    return {"inline_keyboard": rows}


def build_provider_keyboard(providers: List[ProviderInfo]) -> List[List[InlineButton]]:
    """
    Build provider selection keyboard with 2 providers per row.
    
    Args:
        providers: List of provider info with id and model count
        
    Returns:
        List of button rows
    """
    if not providers:
        return []
    
    rows = []
    current_row = []
    
    for provider in providers:
        button = InlineButton(
            text=f"{provider.id} ({provider.count})",
            callback_data=f"mdl_list_{provider.id}_1",
        )
        current_row.append(button)
        
        if len(current_row) == 2:
            rows.append(current_row)
            current_row = []
    
    # Push any remaining button
    if current_row:
        rows.append(current_row)
    
    return rows


def build_models_keyboard(
    provider: str,
    models: List[str],
    current_model: Optional[str] = None,
    current_page: int = 1,
    page_size: int = MODELS_PAGE_SIZE,
) -> List[List[InlineButton]]:
    """
    Build model list keyboard with pagination and back button.
    
    Args:
        provider: Provider ID
        models: List of model IDs
        current_model: Currently selected model (for checkmark indicator)
        current_page: Current page number (1-indexed)
        page_size: Number of models per page
        
    Returns:
        List of button rows
    """
    if not models:
        return [[InlineButton(text="<< Back", callback_data="mdl_back")]]
    
    rows = []
    total_pages = max(1, (len(models) + page_size - 1) // page_size)
    
    # Calculate page slice
    start_idx = (current_page - 1) * page_size
    end_idx = min(start_idx + page_size, len(models))
    page_models = models[start_idx:end_idx]
    
    # Extract model ID from full path if needed
    current_model_id = current_model.split('/')[-1] if current_model and '/' in current_model else current_model
    
    # Model buttons - one per row
    for model in page_models:
        callback_data = build_model_selection_callback(provider, model)
        if not callback_data:
            continue  # Skip models that exceed callback_data limit
        
        is_current = model == current_model_id
        display_text = truncate_model_id(model)
        text = f"{display_text} ✓" if is_current else display_text
        
        rows.append([InlineButton(text=text, callback_data=callback_data)])
    
    # Pagination row
    if total_pages > 1:
        pagination_row = []
        
        if current_page > 1:
            pagination_row.append(InlineButton(
                text="◀ Prev",
                callback_data=f"mdl_list_{provider}_{current_page - 1}",
            ))
        
        pagination_row.append(InlineButton(
            text=f"{current_page}/{total_pages}",
            callback_data=f"mdl_list_{provider}_{current_page}",  # no-op
        ))
        
        if current_page < total_pages:
            pagination_row.append(InlineButton(
                text="Next ▶",
                callback_data=f"mdl_list_{provider}_{current_page + 1}",
            ))
        
        rows.append(pagination_row)
    
    # Back button
    rows.append([InlineButton(text="<< Back", callback_data="mdl_back")])
    
    return rows


def build_model_selection_callback(provider: str, model: str) -> Optional[str]:
    """
    Build callback data for model selection.
    
    Uses compact format if standard format exceeds 64 bytes.
    
    Args:
        provider: Provider ID
        model: Model ID
        
    Returns:
        Callback data string or None if too long
    """
    standard = f"mdl_sel_{provider}/{model}"
    if fits_callback_data(standard):
        return standard
    
    compact = f"mdl_sel/{model}"
    if fits_callback_data(compact):
        return compact
    
    return None


def build_browse_providers_button() -> List[List[InlineButton]]:
    """Build 'Browse providers' button for model selection intro."""
    return [[InlineButton(text="Browse providers", callback_data="mdl_prov")]]


def build_exec_approval_buttons(approval_id: str) -> List[List[InlineButton]]:
    """
    Build exec approval buttons.
    
    Args:
        approval_id: Unique approval request ID
        
    Returns:
        List of button rows with Allow Once/Always and Deny buttons
    """
    allow_once = f"/approve {approval_id} allow-once"
    if not fits_callback_data(allow_once):
        return []  # Can't create buttons if callback data is too long
    
    primary_row = [InlineButton(text="Allow Once", callback_data=allow_once)]
    
    allow_always = f"/approve {approval_id} allow-always"
    if fits_callback_data(allow_always):
        primary_row.append(InlineButton(text="Allow Always", callback_data=allow_always))
    
    rows = [primary_row]
    
    deny = f"/approve {approval_id} deny"
    if fits_callback_data(deny):
        rows.append([InlineButton(text="Deny", callback_data=deny)])
    
    return rows


def build_confirmation_buttons(
    confirm_callback: str,
    cancel_callback: str,
    confirm_text: str = "Confirm",
    cancel_text: str = "Cancel",
) -> List[List[InlineButton]]:
    """
    Build simple confirm/cancel button pair.
    
    Args:
        confirm_callback: Callback data for confirm button
        cancel_callback: Callback data for cancel button
        confirm_text: Text for confirm button
        cancel_text: Text for cancel button
        
    Returns:
        List with single row of two buttons
    """
    if not fits_callback_data(confirm_callback) or not fits_callback_data(cancel_callback):
        return []
    
    return [[
        InlineButton(text=cancel_text, callback_data=cancel_callback),
        InlineButton(text=confirm_text, callback_data=confirm_callback),
    ]]


def build_pagination_keyboard(
    current_page: int,
    total_pages: int,
    callback_prefix: str,
    extra_buttons: Optional[List[InlineButton]] = None,
) -> List[List[InlineButton]]:
    """
    Build pagination controls.
    
    Args:
        current_page: Current page (1-indexed)
        total_pages: Total number of pages
        callback_prefix: Prefix for callback data (page number appended)
        extra_buttons: Additional buttons to add before pagination
        
    Returns:
        List of button rows
    """
    rows = []
    
    if extra_buttons:
        rows.append(extra_buttons)
    
    if total_pages <= 1:
        return rows
    
    pagination_row = []
    
    if current_page > 1:
        pagination_row.append(InlineButton(
            text="◀ Prev",
            callback_data=f"{callback_prefix}_{current_page - 1}",
        ))
    
    pagination_row.append(InlineButton(
        text=f"{current_page}/{total_pages}",
        callback_data=f"{callback_prefix}_{current_page}",  # no-op
    ))
    
    if current_page < total_pages:
        pagination_row.append(InlineButton(
            text="Next ▶",
            callback_data=f"{callback_prefix}_{current_page + 1}",
        ))
    
    rows.append(pagination_row)
    return rows


def clear_inline_keyboard() -> Dict[str, Any]:
    """Return an empty inline keyboard to remove buttons from a message."""
    return {"inline_keyboard": []}


# ─────────────────────────────────────────────────────────────────────────────
# Utility Functions
# ─────────────────────────────────────────────────────────────────────────────

def calculate_total_pages(total_items: int, page_size: int = MODELS_PAGE_SIZE) -> int:
    """Calculate total pages needed for a list of items."""
    if page_size <= 0:
        return 1
    return max(1, (total_items + page_size - 1) // page_size)


def get_page_items(items: List[Any], page: int, page_size: int = MODELS_PAGE_SIZE) -> List[Any]:
    """Get items for a specific page."""
    if not items or page < 1:
        return []
    start = (page - 1) * page_size
    end = min(start + page_size, len(items))
    return items[start:end]
