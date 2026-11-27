#!/usr/bin/env python3
"""Start the HCG Repo commercial TEST environment and keep it running (docs/COMMERCIAL_TEST.md).

  tools/commercial-test/run_test_environment.py      # needs a local HCG Store checkout (HCG_STORE_CHECKOUT)

Prints the TEST HCG Repo site, the TEST HCG Store and the control port. Everything is local, uses TEST products, the
HCG Connect protocol mock and the test payment provider. Ctrl-C stops it and deletes the temporary files.
"""
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(HERE / "tests"), str(HERE / "tools")]
import test_commercial_acceptance as T  # noqa: E402

if not T.AVAILABLE:
    sys.exit("No local HCG Store checkout with node_modules (set HCG_STORE_CHECKOUT).")
T.CommercialAcceptance.setUpClass()
env = T.CommercialAcceptance.env
print(f"HCG Repo (TEST)   {T.CommercialAcceptance.repo_base}")
print(f"HCG Store (TEST)  {env.origin}   (certificate: {env.tmp / 'cert.pem'})")
print(f"Controls          http://127.0.0.1:{env.control_port}/  (next-user, clock, idp, authority, grant, revoke-all)")
print("TEST only: HCG Connect protocol mock, test ledger, test payments. Ctrl-C to stop.", flush=True)
try:
    while True:
        time.sleep(3600)
except KeyboardInterrupt:
    pass
finally:
    T.CommercialAcceptance.tearDownClass()
