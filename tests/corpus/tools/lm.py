import sys; sys.path.insert(0, '/Users/julien.cruau/dev2/avolo-reframe')
import Vision, Foundation, objc
from adapters.obsws import ObsWs
from adapters.frames import FrameSource

with ObsWs() as o:
    fs = FrameSource(o, 'RF Cam', width=640); fs.grab(); jpeg = fs.grab()
    r = Vision.VNDetectFaceLandmarksRequest.alloc().init()
    nsd = Foundation.NSData.dataWithBytes_length_(jpeg, len(jpeg))
    Vision.VNImageRequestHandler.alloc().initWithData_options_(nsd, None).performRequests_error_([r], None)
    for f in r.results() or []:
        reg = f.landmarks().leftEye()
        n = reg.pointCount()
        raw = reg.normalizedPoints()
        print(f'pointCount        = {n}')
        print(f'type(normalizedPoints()) = {type(raw).__name__}  ({type(raw).__module__})')
        print(f'a-t-il as_tuple ? {hasattr(raw, "as_tuple")}')
        if hasattr(raw, 'as_tuple'):
            pts = raw.as_tuple(n)
            print(f'as_tuple({n}) -> {len(pts)} points : {[(round(p.x,3), round(p.y,3)) for p in pts]}')
        print('--- et pointsInImageOfSize_ ---')
        img = reg.pointsInImageOfSize_((640, 360))
        print(f'type = {type(img).__name__}, as_tuple ? {hasattr(img, "as_tuple")}')
        if hasattr(img, 'as_tuple'):
            print('  ', [(round(p.x,1), round(p.y,1)) for p in img.as_tuple(n)])
        break
