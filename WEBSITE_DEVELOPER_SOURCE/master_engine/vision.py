from __future__ import annotations

import os
import bisect
from dataclasses import replace
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .config import Settings
from .models import EditProfile, FaceBox, LayoutSegment, MediaInfo, VisionSample
from .timeline import energy_at
from .subjects import MIN_FACE_AREA
from .utils import CommandError, cancelled, emit


def _find_yunet(cv2=None) -> Optional[Path]:
    model_dir = Path(__file__).resolve().parent / "assets" / "models"
    modern = cv2 is not None and str(cv2.__version__).split(".")[0] == "5"
    candidates = [
        Path(os.getenv("MASTER_YUNET_MODEL", "")),
        model_dir / ("face_detection_yunet_2026may.onnx" if modern else "face_detection_yunet_2023mar.onnx"),
        Path("/root/editingbot_v78/models/face_detection_yunet_2023mar.onnx"),
        Path("/root/editingbot_v78/models/face_detection_yunet.onnx"),
        model_dir / "face_detection_yunet_2023mar.onnx",
    ]
    return next((path for path in candidates if str(path) and path.is_file()), None)


class FaceDetector:
    def __init__(self, cv2):
        self.cv2 = cv2
        self.kind = "safe_full_source"
        self.detector = None
        model = _find_yunet(cv2)
        if model and hasattr(cv2, "FaceDetectorYN"):
            try:
                self.detector = cv2.FaceDetectorYN.create(str(model), "", (320, 320), .80, .30, 5000)
                self.kind = "yunet"
            except Exception:
                self.detector = None
        if self.detector is None and hasattr(cv2,"CascadeClassifier") and hasattr(cv2,"data"):
            self.kind = "safe_full_source"
            self.frontal = cv2.CascadeClassifier(
                str(Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml")
            )
            self.eyes = cv2.CascadeClassifier(
                str(Path(cv2.data.haarcascades) / "haarcascade_eye_tree_eyeglasses.xml")
            )

    def detect(self, frame) -> List[Tuple[int, int, int, int, float]]:
        cv2 = self.cv2
        height, width = frame.shape[:2]
        scale = min(1.0, 800.0 / max(width, height))
        work = frame if scale >= .999 else cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        wh, ww = work.shape[:2]
        rows: List[Tuple[int, int, int, int, float]] = []
        self.landmarks={}
        if self.kind == "safe_full_source":
            return []
        if self.kind == "yunet":
            self.detector.setInputSize((ww, wh))
            _, raw = self.detector.detect(work)
            for item in list(raw if raw is not None else []):
                x, y, w, h = [float(value) for value in item[:4]]
                score = float(item[-1])
                if score >= .80:
                    row=(int(x/scale),int(y/scale),int(w/scale),int(h/scale),score)
                    rows.append(row)
                    if len(item)>=15:
                        self.landmarks[row[:4]]=tuple((float(item[k])/scale,float(item[k+1])/scale) for k in range(4,14,2))
        else:
            gray = cv2.equalizeHist(cv2.cvtColor(work, cv2.COLOR_BGR2GRAY))
            for x, y, w, h in self.frontal.detectMultiScale(gray, 1.11, 5, minSize=(34, 34)):
                eye_rows = self.eyes.detectMultiScale(gray[y:y+h, x:x+w], 1.12, 3, minSize=(7, 7))
                if len(eye_rows) or w >= ww * .13:
                    rows.append((int(x/scale), int(y/scale), int(w/scale), int(h/scale), .74))
        valid: List[Tuple[int, int, int, int, float]] = []
        for x, y, w, h, score in rows:
            if not _subject_candidate((x,y,w,h,score), self.landmarks.get((x,y,w,h), ())):
                continue
            aspect = w / max(1.0, h)
            coverage = w * h / max(1.0, width * height)
            if not (.45 <= aspect <= 1.62 and MIN_FACE_AREA <= coverage <= .50):
                continue
            x = max(0, min(width - 2, x)); y = max(0, min(height - 2, y))
            w = max(2, min(width - x, w)); h = max(2, min(height - y, h))
            valid.append((x, y, w, h, score))
        valid.sort(key=lambda row: (row[4], row[2] * row[3]), reverse=True)
        kept: List[Tuple[int, int, int, int, float]] = []
        for row in valid:
            x, y, w, h, _ = row
            if any(
                abs((x+w/2) - (a+c/2)) < min(w, c) * .38
                and abs((y+h/2) - (b+d/2)) < min(h, d) * .38
                for a, b, c, d, _ in kept
            ):
                continue
            kept.append(row)
        # Four is the PANEL count, not the participant/detector limit.
        return sorted(kept, key=lambda row: row[0] + row[2] * .5)

    def motion_patches(self,frame,row):
        points=self.landmarks.get(row[:4]) if hasattr(self,'landmarks') else None
        if points:
            import numpy as np
            eyes=sorted(points[:2],key=lambda p:p[0])
            if abs(eyes[1][0]-eyes[0][0])>5:
                source=np.float32([eyes[0],eyes[1],points[2]])
                target=np.float32([(22,24),(58,24),(40,45)])
                matrix=self.cv2.getAffineTransform(source,target)
                aligned=self.cv2.warpAffine(frame,matrix,(80,96),flags=self.cv2.INTER_LINEAR,borderMode=self.cv2.BORDER_REPLICATE)
                gray=self.cv2.cvtColor(aligned,self.cv2.COLOR_BGR2GRAY)
                return self.cv2.resize(gray[51:84,13:67],(64,32)),self.cv2.resize(gray[10:39,14:66],(64,32))
        x,y,w,h,_=row
        upper=frame[max(0,int(y+h*.08)):max(1,int(y+h*.43)),max(0,int(x+w*.15)):max(1,int(x+w*.85))]
        patch=self.cv2.resize(self.cv2.cvtColor(upper,self.cv2.COLOR_BGR2GRAY),(64,32)) if upper.size else None
        return _mouth(frame,row,self.cv2),patch


def _mouth(frame, row, cv2):
    x, y, w, h, _ = row
    x1 = max(0, int(x + w*.18)); x2 = min(frame.shape[1], int(x + w*.82))
    y1 = max(0, int(y + h*.54)); y2 = min(frame.shape[0], int(y + h*.93))
    if x2 <= x1 or y2 <= y1:
        return None
    gray = cv2.cvtColor(frame[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)
    return cv2.resize(gray, (64, 32), interpolation=cv2.INTER_AREA)


def _assign_tracks(
    boxes: Sequence[Tuple[int, int, int, int, float]],
    width: int,
    height: int,
    previous: Dict[int, Tuple[float, float]],
    next_id: int,
) -> Tuple[List[int], Dict[int, Tuple[float, float]], int]:
    ids: List[int] = []
    available = set(previous)
    updated: Dict[int, Tuple[float, float]] = {}
    for x, y, w, h, _ in boxes:
        center = ((x + w*.5) / width, (y + h*.5) / height)
        best = None
        best_distance = 9.0
        for track_id in available:
            old = previous[track_id]
            distance = ((old[0]-center[0])**2 + (old[1]-center[1])**2) ** .5
            if distance < .20 and distance < best_distance:
                best, best_distance = track_id, distance
        if best is None:
            best = next_id
            next_id += 1
        else:
            available.remove(best)
        ids.append(best)
        updated[best] = center
    return ids, updated, next_id


def analyze(
    media: MediaInfo,
    profile: EditProfile,
    energy: Sequence[float],
    settings: Settings,
    *,
    progress=None,
    cancel_check: Optional[Callable[[], bool]] = None,
    transcript=None,
) -> List[VisionSample]:
    try:
        import cv2
        import numpy as np
    except ImportError:
        emit(progress, "vision", 1.0, "OpenCV unavailable; safe static layout selected")
        return []
    from .scenes import scene_boundaries
    cuts=scene_boundaries(media,progress=progress,cancel_check=cancel_check)
    cap = cv2.VideoCapture(str(media.path))
    if not cap.isOpened():
        raise CommandError("OpenCV could not open the input video")
    if hasattr(cv2, 'CAP_PROP_ORIENTATION_AUTO'):
        cap.set(cv2.CAP_PROP_ORIENTATION_AUTO, 1)
    detector = FaceDetector(cv2)
    guard = _SubjectGuard(cv2)
    samples: List[VisionSample] = []
    previous_scene = None
    previous_mouth: Dict[int, object] = {}
    previous_upper: Dict[int, object] = {}
    motion_average: Dict[int, float] = {}
    previous_tracks: Dict[int, Tuple[float, float]] = {}
    track_seen: Dict[int, float] = {}
    next_id = 1
    speech_floor = sorted(energy)[max(0, int(len(energy)*.35)-1)] if energy else 0.0
    cut_set={round(t,6) for t in cuts}
    times=sorted({round(n*settings.sample_seconds,6) for n in range(int(media.duration/settings.sample_seconds)+1)
        if n*settings.sample_seconds<media.duration}|cut_set)
    speech_words=sorted(getattr(transcript,'words',[]) or [],key=lambda word:word.start)
    word_starts=[word.start for word in speech_words]
    try:
        for t in times:
            if cancelled(cancel_check):
                raise CommandError("Master Editor job cancelled")
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
            ok, frame = cap.read()
            if not ok:
                continue
            height, width = frame.shape[:2]
            scene_gray = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (128, 72), interpolation=cv2.INTER_AREA)
            scene_delta = float(cv2.absdiff(previous_scene, scene_gray).mean()) / 255.0 if previous_scene is not None else 0.0
            if round(t,6) in cut_set:
                scene_delta=max(.30,scene_delta)
            previous_scene = scene_gray
            # Never interpret a hard camera cut as mouth movement or identity
            # travel. Reset short-lived trackers before analysing the new shot.
            if scene_delta >= .22:
                previous_mouth.clear()
                previous_upper.clear()
                motion_average.clear()
                previous_tracks.clear()
                track_seen.clear()
            boxes = detector.detect(frame)
            # Retain identities through brief detector dropouts. A single
            # missed sample must not create two "people" from one speaker.
            remembered={key:value for key,value in previous_tracks.items() if t-track_seen.get(key,-10)<=1.20}
            ids, current_tracks, next_id = _assign_tracks(boxes, width, height, remembered, next_id)
            previous_tracks={**remembered,**current_tracks}
            for key in ids:
                track_seen[key]=t
            edges = cv2.Canny(scene_gray, 70, 150)
            edge_density = float((edges > 0).mean())
            hsv = cv2.cvtColor(cv2.resize(frame, (128, 72)), cv2.COLOR_BGR2HSV)
            luma = float(scene_gray.mean()) / 255.0
            saturation = float(hsv[:, :, 1].mean()) / 255.0
            sharpness = min(1.0, float(cv2.Laplacian(scene_gray, cv2.CV_64F).var()) / 1200.0)
            band = scene_gray[36:55, :]
            band_edges = cv2.Canny(band, 70, 150)
            band_luma = float(band.mean())/255.0
            caption_edges = float((band_edges>0).mean())
            caption_contrast = float(band.std())/255.0
            hue_hist = cv2.calcHist([hsv], [0], (hsv[:,:,1]>50).astype(np.uint8)*255, [36], [0,180])
            dominant_hue = float(int(hue_hist.argmax())*10+5)
            face_rows: List[FaceBox] = []
            motions: Dict[int, float] = {}
            for row, track_id in zip(boxes, ids):
                mouth,upper=detector.motion_patches(frame,row)
                old = previous_mouth.get(track_id)
                motion = float(cv2.absdiff(old, mouth).mean()) / 255.0 if old is not None and mouth is not None else 0.0
                # Subtract face/body movement so nodding, a mic or a camera pan
                # is not automatically labelled as speech.
                if upper is not None:
                    previous=previous_upper.get(track_id)
                    head_motion=float(cv2.absdiff(previous,upper).mean())/255.0 if previous is not None else 0.0
                    previous_upper[track_id]=upper
                    motion=max(0.0,motion-head_motion*.65)
                motion=.60*motion+.40*motion_average.get(track_id,motion)
                motion_average[track_id]=motion
                previous_mouth[track_id] = mouth
                motions[track_id] = motion
                x, y, w, h, score = row
                face_rows.append(FaceBox(x/width, y/height, w/width, h/height, score, motion, track_id))
            visible_ids = {face.track_id for face in face_rows}
            previous_mouth = {
                track_id: mouth for track_id, mouth in previous_mouth.items()
                if track_id in visible_ids
            }
            active = None
            speaker_state = "ambiguous"
            speech_on = energy_at(energy, t) >= max(.0025, speech_floor * 1.05) if energy else False
            if speech_words:
                wi=bisect.bisect_right(word_starts,t+.08)-1
                speech_on=wi>=0 and speech_words[wi].end+.18>=t
            active_track=-1
            confidence=0.0
            if len(face_rows) >= 2 and speech_on:
                left, right = face_rows[0], face_rows[-1]
                lm = motions.get(left.track_id, 0.0); rm = motions.get(right.track_id, 0.0)
                gate = .007
                if lm >= gate and rm >= gate and max(lm, rm) <= min(lm, rm) * 1.62 + .002:
                    speaker_state = "both"
                elif lm >= gate and lm > rm * 1.34 + .002:
                    active, speaker_state = "left", "sole_left"
                    active_track=left.track_id
                    confidence=min(1.0,(lm-rm)/max(.008,lm))
                elif rm >= gate and rm > lm * 1.34 + .002:
                    active, speaker_state = "right", "sole_right"
                    active_track=right.track_id
                    confidence=min(1.0,(rm-lm)/max(.008,rm))
            elif len(face_rows) == 1 and speech_on:
                speaker_state = "single"
            samples.append(VisionSample(
                t, face_rows, active, scene_delta, edge_density,
                float(sum(motions.values())), speaker_state, luma, saturation, sharpness,
                band_luma, caption_edges, dominant_hue, caption_contrast,
                active_track,confidence,
            ))
            guard.observe(frame, samples[-1])
            if len(samples) % 25 == 0:
                emit(progress, "vision", min(.99, t/media.duration), "Tracking identities, mouth motion, scenes and framing")
    finally:
        cap.release()
    emit(progress, "vision", 1.0, f"{len(samples)} identity-aware visual samples analyzed")
    return guard.finish(samples)


# Public compatibility entrypoint; all new shot design lives in this module.
from .shot_director import build_layouts


# MASTER_SUBJECT_FIX_READABLE_1 -- conservative crop evidence, not biometric liveness.
def _subject_candidate(row, points):
    from math import hypot, isfinite
    x, y, w, h, score = row
    if not all(isfinite(v) for v in row) or score < .85 or min(w,h) < 2 or len(points) != 5:
        return False
    p = [((a-x)/w, (b-y)/h) for a,b in points]
    if any(not isfinite(a+b) or not (-.15 <= a <= 1.15 and -.15 <= b <= 1.15) for a,b in p):
        return False
    eye, mouth = (p[0][1]+p[1][1])/2, (p[3][1]+p[4][1])/2
    return (.035 <= hypot(p[1][0]-p[0][0],p[1][1]-p[0][1]) <= 1.10
        and .03 <= hypot(p[4][0]-p[3][0],p[4][1]-p[3][1]) <= .90
        and mouth-eye >= .14 and eye-.10 <= p[2][1] <= mouth+.10)

def _subject_patch(frame, face, cv):
    H,W = frame.shape[:2]
    x,y,w,h = face.x*W,face.y*H,face.w*W,face.h*H
    a,b = max(0,round(x-.12*w)),max(0,round(y-.10*h))
    c,d = min(W,round(x+1.12*w)),min(H,round(y+1.10*h))
    if c-a < 24 or d-b < 28:
        return None
    gray = cv.cvtColor(frame[b:d,a:c],cv.COLOR_BGR2GRAY)
    return cv.GaussianBlur(cv.resize(gray,(112,128),interpolation=cv.INTER_AREA),(3,3),.7)

def _subject_detail(old, new, cv):
    import numpy as np
    p = cv.goodFeaturesToTrack(old,140,.018,4,blockSize=3)
    if p is None or len(p) < 14:
        return None
    args = dict(winSize=(15,15),maxLevel=2,criteria=(cv.TERM_CRITERIA_EPS|cv.TERM_CRITERIA_COUNT,24,.02))
    q,s,e = cv.calcOpticalFlowPyrLK(old,new,p,None,**args)
    if q is None or s is None:
        return None
    back,bs,_ = cv.calcOpticalFlowPyrLK(new,old,q,None,**args)
    if back is None or bs is None:
        return None
    keep = (s.ravel()==1)&(bs.ravel()==1)&(np.linalg.norm(back-p,axis=2).ravel()<1.2)
    if e is not None:
        keep &= e.ravel()<24
    if keep.sum() < 12:
        return None
    M,mask = cv.findHomography(p[keep].reshape(-1,2),q[keep].reshape(-1,2),cv.RANSAC,1.4)
    if M is None or mask is None or mask.sum()<10 or mask.mean()<.5 or not np.isfinite(M).all():
        return None
    corners = np.float32([[[0,0],[111,0],[111,127],[0,127]]])
    mapped = cv.perspectiveTransform(corners,M)
    if not np.isfinite(mapped).all() or not 7000<abs(cv.contourArea(mapped))<28000 or abs(mapped-corners).max()>36:
        return None
    a = cv.warpPerspective(old,M,(112,128)).astype(np.float32)
    b = new.astype(np.float32)
    valid = cv.warpPerspective(np.full_like(old,255),M,(112,128),flags=cv.INTER_NEAREST)>250
    valid[:6,:] = valid[-6:,:] = valid[:,:6] = valid[:,-6:] = False
    stable = valid.copy()
    stable[27:65,18:94] = stable[67:113,20:92] = False
    if stable.sum()<100:
        return None
    gain = np.clip(b[stable].std()/max(1.0,a[stable].std()),.65,1.55)
    delta = abs(b-(gain*a+np.median((b-gain*a)[stable])))
    threshold = max(7.0,float(np.percentile(delta[12:29,20:92],70))*2.7+3)
    for ys,xs in ((slice(30,61),slice(21,91)),(slice(72,109),slice(24,88))):
        mask = valid[ys,xs]
        if mask.mean()>=.85 and (delta[ys,xs][mask]>threshold).mean()>=.06:
            return True
    return False

class _SubjectGuard:
    def __init__(self, cv):
        self.cv, self.last, self.tracks, self.records = cv, {}, [], []
    def observe(self, frame, sample):
        if sample.scene_delta>=.22:
            self.last.clear()
        keys = {}
        for face in sample.faces:
            patch = _subject_patch(frame,face,self.cv)
            previous = self.last.get(face.track_id)
            changed = None
            if previous is not None:
                t,old,old_patch,key = previous
                travel = ((face.cx-old.cx)**2+(face.cy-old.cy)**2)**.5
                ratio = max(face.w/old.w,old.w/face.w,face.h/old.h,old.h/face.h)
                if (0<sample.time-t<=.65 and travel<=.6*max(face.w,face.h,old.w,old.h)
                        and ratio<1.55 and patch is not None and old_patch is not None):
                    changed = _subject_detail(old_patch,patch,self.cv)
                    if changed is None and float(self.cv.absdiff(old_patch,patch).mean())<1.0:
                        changed = False  # Near-identical pixels are not an identity change or new live evidence.
            if changed is None:
                key = len(self.tracks)
                self.tracks.append([[],[]])
            times,events = self.tracks[key]
            times.append(sample.time)
            if changed:
                events.append(sample.time)
            keys[face.track_id] = key
            self.last[face.track_id] = (sample.time,face,patch,key)
        self.records.append(keys)
    def finish(self, samples):
        if len(samples)!=len(self.records):
            raise RuntimeError('Subject checks incomplete; refusing a guessed crop')
        accepted = {i for i,(ts,es) in enumerate(self.tracks)
            if len(ts)>=4 and ts[-1]-ts[0]>=.58 and len(es)>=3 and es[-1]-es[0]>=.35}
        result = []
        for sample,keys in zip(samples,self.records):
            faces = [f for f in sample.faces if keys.get(f.track_id) in accepted]
            if len(faces)==len(sample.faces):
                result.append(sample)
                continue
            state,side,track,confidence = ('single' if len(faces)==1 else 'ambiguous'),None,-1,0.0
            if len(faces)>=2 and sample.speaker_state!='ambiguous':
                left,right = faces[0],faces[-1]
                lm,rm = left.motion,right.motion
                if min(lm,rm)>=.007 and max(lm,rm)<=min(lm,rm)*1.62+.002:
                    state = 'both'
                elif lm>=.007 and lm>rm*1.34+.002:
                    side,state,track,confidence = 'left','sole_left',left.track_id,min(1.0,(lm-rm)/max(.008,lm))
                elif rm>=.007 and rm>lm*1.34+.002:
                    side,state,track,confidence = 'right','sole_right',right.track_id,min(1.0,(rm-lm)/max(.008,rm))
            result.append(replace(sample,faces=faces,active_side=side,speaker_state=state,
                active_track_id=track,speech_confidence=confidence,motion_energy=sum(f.motion for f in faces)))
        return result
