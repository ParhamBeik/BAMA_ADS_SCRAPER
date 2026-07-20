"""Project paths used by the scraper and reports."""

from pathlib import Path


def get_project_root() -> Path:
    return Path(__file__).resolve().parents[2]


PROJECT_ROOT = get_project_root()
BAMA_SCRAPER_ROOT = PROJECT_ROOT / "bama-scraper"
DATA_DIR = BAMA_SCRAPER_ROOT / "data"
ADS_ROOT = DATA_DIR / "BAMA ADS"
OUTPUT_DIR = DATA_DIR / "outputs"
ANALYSIS_OUTPUT_DIR = OUTPUT_DIR
TIME_DICT_PATH = DATA_DIR / "time_dictionary.json"
UNKNOWN_TIMES_LOG = DATA_DIR / "unknown_times.log"
BAMA_DB_PATH = DATA_DIR / "bama.db"
# Legacy stores, kept only for the one-time migrate_to_db.py import.
CODE_MAP_PATH = DATA_DIR / "code_map.db"
HISTORY_DB_PATH = DATA_DIR / "history.db"
PROJECT_LOCK_PATH = DATA_DIR / ".writer.lock"
ROUTE_CONFLICTS_LOG = DATA_DIR / "route_conflicts.log"
BRAND_ALIASES_PATH = DATA_DIR / "brand_aliases.json"


if __name__ == "__main__":
    print(f"Project Root: {PROJECT_ROOT}")
    print(f"Bama Scraper Root: {BAMA_SCRAPER_ROOT}")
    print(f"Data Dir: {DATA_DIR}")
    print(f"ADS Root: {ADS_ROOT}")
    print(f"ADS Root exists: {ADS_ROOT.exists()}")
    print(f"Time Dict: {TIME_DICT_PATH}")
    print(f"Code Map: {CODE_MAP_PATH}")
    print(f"Analysis Output: {ANALYSIS_OUTPUT_DIR}")
