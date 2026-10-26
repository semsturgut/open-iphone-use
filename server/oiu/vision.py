"""Safety gates for our own green-bordered white calibration board only."""
from io import BytesIO
from PIL import Image, ImageChops, ImageFilter


def image(jpeg):
    with Image.open(BytesIO(jpeg)) as source:
        if source.width * source.height > 20_000_000: raise ValueError('Capture exceeds 20 megapixels')
        source.thumbnail((750, 1400))
        return source.convert('RGB')


def board(jpeg):
    pic = image(jpeg)
    r, g, b = pic.split()
    mask = ImageChops.multiply(g.point(lambda v: 255 if v > 150 else 0),
                              ImageChops.multiply(r.point(lambda v: 255 if v < 80 else 0), b.point(lambda v: 255 if v < 180 else 0)))
    rect = mask.getbbox()
    if not rect: raise ValueError('Keep the green-bordered white Safari calibration board visible')
    x0, y0, x1, y1 = rect
    w, h = pic.size
    if x1-x0 < w*.6 or y1-y0 < h*.35: raise ValueError('Calibration board is partly obscured')
    # Require green on all four edges, not merely a green object elsewhere on screen.
    for box in ((x0,y0,x1,min(y1,y0+5)), (x0,max(y0,y1-5),x1,y1),
                (x0,y0,min(x1,x0+5),y1), (max(x0,x1-5),y0,x1,y1)):
        edge = mask.crop(box)
        if sum(edge.histogram()[1:]) < max(edge.size)*.3: raise ValueError('Calibration border is incomplete')
    interior = pic.crop((x0+5, y0+5, x1-5, y1-5)).convert('L')
    if sum(interior.histogram()[210:]) < interior.width*interior.height*.65:
        raise ValueError('Calibration board is covered; no click was sent')
    return pic, (x0/w, y0/h, (x1-x0)/w, (y1-y0)/h)


def pointer(jpeg):
    pic, rect = board(jpeg)
    w, h = pic.size
    x0, y0, rw, rh = rect
    pixels = pic.load()
    mask = Image.new('L', pic.size)
    data = mask.load()
    for y in range(int((y0+.015)*h), int((y0+rh-.015)*h)):
        for x in range(int((x0+.015)*w), int((x0+rw-.015)*w)):
            r,g,b = pixels[x,y]
            if 80 <= r <= 190 and max(r,g,b)-min(r,g,b) < 12: data[x,y]=255
    mask = mask.filter(ImageFilter.MinFilter(5))
    data = mask.load()
    candidates = []
    for y in range(h):
        for x in range(w):
            if not data[x,y]: continue
            queue = [(x,y)]; data[x,y]=0
            for px,py in queue:
                for nx,ny in ((px-1,py),(px+1,py),(px,py-1),(px,py+1)):
                    if 0 <= nx < w and 0 <= ny < h and data[nx,ny]:
                        data[nx,ny]=0; queue.append((nx,ny))
            xs,ys=zip(*queue)
            cw,ch=max(xs)-min(xs)+1,max(ys)-min(ys)+1
            fill=len(queue)/(cw*ch)
            if max(4,w//150) <= min(cw,ch) and max(cw,ch) <= w/10 and .75 <= cw/ch <= 1.33 and .6 <= fill <= .95:
                candidates.append((sum(xs)/len(xs)/w,sum(ys)/len(ys)/h))
    if len(candidates) != 1: raise ValueError('No single grey pointer is visible inside the board; no click was sent. Use the standard grey AssistiveTouch pointer')
    return candidates[0], rect
