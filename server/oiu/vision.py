"""Image facts reported with every frame."""
from io import BytesIO
from PIL import Image


def dimensions(jpeg):
    with Image.open(BytesIO(jpeg)) as source:
        return source.size
