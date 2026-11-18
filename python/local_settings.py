"""Persist user tuning independently of camera sessions."""
import json
import logging
import os
from pathlib import Path
import tempfile

from tracking_targets import PINCH_ACTIVATION_RATIO

SETTINGS_PATH = Path(__file__).resolve().parents[1] / 'config' / 'local-settings.json'


def valid_pinch_threshold(value):
    return type(value) in (int, float) and 0.05 <= value <= 0.60


def load_pinch_threshold(path):
    try:
        data = json.loads(path.read_text())
        value = data.get('pinch_threshold') if isinstance(data, dict) else None
        if not valid_pinch_threshold(value):
            raise ValueError('Invalid pinch threshold')
        return value
    except FileNotFoundError:
        return PINCH_ACTIVATION_RATIO
    except (OSError, ValueError) as error:
        logging.getLogger(__name__).warning('Using default pinch threshold: %s', error)
        return PINCH_ACTIVATION_RATIO


def save_pinch_threshold(path, value):
    if not valid_pinch_threshold(value):
        raise ValueError('Pinch threshold must be a ratio from 0.05 to 0.60.')
    temporary_path = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Replace atomically so an interrupted write cannot leave partial JSON.
        with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as output:
            temporary_path = Path(output.name)
            json.dump({'pinch_threshold': value}, output, indent=2)
            output.write('\n')
            output.flush()
            os.fsync(output.fileno())
        temporary_path.replace(path)
    except OSError as error:
        raise RuntimeError(f'Could not save pinch settings: {error}') from error
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
