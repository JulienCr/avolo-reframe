try:
    import AVFoundation as AV
    session = AV.AVCaptureDeviceDiscoverySession.discoverySessionWithDeviceTypes_mediaType_position_(
        ["AVCaptureDeviceTypeBuiltInWideAngleCamera", "AVCaptureDeviceTypeExternal",
         "AVCaptureDeviceTypeContinuityCamera"], AV.AVMediaTypeVideo, 0)
    for d in session.devices():
        print(d.localizedName(), d.uniqueID(), d.isCenterStageActive())
    print("global center stage enabled:", AV.AVCaptureDevice.isCenterStageEnabled())
except Exception as e:
    print("ERROR", type(e), e)
