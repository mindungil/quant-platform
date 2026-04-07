import sys, os

# Add service root and repo root to path
_service_dir = os.path.dirname(__file__)
_repo_root = os.path.dirname(os.path.dirname(_service_dir))
sys.path.insert(0, _service_dir)
sys.path.insert(0, _repo_root)

