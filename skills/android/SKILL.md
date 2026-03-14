---
name: android
description: Control an Android phone remotely — navigate apps, tap, type, swipe, and automate Uber, WhatsApp, Spotify, Maps, Settings, Tinder
version: 1.0.0
metadata:
  hermes:
    tags: [android, phone, automation, accessibility]
    category: android
---

# Android Device Control

You can control an Android phone remotely using the `android_*` tools. The phone runs a companion app called **Hermes Bridge** which exposes an HTTP API. You communicate with it over the network — no USB, no ADB, no physical connection needed.

## How It Works

```
Hermes Agent (this server)  ──HTTP──>  Hermes Bridge app (Android phone)
                                        ├── Reads screen via AccessibilityService
                                        ├── Performs taps, types, swipes
                                        └── Authenticated via pairing code
```

## Setup / Connecting a Phone

When the user wants to connect their phone, ask for their **pairing code** — a 6-character code shown in the Hermes Bridge app (e.g. `K7V3NP`).

Then call:
```
android_setup("<pairing_code>")
```

This does two things:
1. Starts a relay on this server (auto-detects the server's public IP)
2. Returns the exact instructions to tell the user — the server address and pairing code to enter in their phone app

**Relay the `user_instructions` field from the result directly to the user.** It contains the server IP and port they need to type into the phone app.

After the user taps Connect on their phone, the phone connects to this server via WebSocket. Call `android_ping()` to verify the connection is live.

**Do NOT ask about:**
- USB, ADB, or developer options
- The phone's IP address (not needed — the phone connects to the server, not the other way around)
- nginx, firewalls, or port forwarding
- Any networking concepts

**Just ask for the pairing code, call setup, and relay the instructions.**

## Core Patterns

### Always read before acting
Call `android_read_screen()` before tapping. Never guess coordinates.

### Prefer text over coordinates
Use `android_tap_text("Continue")` over `android_tap(x=540, y=1200)`.

### Wait after navigation
After opening an app or tapping a button that triggers loading:
```
android_wait(text="<expected element>", timeout_ms=8000)
```

### Confirm destructive actions
Before confirming a purchase, ride, or send — report to the user and wait for approval.

### Typing into a field
1. `android_tap_text("<field label>")` — focus the field
2. `android_wait(class_name="android.widget.EditText")`
3. `android_type("<text>", clear_first=True)`

### Navigation
- Back: `android_press_key("back")`
- Home: `android_press_key("home")`
- Notifications: `android_press_key("notifications")`
- Find package name: `android_get_apps()` then search results

### Permission dialogs
Look for "Allow" / "Deny" / "While using the app" after opening apps:
`android_tap_text("Allow")`

### When accessibility tree is insufficient
Use `android_screenshot()` for apps with canvas/custom rendering, then use coordinates.

---

## Common Package Names

| App | Package |
|-----|---------|
| Uber | com.ubercab |
| Bolt | com.bolt.client |
| WhatsApp | com.whatsapp |
| Spotify | com.spotify.music |
| Google Maps | com.google.android.apps.maps |
| Chrome | com.android.chrome |
| Gmail | com.google.android.gm |
| Instagram | com.instagram.android |
| X/Twitter | com.twitter.android |
| Tinder | com.tinder |
| Settings | com.android.settings |

---

## App-Specific Procedures

### Uber — Order a ride

1. `android_open_app("com.ubercab")`
2. `android_wait(text="Where to?", timeout_ms=8000)`
3. `android_tap_text("Where to?")`
4. `android_type("<destination>", clear_first=True)`
5. `android_wait(text="<destination keyword>")` then tap suggestion
6. `android_read_screen()` — read price and car options
7. **STOP** — Report options and price to user, wait for confirmation
8. After confirmation: `android_tap_text("UberX")` then `android_tap_text("Confirm UberX")`
9. `android_wait(text="Finding your driver", timeout_ms=10000)`

**Pitfalls:** Uber may block accessibility taps on some versions — fall back to screenshot + coordinates. Always mention surge pricing to user.

### WhatsApp — Send a message

1. `android_open_app("com.whatsapp")`
2. `android_wait(text="Chats")`
3. Existing chat: `android_tap_text("<contact name>")`
4. New chat: `android_tap_text("New chat")` → type contact name → tap match
5. `android_tap_text("Type a message")`
6. `android_type("<message text>")`
7. **STOP** — Confirm with user before sending
8. `android_tap_text("Send")` or `android_press_key("enter")`

**Pitfalls:** Message input is `android.widget.EditText`. Read screen after typing to verify before sending.

### Spotify — Play music

1. `android_open_app("com.spotify.music")`
2. `android_wait(text="Search", timeout_ms=8000)`
3. `android_tap_text("Search")`
4. `android_wait(class_name="android.widget.EditText")`
5. `android_type("<query>", clear_first=True)`
6. `android_wait(text="Songs", timeout_ms=5000)`
7. `android_read_screen()` then tap desired result

**Playback:** `android_tap_text("Play")`, `android_tap_text("Next")`, `android_tap_text("Pause")`

**Pitfalls:** Spotify uses custom views — screenshot may be more useful than read_screen.

### Google Maps — Get directions

1. `android_open_app("com.google.android.apps.maps")`
2. `android_wait(text="Search here", timeout_ms=8000)`
3. `android_tap_text("Search here")`
4. `android_type("<destination>", clear_first=True)`
5. Tap suggestion → `android_tap_text("Directions")`
6. `android_read_screen()` — report time, distance, route to user
7. Start navigation only if user confirms: `android_tap_text("Start")`

**Pitfalls:** Maps uses heavy canvas rendering — prefer `android_screenshot()`. Exit navigation with `android_press_key("back")`.

### Settings — Change system settings

1. `android_open_app("com.android.settings")`
2. `android_wait(text="Settings", timeout_ms=5000)`
3. Navigate by tapping section names:
   - "Network & internet" → WiFi, mobile data
   - "Connected devices" → Bluetooth, NFC
   - "Display" → Brightness, dark mode
   - "Sound & vibration" → Volume
   - "Apps" → App management
4. `android_read_screen()` to find specific toggles

**Pitfalls:** Settings UI varies across manufacturers (Samsung, Pixel, Xiaomi). Always read_screen to discover actual labels. Use `android_scroll("down")` if setting not visible.

### Tinder — View profiles and interact

1. `android_open_app("com.tinder")`
2. `android_wait(timeout_ms=8000)`
3. `android_read_screen()` + `android_screenshot()` — Tinder is image-heavy
4. Report profile details to user

**IMPORTANT:** Always confirm with user before swiping or messaging.
- Like: `android_swipe("right")`
- Pass: `android_swipe("left")`
- Super Like: `android_swipe("up")`

**Pitfalls:** Tinder uses custom UI — accessibility tree is limited, prefer screenshots. "It's a Match!" popup: tap anywhere to dismiss.
