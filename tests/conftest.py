import sys
from pathlib import Path

# make the project root importable for every test
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
