from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
PRODUCT_NAME = "TenderSentry"
OPENAI_MODEL = "gpt-4o"
DATA_DIR = "data/tenders"

CANADABUYS_OPEN_TENDERS_URL = (
    "https://canadabuys.canada.ca/opendata/pub/"
    "openTenderNotice-ouvertAvisAppelOffres.csv"
)
OPEN_TENDERS_CSV_PATH = PROJECT_ROOT / "data" / "open_tender_notices.csv"
NOTICES_PATH = PROJECT_ROOT / "data" / "notices.json"

REGION_FILTER = "Ontario"
CATEGORY_FILTER = "construction"
CATEGORY_KEYWORDS = [
    "construction",
    "paving",
    "roofing",
    "HVAC",
    "renovation",
    "bridge",
    "watermain",
    "sewer",
    "road",
]
