"""Frame-timestamp scene boundaries; sampling cadence must not delay a cut."""
import re
from .utils import run,CommandError,emit


def scene_boundaries(media,*,progress=None,cancel_check=None):
    emit(progress,'scene_scan',0.0,'Locating original camera cuts at source-frame timestamps')
    result=run(['ffmpeg','-hide_banner','-nostats','-v','info','-filter_threads','2',
        '-i',str(media.path),'-an','-vf',"scale=160:90,select='gt(scene,0.24)',showinfo",
        '-f','null','-'],timeout=max(180,media.duration*3),cancel_check=cancel_check)
    found=[]
    frame=1/max(1,media.fps)
    for value in re.findall(r'pts_time:([0-9.]+)',result.stderr or ''):
        t=round(float(value)/frame)*frame
        if frame*.5<t<media.duration-frame*.5 and (not found or t-found[-1]>=frame*.8):
            found.append(round(t,6))
    emit(progress,'scene_scan',1.0,f'{len(found)} original camera cuts located; no added delay')
    return found
