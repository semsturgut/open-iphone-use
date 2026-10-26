"""Bluetooth labels never substitute for a peer's exact identity."""
from collections import Counter
import re


def specific(value):
    clean=''.join(c for c in (value or '') if c.isprintable()).strip()[:80]
    return clean if clean.casefold() not in ('','iphone','apple iphone','unknown','bluetooth device') else None


def distinguish(items, identity='bt_id'):
    counts=Counter(item['name'].casefold() for item in items)
    result=[]
    for item in items:
        item=dict(item, bluetooth_name=item['name'])
        if counts[item['name'].casefold()]>1:
            peer=item.get(identity) or item.get('device_id','')
            suffix=re.sub('[^a-zA-Z0-9]','',peer)[-6:]
            item['name']=f"{item['name']} ({suffix})"
        result.append(item)
    return result
