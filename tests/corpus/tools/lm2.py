import sys; sys.path.insert(0, '/Users/julien.cruau/dev2/avolo-reframe')
import Vision, Foundation, time
from adapters.obsws import ObsWs
from adapters.frames import FrameSource

with ObsWs() as o:
    fs = FrameSource(o, 'RF Cam', width=640)
    r = Vision.VNDetectFaceLandmarksRequest.alloc().init()
    faces = []
    for i in range(15):
        jpeg = fs.grab()
        nsd = Foundation.NSData.dataWithBytes_length_(jpeg, len(jpeg))
        Vision.VNImageRequestHandler.alloc().initWithData_options_(nsd, None).performRequests_error_([r], None)
        faces = r.results() or []
        if faces: print(f'visage trouve a l image {i}'); break
        time.sleep(0.3)
    if not faces:
        print('aucun visage sur 15 images -- personne devant la camera ?'); raise SystemExit(0)
    reg = faces[0].landmarks().leftEye()
    n = reg.pointCount()
    raw = reg.normalizedPoints()
    print(f'pointCount = {n}')
    print(f'type(normalizedPoints()) = {type(raw).__name__} du module {type(raw).__module__}')
    print(f'as_tuple present ? {hasattr(raw, "as_tuple")}   len() possible ? ', end='')
    try: print(len(raw))
    except Exception as e: print(f'NON ({type(e).__name__})')
    if hasattr(raw, 'as_tuple'):
        print(f'  as_tuple({n}) -> {[(round(p.x,3), round(p.y,3)) for p in raw.as_tuple(n)]}')
    img = reg.pointsInImageOfSize_((640, 360))
    print(f'pointsInImageOfSize_ -> {type(img).__name__}, as_tuple ? {hasattr(img, "as_tuple")}')
    if hasattr(img, 'as_tuple'):
        print(f'  {[(round(p.x,1), round(p.y,1)) for p in img.as_tuple(n)]}')
    print(f'\nboite du visage (Vision, origine en bas) : {faces[0].boundingBox()}')
