"""Local OFL font assets: same typefaces for measurement and libass rendering."""
from pathlib import Path
import struct
from functools import lru_cache

FONT_DIR=Path(__file__).parent/'assets/fonts/ttf'


def render_family(family,weight=700):
    if family=='Montserrat':
        return 'Montserrat ExtraBold' if weight>=800 else 'Montserrat SemiBold' if weight<700 else family
    return family


def bundled_font(family,weight=700,italic=False):
    if family in {'Montserrat','Montserrat ExtraBold','Montserrat SemiBold'}:
        weight=800 if family=='Montserrat ExtraBold' else 600 if family=='Montserrat SemiBold' else weight
        name='Montserrat-'+('ExtraBold' if weight>=800 else 'Bold' if weight>=700 else 'SemiBold')+'.ttf'
    elif family=='Anton':
        name='Anton-Regular.ttf'
    elif family=='Barlow Condensed':
        name='BarlowCondensed-Bold'+('Italic' if italic else '')+'.ttf'
    elif family=='DM Serif Display':
        name='DMSerifDisplay-'+('Italic' if italic else 'Regular')+'.ttf'
    else:
        return ''
    path=FONT_DIR/name
    return str(path) if path.is_file() else ''


@lru_cache(maxsize=32)
def ass_metric_scale(path):
    """TTF Windows metrics used by libass; no fontTools runtime dependency."""
    try:
        with open(path,'rb') as handle:
            header=handle.read(12)
            count=struct.unpack('>H',header[4:6])[0]
            tables={}
            for _ in range(count):
                tag,_,offset,length=struct.unpack('>4sIII',handle.read(16))
                tables[tag]=(offset,length)
            handle.seek(tables[b'head'][0]+18)
            units=struct.unpack('>H',handle.read(2))[0]
            handle.seek(tables[b'OS/2'][0]+74)
            ascent,descent=struct.unpack('>HH',handle.read(4))
            return units/max(1,ascent+descent)
    except (OSError,KeyError,struct.error):
        return 1.0


def check_fonts():
    from PIL import ImageFont
    found=[]
    for family,weight,italic in [('Montserrat',600,False),('Montserrat',700,False),('Montserrat',800,False),
        ('Anton',400,False),('Barlow Condensed',700,False),('Barlow Condensed',700,True),
        ('DM Serif Display',400,False),('DM Serif Display',400,True)]:
        path=bundled_font(family,weight,italic)
        if not path:
            raise ValueError('Bundled caption font is missing: '+family)
        ImageFont.truetype(path,64)
        found.append(Path(path).name)
    return found
