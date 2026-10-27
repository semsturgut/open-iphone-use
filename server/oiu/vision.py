"""Safety gates for our own green-bordered white calibration board only."""
from io import BytesIO
import math
from PIL import Image, ImageChops, ImageFilter


def dimensions(jpeg):
    with Image.open(BytesIO(jpeg)) as source:
        return source.size

def image(jpeg):
    with Image.open(BytesIO(jpeg)) as source:
        if source.width * source.height > 20_000_000: raise ValueError('Capture exceeds 20 megapixels')
        source.thumbnail((750, 1400))
        return source.convert('RGB')


def components(mask):
    """Connected green objects, so status/battery icons cannot extend board bounds."""
    w,h=mask.size
    pixels=bytearray(mask.tobytes())
    found=[]
    for seed,value in enumerate(pixels):
        if not value: continue
        pixels[seed]=0
        pending=[seed]
        x0=x1=seed%w; y0=y1=seed//w
        for index in pending:
            x,y=index%w,index//w
            x0=min(x0,x);x1=max(x1,x);y0=min(y0,y);y1=max(y1,y)
            for ny in range(max(0,y-1),min(h,y+2)):
                for nx in range(max(0,x-1),min(w,x+2)):
                    n=ny*w+nx
                    if pixels[n]: pixels[n]=0;pending.append(n)
        found.append((len(pending),(x0,y0,x1+1,y1+1)))
    return sorted(found,reverse=True)


def board(jpeg):
    pic = image(jpeg)
    r, g, b = pic.split()
    mask = ImageChops.multiply(g.point(lambda v: 255 if v > 150 else 0),
                              ImageChops.multiply(r.point(lambda v: 255 if v < 80 else 0), b.point(lambda v: 255 if v < 180 else 0)))
    objects=components(mask)
    rectangles=[box for _,box in objects if box[2]-box[0]>=pic.width*.6 and box[3]-box[1]>=pic.height*.35]
    if len(rectangles)>1: raise ValueError('More than one possible calibration board')
    rect=rectangles[0] if rectangles else None
    if not rect: raise ValueError('Calibration board is closed or not visible: status dots and charging battery icons are not the board. Open the green-bordered white Safari board and keep sharing')
    x0, y0, x1, y1 = rect
    w, h = pic.size
    if x1-x0 < w*.6 or y1-y0 < h*.35: raise ValueError('Calibration board is closed or not visible: the green status dot is not the board. Open the Safari calibration board and keep sharing')
    # Require green on all four edges, not merely a green object elsewhere on screen.
    for box in ((x0,y0,x1,min(y1,y0+5)), (x0,max(y0,y1-5),x1,y1),
                (x0,y0,min(x1,x0+5),y1), (max(x0,x1-5),y0,x1,y1)):
        edge = mask.crop(box)
        if sum(edge.histogram()[1:]) < max(edge.size)*.3: raise ValueError('Calibration border is incomplete')
    interior = pic.crop((x0+5, y0+5, x1-5, y1-5)).convert('L')
    if sum(interior.histogram()[210:]) < interior.width*interior.height*.65:
        raise ValueError('Calibration board is covered')
    return pic, (x0/w, y0/h, (x1-x0)/w, (y1-y0)/h)


def safe_calibration_point(point, rect):
    """Keep calibration clicks inside the board and away from its Back button."""
    x,y=point; bx,by,bw,bh=rect
    inside=bx+.04*bw<x<bx+.9*bw and by+.06*bh<y<by+.88*bh
    back_zone=x>bx+.7*bw and y<by+.18*bh
    return inside and not back_zone


def pointer(jpeg, board_required=True, expected=None, radius=.18, reference=None):
    on_board=True
    if board_required: pic,rect=board(jpeg)
    else:
        try: pic,rect=board(jpeg)
        except ValueError: pic,rect=image(jpeg),(0,0,1,1); on_board=False
    w,h=pic.size
    x0,y0,rw,rh=rect
    left,top=max(0,int((x0+.015)*w)),max(0,int((y0+.015)*h))
    right,bottom=min(w,int((x0+rw-.015)*w)),min(h,int((y0+rh-.015)*h))
    if expected is not None:
        left=max(left,int((expected[0]-radius)*w));right=min(right,int((expected[0]+radius)*w)+1)
        top=max(top,int((expected[1]-radius)*h));bottom=min(bottom,int((expected[1]+radius)*h)+1)
    if right<=left or bottom<=top: raise ValueError('Pointer target is outside the observable screen area')
    pixels=pic.crop((left,top,right,bottom))
    difference=None
    if reference is not None:
        prior=image(reference)
        if prior.size != pic.size: raise ValueError('Screen geometry changed while locating the pointer')
        delta=ImageChops.difference(pic,prior).convert('L')
        difference=delta.point(lambda value: 255 if value>=12 else 0)
    # A translucent grey circle inherits color from the app below it. Narrow luminance
    # bands isolate it even on grey/colored screens without accepting a whole grey panel.
    bands=[(80,190,12)] if on_board or expected is None else [(80,190,12),(60,110,48),(100,145,48),(135,180,48),(170,215,48)]
    candidates=[]
    for low,high,chroma in bands:
        mask=Image.new('L',pixels.size)
        mask.putdata([255 if low<=r<=high and max(r,g,b)-min(r,g,b)<chroma else 0 for r,g,b in (pixels.get_flattened_data() if hasattr(pixels,'get_flattened_data') else pixels.getdata())])
        mask=mask.filter(ImageFilter.MinFilter(5))
        for count,(bx,by,ex,ey) in components(mask):
            cw,ch=ex-bx,ey-by
            fill=count/(cw*ch)
            if max(4,w//150)<=min(cw,ch) and max(cw,ch)<=w/10 and .8<=cw/ch<=1.25 and .6<=fill<=.95:
                point=((left+(bx+ex-1)/2)/w,(top+(by+ey-1)/2)/h)
                if expected is not None and math.dist(point,expected)>radius: continue
                if difference is not None:
                    changed=difference.crop((left+bx,top+by,left+ex,top+ey))
                    if changed.histogram()[255] < changed.width*changed.height*.2: continue
                if not any(math.dist(point,old)<.006 for old in candidates): candidates.append(point)
    if len(candidates)!=1:
        raise ValueError('No single grey pointer is visible '+('inside the board' if on_board else 'on the shared screen')+'. Use the standard grey AssistiveTouch pointer, keep it visible and leave pointer size/Zoom unchanged')
    return candidates[0],rect


def pointer_difference(jpeg, reference, expected, radius=.18, exclude=None):
    """Find a localized newly visible grey disc; reject widespread screen changes."""
    pic, prior = image(jpeg), image(reference)
    if pic.size != prior.size: raise ValueError('Screen geometry changed while locating the pointer')
    w,h=pic.size
    delta=ImageChops.difference(pic,prior).convert('L')
    mask=delta.point(lambda v: 255 if v >= 12 else 0)
    if mask.histogram()[255] > w*h*.025: raise ValueError('Screen content changed too broadly')
    candidates=[]
    for count,(x0,y0,x1,y1) in components(mask):
        cw,ch=x1-x0,y1-y0
        if not (max(5,w//150)<=min(cw,ch) and max(cw,ch)<=w/10 and .75<=cw/ch<=1.33 and .5<=count/(cw*ch)<=.95): continue
        point=((x0+x1-1)/2/w,(y0+y1-1)/2/h)
        if math.dist(point,expected)>min(radius,.06): continue
        if exclude is not None and math.hypot((point[0]-exclude[0])*w,(point[1]-exclude[1])*h) <= max(w*.03, 1.5*max(cw,ch)): continue
        pixels=pic.crop((x0,y0,x1,y1))
        changes=mask.crop((x0,y0,x1,y1))
        pixdata=pixels.get_flattened_data() if hasattr(pixels,'get_flattened_data') else pixels.getdata()
        changedata=changes.get_flattened_data() if hasattr(changes,'get_flattened_data') else changes.getdata()
        pairs=zip(pixdata,changedata)
        grey=sum(1 for (r,g,b),changed in pairs if changed and 45<=max(r,g,b)<=225 and max(r,g,b)-min(r,g,b)<=50)
        if grey >= count*.7: candidates.append(point)
    if len(candidates)!=1: raise ValueError('No localized grey pointer change found')
    return candidates[0],(0,0,1,1)
