commit 109bfb6b4deddba2969966ecc426d494008512e1
Author: opencode <opencode@rvlabs.tech>
Date:   Tue Mar 10 19:36:38 2026 +0000

    fix(agentscore): batch attestation, error tracking, retry logic, tests
    
    - Track tool_errors from agent:step events instead of hardcoding to 0
    - Batch attestation: accumulate pending, flush at count >= 25 or time >= 30m
    - Add attestBatch to contract ABI and AgentScoreChain
    - Persist failed/unflushed attestations to ~/.agentscore/pending.json
    - Retry pending attestations on startup
    - SIGTERM handler flushes pending before exit
    - Log warning when key file chmod fails
    - Add tests/test_agentscore.py (17 tests covering lifecycle, errors,
      batching, persistence, offline mode)

diff --git a/agentscore/identity.py b/agentscore/identity.py
index c7c3335..a1b71f0 100644
--- a/agentscore/identity.py
+++ b/agentscore/identity.py
@@ -47,8 +47,11 @@ def ensure_identity() -> Tuple[str, str]:
     KEY_FILE.write_text(private_key)
     try:
         os.chmod(KEY_FILE, 0o600)
-    except OSError:
-        pass  # Windows
+    except OSError as e:
+        logger.warning(
+            "[agentscore] Failed to set key file permissions (key may be world-readable): %s",
+            e,
+        )
 
     config = {
         "address": account.address,
@@ -66,6 +69,7 @@ def get_address() -> Optional[str]:
         return None
     try:
         from eth_account import Account
+
         private_key = KEY_FILE.read_text().strip()
         return Account.from_key(private_key).address
     except ImportError:
