import logging
import sys


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    # Never log request/response bodies at this level - passwords and JWTs pass through them.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
