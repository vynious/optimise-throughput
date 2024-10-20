import logging
import sys

def configure_logger(name=None) -> logging.Logger:
    """Configure and return a logger."""
    logger = logging.getLogger(name)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')


    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    file_name = "async/async-debug.log" if name == "debug" else "async/status.log"
    file_handler = logging.FileHandler(file_name, mode="a")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    logger.setLevel(logging.DEBUG)
    return logger
