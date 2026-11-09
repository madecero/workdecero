"""Put the dev tooling and the engine on the import path for the tests."""
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "dev"))
sys.path.insert(0, os.path.join(REPO_ROOT, "engine"))
sys.path.insert(0, os.path.join(REPO_ROOT, "trackers"))

# Tests must never write into a real config folder. Anything that falls back to the
# default location lands in a throwaway folder instead.
import atexit  # noqa: E402
import shutil  # noqa: E402
import tempfile  # noqa: E402

_SCRATCH_HOME = tempfile.mkdtemp(prefix="workdecero-tests-")
os.environ["WORKDECERO_HOME"] = _SCRATCH_HOME
atexit.register(shutil.rmtree, _SCRATCH_HOME, True)
