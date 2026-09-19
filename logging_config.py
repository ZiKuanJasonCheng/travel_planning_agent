import logging
import os
from datetime import datetime

_LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")


class TerminalRoutingFilter(logging.Filter):
    def filter(self, record):
        # Check for 'to_terminal'. If it's not provided, default to True.
        return getattr(record, 'to_terminal', True)


def setup_request_logging(prefix: str) -> str:
    """
    Configure the root logger to write this request's logs to a new file
    under logs/, named "{prefix}_{timestamp}.log". INFO logs also print to
    the terminal unless emitted with extra={"to_terminal": False}.

    Returns the log file path.
    """
    os.makedirs(_LOG_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path = os.path.join(_LOG_DIR, f"{prefix}_{timestamp}.log")

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    file_handler = logging.FileHandler(log_path)
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    terminal_handler = logging.StreamHandler()
    terminal_handler.setLevel(logging.INFO)
    terminal_handler.setFormatter(formatter)
    terminal_handler.addFilter(TerminalRoutingFilter())
    root_logger.addHandler(terminal_handler)

    return log_path
