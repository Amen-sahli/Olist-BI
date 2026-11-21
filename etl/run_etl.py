import logging
import time
from .config import get_engine
from .extract import extract
from .load import load_all
from .checks import run_checks

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("etl")


def main():
    engine = get_engine()
    t0 = time.time()
    log.info("== EXTRACT ==")
    extract(engine)
    log.info("== TRANSFORM + LOAD ==")
    load_all(engine)
    log.info("== QUALITY CHECKS ==")
    run_checks(engine)
    log.info("ETL finished in %.1fs", time.time() - t0)


if __name__ == "__main__":
    main()