"""Response-only image sizing. Capture, frame IDs and touch coordinates stay native."""
import base64
import hashlib
import io

from PIL import Image

from . import vision

IMAGE_MODES = ('full', 'preview', 'none')


def image_mode(value):
    if value not in IMAGE_MODES:
        raise ValueError('image_mode must be full, preview or none')
    return value


def frame_payload(image, mode='full', **metadata):
    image_mode(mode)
    width, height = vision.dimensions(image)
    result = dict(metadata, mimeType='image/jpeg', width=width, height=height,
                  sourceWidth=width, sourceHeight=height, screenHash=hashlib.sha256(image).hexdigest())
    if mode == 'none':
        return result
    if mode == 'preview':
        with Image.open(io.BytesIO(image)) as frame:
            frame.thumbnail((640, 640), Image.Resampling.LANCZOS)
            result['width'], result['height'] = frame.size
            output = io.BytesIO()
            frame.convert('RGB').save(output, format='JPEG', quality=70)
            image = output.getvalue()
    result['base64'] = base64.b64encode(image).decode()
    return result
