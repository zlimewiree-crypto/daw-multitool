// DAW Multitool Audio: a virtual stereo audio device (a Core Audio server plug-in) that sits between a DAW and the
// real audio interface. The DAW plays into it; DAW Multitool's bridge (native/audio_bridge.c) reads the same audio
// back from its input side and passes it on to the interface, so DAW Multitool can work on what the DAW plays.
//
// Its clock can run slower or faster than real time. The DAW renders whenever the device asks for the next block,
// so at 75% the whole DAW engine runs at 75% speed without knowing (tape-style varispeed); the bridge resamples
// it back to real time so the pitch follows the speed. Speed changes are limited per zero-timestamp period (rise
// 0.5 semitone, fall 1 semitone per 1024 frames) because Core Audio only learns a new rate one period late.
//
// Written for DAW Multitool from Apple's AudioServerPlugIn.h documentation. Built by
// scripts/build_audio_device.py; installed only when the user clicks Install in DAW Multitool's Settings.
#include <CoreAudio/AudioServerPlugIn.h>
#include <mach/mach_time.h>
#include <math.h>
#include <pthread.h>
#include <stdatomic.h>
#include <string.h>

#define kBundleID        "com.dawmultitool.audio.driver"
#define kDeviceUID       "com.dawmultitool.audio.device"
#define kModelUID        "com.dawmultitool.audio.model"
#define kDeviceName      "DAW Multitool"
#define kManufacturer    "DAW Multitool"

enum {
    kObjectID_PlugIn = kAudioObjectPlugInObject,
    kObjectID_Device = 2,
    kObjectID_Stream_Input = 3,
    kObjectID_Stream_Output = 4,
};

#define kChannels               2
#define kPeriodFrames           1024        // zero-timestamp period; Core Audio caches it, so it never changes
#define kRingFrames             65536       // loopback ring: far more than any DAW-to-bridge gap
#define kMinSpeed               0.5
#define kMaxSpeed               2.0
#define kMaxRisePerPeriod       0.5         // semitones per period
#define kMaxFallPerPeriod       1.0
#define kProperty_TargetSpeed   'dmts'      // custom properties, CFNumber (double)
#define kProperty_CurrentSpeed  'dmcs'

static const Float64 kSampleRates[] = {44100.0, 48000.0, 88200.0, 96000.0};
#define kSampleRateCount (sizeof(kSampleRates) / sizeof(kSampleRates[0]))

static AudioServerPlugInHostRef gHost = NULL;
static pthread_mutex_t gStateMutex = PTHREAD_MUTEX_INITIALIZER;   // properties
static pthread_mutex_t gClockMutex = PTHREAD_MUTEX_INITIALIZER;   // the clock, used from the IO thread
static atomic_uint gRefCount = 0;
static Float64 gSampleRate = 48000.0;
static Float64 gPendingSampleRate = 0.0;
static UInt32 gIOCount = 0;
static Boolean gInputActive = true, gOutputActive = true;
static float gRing[kRingFrames * kChannels];
static SInt64 gWriteEnd = 0;  // the sample time just after the last audio the DAW wrote

// clock: the host time of the current period boundary, how many periods have passed, and the speed
static Float64 gHostTicksPerSecond = 0.0;
static Float64 gAnchorHostTime = 0.0;
static UInt64 gPeriodCount = 0;
static Float64 gSpeed = 1.0;
static _Atomic double gTargetSpeed = 1.0;
static _Atomic double gReportedSpeed = 1.0;

// ---------------------------------------------------------------------------------------------- helpers
static Float64 clampSpeed(Float64 speed) {
    if (!(speed == speed)) return 1.0;  // NaN
    return speed < kMinSpeed ? kMinSpeed : (speed > kMaxSpeed ? kMaxSpeed : speed);
}

static Boolean isSupportedRate(Float64 rate) {
    for (UInt32 i = 0; i < kSampleRateCount; i++) if (kSampleRates[i] == rate) return true;
    return false;
}

static AudioStreamBasicDescription streamFormat(Float64 rate) {
    AudioStreamBasicDescription f = {0};
    f.mSampleRate = rate;
    f.mFormatID = kAudioFormatLinearPCM;
    f.mFormatFlags = kAudioFormatFlagIsFloat | kAudioFormatFlagsNativeEndian | kAudioFormatFlagIsPacked;
    f.mBytesPerPacket = 4 * kChannels;
    f.mFramesPerPacket = 1;
    f.mBytesPerFrame = 4 * kChannels;
    f.mChannelsPerFrame = kChannels;
    f.mBitsPerChannel = 32;
    return f;
}

// One step of the speed toward its target, limited in semitones per period.
static Float64 stepSpeed(Float64 speed, Float64 target) {
    Float64 semis = 12.0 * log2(target / speed);
    if (semis > kMaxRisePerPeriod) semis = kMaxRisePerPeriod;
    if (semis < -kMaxFallPerPeriod) semis = -kMaxFallPerPeriod;
    return clampSpeed(speed * exp2(semis / 12.0));
}

static void notify(AudioObjectID object, AudioObjectPropertySelector selector) {
    if (gHost == NULL) return;
    AudioObjectPropertyAddress address = {selector, kAudioObjectPropertyScopeGlobal, kAudioObjectPropertyElementMain};
    gHost->PropertiesChanged(gHost, object, 1, &address);
}

static void storeSampleRate(Float64 rate) {
    if (gHost == NULL) return;
    CFNumberRef value = CFNumberCreate(NULL, kCFNumberFloat64Type, &rate);
    if (value) { gHost->WriteToStorage(gHost, CFSTR("sample rate"), value); CFRelease(value); }
}

// ---------------------------------------------------------------------------------------------- IUnknown
static HRESULT QueryInterface(void* inDriver, REFIID inUUID, LPVOID* outInterface);
static ULONG AddRef(void* inDriver);
static ULONG Release(void* inDriver);
static OSStatus Initialize(AudioServerPlugInDriverRef inDriver, AudioServerPlugInHostRef inHost);
static OSStatus CreateDevice(AudioServerPlugInDriverRef d, CFDictionaryRef desc, const AudioServerPlugInClientInfo* c, AudioObjectID* out);
static OSStatus DestroyDevice(AudioServerPlugInDriverRef d, AudioObjectID device);
static OSStatus AddDeviceClient(AudioServerPlugInDriverRef d, AudioObjectID device, const AudioServerPlugInClientInfo* c);
static OSStatus RemoveDeviceClient(AudioServerPlugInDriverRef d, AudioObjectID device, const AudioServerPlugInClientInfo* c);
static OSStatus PerformDeviceConfigurationChange(AudioServerPlugInDriverRef d, AudioObjectID device, UInt64 action, void* info);
static OSStatus AbortDeviceConfigurationChange(AudioServerPlugInDriverRef d, AudioObjectID device, UInt64 action, void* info);
static Boolean HasProperty(AudioServerPlugInDriverRef d, AudioObjectID object, pid_t pid, const AudioObjectPropertyAddress* a);
static OSStatus IsPropertySettable(AudioServerPlugInDriverRef d, AudioObjectID object, pid_t pid, const AudioObjectPropertyAddress* a, Boolean* out);
static OSStatus GetPropertyDataSize(AudioServerPlugInDriverRef d, AudioObjectID object, pid_t pid, const AudioObjectPropertyAddress* a,
                                    UInt32 qSize, const void* q, UInt32* outSize);
static OSStatus GetPropertyData(AudioServerPlugInDriverRef d, AudioObjectID object, pid_t pid, const AudioObjectPropertyAddress* a,
                                UInt32 qSize, const void* q, UInt32 inSize, UInt32* outSize, void* outData);
static OSStatus SetPropertyData(AudioServerPlugInDriverRef d, AudioObjectID object, pid_t pid, const AudioObjectPropertyAddress* a,
                                UInt32 qSize, const void* q, UInt32 inSize, const void* inData);
static OSStatus StartIO(AudioServerPlugInDriverRef d, AudioObjectID device, UInt32 client);
static OSStatus StopIO(AudioServerPlugInDriverRef d, AudioObjectID device, UInt32 client);
static OSStatus GetZeroTimeStamp(AudioServerPlugInDriverRef d, AudioObjectID device, UInt32 client, Float64* outSampleTime,
                                 UInt64* outHostTime, UInt64* outSeed);
static OSStatus WillDoIOOperation(AudioServerPlugInDriverRef d, AudioObjectID device, UInt32 client, UInt32 op, Boolean* outWillDo,
                                  Boolean* outInPlace);
static OSStatus BeginIOOperation(AudioServerPlugInDriverRef d, AudioObjectID device, UInt32 client, UInt32 op, UInt32 frames,
                                 const AudioServerPlugInIOCycleInfo* cycle);
static OSStatus DoIOOperation(AudioServerPlugInDriverRef d, AudioObjectID device, AudioObjectID stream, UInt32 client, UInt32 op,
                              UInt32 frames, const AudioServerPlugInIOCycleInfo* cycle, void* main, void* secondary);
static OSStatus EndIOOperation(AudioServerPlugInDriverRef d, AudioObjectID device, UInt32 client, UInt32 op, UInt32 frames,
                               const AudioServerPlugInIOCycleInfo* cycle);

static AudioServerPlugInDriverInterface gInterface = {
    ._reserved = NULL,
    .QueryInterface = QueryInterface,
    .AddRef = AddRef,
    .Release = Release,
    .Initialize = Initialize,
    .CreateDevice = CreateDevice,
    .DestroyDevice = DestroyDevice,
    .AddDeviceClient = AddDeviceClient,
    .RemoveDeviceClient = RemoveDeviceClient,
    .PerformDeviceConfigurationChange = PerformDeviceConfigurationChange,
    .AbortDeviceConfigurationChange = AbortDeviceConfigurationChange,
    .HasProperty = HasProperty,
    .IsPropertySettable = IsPropertySettable,
    .GetPropertyDataSize = GetPropertyDataSize,
    .GetPropertyData = GetPropertyData,
    .SetPropertyData = SetPropertyData,
    .StartIO = StartIO,
    .StopIO = StopIO,
    .GetZeroTimeStamp = GetZeroTimeStamp,
    .WillDoIOOperation = WillDoIOOperation,
    .BeginIOOperation = BeginIOOperation,
    .DoIOOperation = DoIOOperation,
    .EndIOOperation = EndIOOperation,
};
static AudioServerPlugInDriverInterface* gInterfacePtr = &gInterface;
static AudioServerPlugInDriverRef gDriverRef = &gInterfacePtr;

// The factory named in Info.plist (CFPlugInFactories).
void* DAWMultitoolAudio_Create(CFAllocatorRef allocator, CFUUIDRef requestedType);
void* DAWMultitoolAudio_Create(CFAllocatorRef allocator, CFUUIDRef requestedType) {
    (void)allocator;
    return CFEqual(requestedType, kAudioServerPlugInTypeUUID) ? gDriverRef : NULL;
}

static HRESULT QueryInterface(void* inDriver, REFIID inUUID, LPVOID* outInterface) {
    if (inDriver != gDriverRef || outInterface == NULL) return kAudioHardwareBadObjectError;
    CFUUIDRef requested = CFUUIDCreateFromUUIDBytes(NULL, inUUID);
    HRESULT result = E_NOINTERFACE;
    if (CFEqual(requested, IUnknownUUID) || CFEqual(requested, kAudioServerPlugInDriverInterfaceUUID)) {
        atomic_fetch_add(&gRefCount, 1);
        *outInterface = gDriverRef;
        result = S_OK;
    }
    CFRelease(requested);
    return result;
}

static ULONG AddRef(void* inDriver) {
    return inDriver == gDriverRef ? atomic_fetch_add(&gRefCount, 1) + 1 : 0;
}

static ULONG Release(void* inDriver) {
    if (inDriver != gDriverRef) return 0;
    unsigned current = atomic_load(&gRefCount);
    while (current > 0 && !atomic_compare_exchange_weak(&gRefCount, &current, current - 1)) {}
    return current > 0 ? current - 1 : 0;
}

// ---------------------------------------------------------------------------------------------- basic operations
static OSStatus Initialize(AudioServerPlugInDriverRef inDriver, AudioServerPlugInHostRef inHost) {
    if (inDriver != gDriverRef) return kAudioHardwareBadObjectError;
    gHost = inHost;
    mach_timebase_info_data_t timebase;
    mach_timebase_info(&timebase);
    gHostTicksPerSecond = 1.0e9 * (Float64)timebase.denom / (Float64)timebase.numer;
    CFPropertyListRef stored = NULL;
    if (gHost && gHost->CopyFromStorage(gHost, CFSTR("sample rate"), &stored) == 0 && stored) {
        Float64 rate = 0;
        if (CFGetTypeID(stored) == CFNumberGetTypeID() && CFNumberGetValue(stored, kCFNumberFloat64Type, &rate) && isSupportedRate(rate))
            gSampleRate = rate;  // the rate the DAW last used
        CFRelease(stored);
    }
    return 0;
}

static OSStatus CreateDevice(AudioServerPlugInDriverRef d, CFDictionaryRef desc, const AudioServerPlugInClientInfo* c, AudioObjectID* out) {
    (void)d; (void)desc; (void)c; (void)out;
    return kAudioHardwareUnsupportedOperationError;
}

static OSStatus DestroyDevice(AudioServerPlugInDriverRef d, AudioObjectID device) {
    (void)d; (void)device;
    return kAudioHardwareUnsupportedOperationError;
}

static OSStatus AddDeviceClient(AudioServerPlugInDriverRef d, AudioObjectID device, const AudioServerPlugInClientInfo* c) {
    (void)c;
    return (d == gDriverRef && device == kObjectID_Device) ? 0 : kAudioHardwareBadObjectError;
}

static OSStatus RemoveDeviceClient(AudioServerPlugInDriverRef d, AudioObjectID device, const AudioServerPlugInClientInfo* c) {
    (void)c;
    return (d == gDriverRef && device == kObjectID_Device) ? 0 : kAudioHardwareBadObjectError;
}

static OSStatus PerformDeviceConfigurationChange(AudioServerPlugInDriverRef d, AudioObjectID device, UInt64 action, void* info) {
    (void)action; (void)info;
    if (d != gDriverRef || device != kObjectID_Device) return kAudioHardwareBadObjectError;
    pthread_mutex_lock(&gStateMutex);
    if (gPendingSampleRate > 0 && isSupportedRate(gPendingSampleRate)) {
        gSampleRate = gPendingSampleRate;
        storeSampleRate(gSampleRate);
    }
    gPendingSampleRate = 0;
    pthread_mutex_unlock(&gStateMutex);
    return 0;
}

static OSStatus AbortDeviceConfigurationChange(AudioServerPlugInDriverRef d, AudioObjectID device, UInt64 action, void* info) {
    (void)action; (void)info;
    if (d != gDriverRef || device != kObjectID_Device) return kAudioHardwareBadObjectError;
    pthread_mutex_lock(&gStateMutex);
    gPendingSampleRate = 0;
    pthread_mutex_unlock(&gStateMutex);
    return 0;
}

// ---------------------------------------------------------------------------------------------- properties
static Boolean isStream(AudioObjectID object) {
    return object == kObjectID_Stream_Input || object == kObjectID_Stream_Output;
}

static Boolean HasProperty(AudioServerPlugInDriverRef d, AudioObjectID object, pid_t pid, const AudioObjectPropertyAddress* a) {
    (void)pid;
    if (d != gDriverRef || a == NULL) return false;
    switch (object) {
        case kObjectID_PlugIn:
            switch (a->mSelector) {
                case kAudioObjectPropertyBaseClass: case kAudioObjectPropertyClass: case kAudioObjectPropertyOwner:
                case kAudioObjectPropertyManufacturer: case kAudioObjectPropertyOwnedObjects: case kAudioPlugInPropertyDeviceList:
                case kAudioPlugInPropertyTranslateUIDToDevice: case kAudioPlugInPropertyResourceBundle:
                    return true;
            }
            return false;
        case kObjectID_Device:
            switch (a->mSelector) {
                case kAudioObjectPropertyBaseClass: case kAudioObjectPropertyClass: case kAudioObjectPropertyOwner:
                case kAudioObjectPropertyName: case kAudioObjectPropertyManufacturer: case kAudioObjectPropertyOwnedObjects:
                case kAudioDevicePropertyDeviceUID: case kAudioDevicePropertyModelUID: case kAudioDevicePropertyTransportType:
                case kAudioDevicePropertyRelatedDevices: case kAudioDevicePropertyClockDomain: case kAudioDevicePropertyDeviceIsAlive:
                case kAudioDevicePropertyDeviceIsRunning: case kAudioDevicePropertyDeviceCanBeDefaultDevice:
                case kAudioDevicePropertyDeviceCanBeDefaultSystemDevice: case kAudioDevicePropertyLatency:
                case kAudioDevicePropertyStreams: case kAudioObjectPropertyControlList: case kAudioDevicePropertySafetyOffset:
                case kAudioDevicePropertyNominalSampleRate: case kAudioDevicePropertyAvailableNominalSampleRates:
                case kAudioDevicePropertyIsHidden: case kAudioDevicePropertyPreferredChannelsForStereo:
                case kAudioDevicePropertyPreferredChannelLayout: case kAudioDevicePropertyZeroTimeStampPeriod:
                case kAudioDevicePropertyClockAlgorithm: case kAudioDevicePropertyClockIsStable:
                case kAudioObjectPropertyCustomPropertyInfoList: case kProperty_TargetSpeed: case kProperty_CurrentSpeed:
                    return true;
            }
            return false;
        case kObjectID_Stream_Input:
        case kObjectID_Stream_Output:
            switch (a->mSelector) {
                case kAudioObjectPropertyBaseClass: case kAudioObjectPropertyClass: case kAudioObjectPropertyOwner:
                case kAudioObjectPropertyName: case kAudioObjectPropertyOwnedObjects: case kAudioStreamPropertyIsActive:
                case kAudioStreamPropertyDirection: case kAudioStreamPropertyTerminalType: case kAudioStreamPropertyStartingChannel:
                case kAudioStreamPropertyLatency: case kAudioStreamPropertyVirtualFormat: case kAudioStreamPropertyPhysicalFormat:
                case kAudioStreamPropertyAvailableVirtualFormats: case kAudioStreamPropertyAvailablePhysicalFormats:
                    return true;
            }
            return false;
    }
    return false;
}

static OSStatus IsPropertySettable(AudioServerPlugInDriverRef d, AudioObjectID object, pid_t pid, const AudioObjectPropertyAddress* a, Boolean* out) {
    if (!HasProperty(d, object, pid, a) || out == NULL) return kAudioHardwareUnknownPropertyError;
    *out = false;
    if (object == kObjectID_Device)
        *out = a->mSelector == kAudioDevicePropertyNominalSampleRate || a->mSelector == kProperty_TargetSpeed;
    else if (isStream(object))
        *out = a->mSelector == kAudioStreamPropertyIsActive || a->mSelector == kAudioStreamPropertyVirtualFormat ||
               a->mSelector == kAudioStreamPropertyPhysicalFormat;
    return 0;
}

static UInt32 deviceStreams(AudioObjectPropertyScope scope, AudioObjectID* ids) {
    UInt32 n = 0;
    if (scope == kAudioObjectPropertyScopeGlobal || scope == kAudioObjectPropertyScopeInput) ids[n++] = kObjectID_Stream_Input;
    if (scope == kAudioObjectPropertyScopeGlobal || scope == kAudioObjectPropertyScopeOutput) ids[n++] = kObjectID_Stream_Output;
    return n;
}

static OSStatus GetPropertyDataSize(AudioServerPlugInDriverRef d, AudioObjectID object, pid_t pid, const AudioObjectPropertyAddress* a,
                                    UInt32 qSize, const void* q, UInt32* outSize) {
    (void)qSize; (void)q;
    if (!HasProperty(d, object, pid, a) || outSize == NULL) return kAudioHardwareUnknownPropertyError;
    AudioObjectID ids[2];
    switch (a->mSelector) {
        case kAudioObjectPropertyBaseClass: case kAudioObjectPropertyClass: *outSize = sizeof(AudioClassID); return 0;
        case kAudioObjectPropertyOwner: *outSize = sizeof(AudioObjectID); return 0;
        case kAudioObjectPropertyName: case kAudioObjectPropertyManufacturer: case kAudioDevicePropertyDeviceUID:
        case kAudioDevicePropertyModelUID: case kAudioPlugInPropertyResourceBundle:
            *outSize = sizeof(CFStringRef); return 0;
        case kProperty_TargetSpeed: case kProperty_CurrentSpeed: *outSize = sizeof(CFPropertyListRef); return 0;
        case kAudioObjectPropertyCustomPropertyInfoList: *outSize = 2 * sizeof(AudioServerPlugInCustomPropertyInfo); return 0;
        case kAudioObjectPropertyOwnedObjects:
            if (object == kObjectID_PlugIn) *outSize = sizeof(AudioObjectID);
            else if (object == kObjectID_Device) *outSize = deviceStreams(a->mScope, ids) * sizeof(AudioObjectID);
            else *outSize = 0;
            return 0;
        case kAudioPlugInPropertyDeviceList: case kAudioPlugInPropertyTranslateUIDToDevice: case kAudioDevicePropertyRelatedDevices:
            *outSize = sizeof(AudioObjectID); return 0;
        case kAudioDevicePropertyStreams: *outSize = deviceStreams(a->mScope, ids) * sizeof(AudioObjectID); return 0;
        case kAudioObjectPropertyControlList: *outSize = 0; return 0;
        case kAudioDevicePropertyNominalSampleRate: *outSize = sizeof(Float64); return 0;
        case kAudioDevicePropertyAvailableNominalSampleRates: *outSize = kSampleRateCount * sizeof(AudioValueRange); return 0;
        case kAudioDevicePropertyPreferredChannelsForStereo: *outSize = 2 * sizeof(UInt32); return 0;
        case kAudioDevicePropertyPreferredChannelLayout:
            *outSize = offsetof(AudioChannelLayout, mChannelDescriptions) + kChannels * sizeof(AudioChannelDescription); return 0;
        case kAudioStreamPropertyVirtualFormat: case kAudioStreamPropertyPhysicalFormat:
            *outSize = sizeof(AudioStreamBasicDescription); return 0;
        case kAudioStreamPropertyAvailableVirtualFormats: case kAudioStreamPropertyAvailablePhysicalFormats:
            *outSize = kSampleRateCount * sizeof(AudioStreamRangedDescription); return 0;
        default: *outSize = sizeof(UInt32); return 0;  // every other property is a UInt32
    }
}

static OSStatus putUInt32(UInt32 value, UInt32 inSize, UInt32* outSize, void* outData) {
    if (inSize < sizeof(UInt32)) return kAudioHardwareBadPropertySizeError;
    *(UInt32*)outData = value;
    *outSize = sizeof(UInt32);
    return 0;
}

static OSStatus putString(const char* value, UInt32 inSize, UInt32* outSize, void* outData) {
    if (inSize < sizeof(CFStringRef)) return kAudioHardwareBadPropertySizeError;
    *(CFStringRef*)outData = CFStringCreateWithCString(NULL, value, kCFStringEncodingUTF8);  // the caller releases it
    *outSize = sizeof(CFStringRef);
    return 0;
}

static OSStatus putNumber(double value, UInt32 inSize, UInt32* outSize, void* outData) {
    if (inSize < sizeof(CFPropertyListRef)) return kAudioHardwareBadPropertySizeError;
    *(CFPropertyListRef*)outData = CFNumberCreate(NULL, kCFNumberDoubleType, &value);  // the caller releases it
    *outSize = sizeof(CFPropertyListRef);
    return 0;
}

static OSStatus GetPropertyData(AudioServerPlugInDriverRef d, AudioObjectID object, pid_t pid, const AudioObjectPropertyAddress* a,
                                UInt32 qSize, const void* q, UInt32 inSize, UInt32* outSize, void* outData) {
    if (!HasProperty(d, object, pid, a) || outSize == NULL || outData == NULL) return kAudioHardwareUnknownPropertyError;
    AudioObjectID ids[2];
    UInt32 n, count;
    Float64 rate;
    pthread_mutex_lock(&gStateMutex);
    rate = gSampleRate;
    pthread_mutex_unlock(&gStateMutex);

    if (object == kObjectID_PlugIn) {
        switch (a->mSelector) {
            case kAudioObjectPropertyBaseClass: return putUInt32(kAudioObjectClassID, inSize, outSize, outData);
            case kAudioObjectPropertyClass: return putUInt32(kAudioPlugInClassID, inSize, outSize, outData);
            case kAudioObjectPropertyOwner: return putUInt32(kAudioObjectUnknown, inSize, outSize, outData);
            case kAudioObjectPropertyManufacturer: return putString(kManufacturer, inSize, outSize, outData);
            case kAudioObjectPropertyOwnedObjects: case kAudioPlugInPropertyDeviceList:
                if (inSize < sizeof(AudioObjectID)) { *outSize = 0; return 0; }
                return putUInt32(kObjectID_Device, inSize, outSize, outData);
            case kAudioPlugInPropertyTranslateUIDToDevice: {
                AudioObjectID found = kAudioObjectUnknown;
                if (qSize == sizeof(CFStringRef) && q != NULL) {
                    CFStringRef uid = *(const CFStringRef*)q;
                    if (uid && CFStringCompare(uid, CFSTR(kDeviceUID), 0) == kCFCompareEqualTo) found = kObjectID_Device;
                }
                return putUInt32(found, inSize, outSize, outData);
            }
            case kAudioPlugInPropertyResourceBundle: return putString("", inSize, outSize, outData);
        }
    } else if (object == kObjectID_Device) {
        switch (a->mSelector) {
            case kAudioObjectPropertyBaseClass: return putUInt32(kAudioObjectClassID, inSize, outSize, outData);
            case kAudioObjectPropertyClass: return putUInt32(kAudioDeviceClassID, inSize, outSize, outData);
            case kAudioObjectPropertyOwner: return putUInt32(kObjectID_PlugIn, inSize, outSize, outData);
            case kAudioObjectPropertyName: return putString(kDeviceName, inSize, outSize, outData);
            case kAudioObjectPropertyManufacturer: return putString(kManufacturer, inSize, outSize, outData);
            case kAudioDevicePropertyDeviceUID: return putString(kDeviceUID, inSize, outSize, outData);
            case kAudioDevicePropertyModelUID: return putString(kModelUID, inSize, outSize, outData);
            case kAudioDevicePropertyTransportType: return putUInt32(kAudioDeviceTransportTypeVirtual, inSize, outSize, outData);
            case kAudioDevicePropertyRelatedDevices: return putUInt32(kObjectID_Device, inSize, outSize, outData);
            case kAudioDevicePropertyClockDomain: return putUInt32(0, inSize, outSize, outData);
            case kAudioDevicePropertyDeviceIsAlive: return putUInt32(1, inSize, outSize, outData);
            case kAudioDevicePropertyDeviceIsRunning: {
                pthread_mutex_lock(&gStateMutex);
                UInt32 running = gIOCount > 0;
                pthread_mutex_unlock(&gStateMutex);
                return putUInt32(running, inSize, outSize, outData);
            }
            // never the Mac's default output: only apps that choose it (a DAW, DAW Multitool) use it
            case kAudioDevicePropertyDeviceCanBeDefaultDevice: return putUInt32(0, inSize, outSize, outData);
            case kAudioDevicePropertyDeviceCanBeDefaultSystemDevice: return putUInt32(0, inSize, outSize, outData);
            case kAudioDevicePropertyLatency: return putUInt32(0, inSize, outSize, outData);
            case kAudioDevicePropertySafetyOffset: return putUInt32(0, inSize, outSize, outData);
            case kAudioDevicePropertyIsHidden: return putUInt32(0, inSize, outSize, outData);
            case kAudioDevicePropertyZeroTimeStampPeriod: return putUInt32(kPeriodFrames, inSize, outSize, outData);
            // raw: Core Audio follows our timestamps directly instead of smoothing them (smoothing lags speed changes)
            case kAudioDevicePropertyClockAlgorithm: return putUInt32(kAudioDeviceClockAlgorithmRaw, inSize, outSize, outData);
            case kAudioDevicePropertyClockIsStable: return putUInt32(0, inSize, outSize, outData);
            case kAudioObjectPropertyOwnedObjects: case kAudioDevicePropertyStreams:
                n = deviceStreams(a->mScope, ids);
                count = inSize / sizeof(AudioObjectID);
                if (count > n) count = n;
                memcpy(outData, ids, count * sizeof(AudioObjectID));
                *outSize = count * sizeof(AudioObjectID);
                return 0;
            case kAudioObjectPropertyControlList: *outSize = 0; return 0;
            case kAudioDevicePropertyNominalSampleRate:
                if (inSize < sizeof(Float64)) return kAudioHardwareBadPropertySizeError;
                *(Float64*)outData = rate;
                *outSize = sizeof(Float64);
                return 0;
            case kAudioDevicePropertyAvailableNominalSampleRates: {
                count = inSize / sizeof(AudioValueRange);
                if (count > kSampleRateCount) count = kSampleRateCount;
                AudioValueRange* ranges = outData;
                for (UInt32 i = 0; i < count; i++) ranges[i] = (AudioValueRange){kSampleRates[i], kSampleRates[i]};
                *outSize = count * sizeof(AudioValueRange);
                return 0;
            }
            case kAudioDevicePropertyPreferredChannelsForStereo:
                if (inSize < 2 * sizeof(UInt32)) return kAudioHardwareBadPropertySizeError;
                ((UInt32*)outData)[0] = 1;
                ((UInt32*)outData)[1] = 2;
                *outSize = 2 * sizeof(UInt32);
                return 0;
            case kAudioDevicePropertyPreferredChannelLayout: {
                UInt32 size = offsetof(AudioChannelLayout, mChannelDescriptions) + kChannels * sizeof(AudioChannelDescription);
                if (inSize < size) return kAudioHardwareBadPropertySizeError;
                AudioChannelLayout* layout = outData;
                memset(layout, 0, size);
                layout->mChannelLayoutTag = kAudioChannelLayoutTag_UseChannelDescriptions;
                layout->mNumberChannelDescriptions = kChannels;
                layout->mChannelDescriptions[0].mChannelLabel = kAudioChannelLabel_Left;
                layout->mChannelDescriptions[1].mChannelLabel = kAudioChannelLabel_Right;
                *outSize = size;
                return 0;
            }
            case kAudioObjectPropertyCustomPropertyInfoList: {
                count = inSize / sizeof(AudioServerPlugInCustomPropertyInfo);
                if (count > 2) count = 2;
                AudioServerPlugInCustomPropertyInfo infos[2] = {
                    {kProperty_TargetSpeed, kAudioServerPlugInCustomPropertyDataTypeCFPropertyList, kAudioServerPlugInCustomPropertyDataTypeNone},
                    {kProperty_CurrentSpeed, kAudioServerPlugInCustomPropertyDataTypeCFPropertyList, kAudioServerPlugInCustomPropertyDataTypeNone},
                };
                memcpy(outData, infos, count * sizeof(AudioServerPlugInCustomPropertyInfo));
                *outSize = count * sizeof(AudioServerPlugInCustomPropertyInfo);
                return 0;
            }
            case kProperty_TargetSpeed: return putNumber(atomic_load(&gTargetSpeed), inSize, outSize, outData);
            case kProperty_CurrentSpeed: return putNumber(atomic_load(&gReportedSpeed), inSize, outSize, outData);
        }
    } else if (isStream(object)) {
        Boolean input = object == kObjectID_Stream_Input;
        switch (a->mSelector) {
            case kAudioObjectPropertyBaseClass: return putUInt32(kAudioObjectClassID, inSize, outSize, outData);
            case kAudioObjectPropertyClass: return putUInt32(kAudioStreamClassID, inSize, outSize, outData);
            case kAudioObjectPropertyOwner: return putUInt32(kObjectID_Device, inSize, outSize, outData);
            case kAudioObjectPropertyName: return putString(input ? "DAW Multitool In" : "DAW Multitool Out", inSize, outSize, outData);
            case kAudioObjectPropertyOwnedObjects: *outSize = 0; return 0;
            case kAudioStreamPropertyIsActive: {
                pthread_mutex_lock(&gStateMutex);
                UInt32 active = input ? gInputActive : gOutputActive;
                pthread_mutex_unlock(&gStateMutex);
                return putUInt32(active, inSize, outSize, outData);
            }
            case kAudioStreamPropertyDirection: return putUInt32(input ? 1 : 0, inSize, outSize, outData);
            case kAudioStreamPropertyTerminalType:
                return putUInt32(input ? kAudioStreamTerminalTypeLine : kAudioStreamTerminalTypeLine, inSize, outSize, outData);
            case kAudioStreamPropertyStartingChannel: return putUInt32(1, inSize, outSize, outData);
            case kAudioStreamPropertyLatency: return putUInt32(0, inSize, outSize, outData);
            case kAudioStreamPropertyVirtualFormat: case kAudioStreamPropertyPhysicalFormat:
                if (inSize < sizeof(AudioStreamBasicDescription)) return kAudioHardwareBadPropertySizeError;
                *(AudioStreamBasicDescription*)outData = streamFormat(rate);
                *outSize = sizeof(AudioStreamBasicDescription);
                return 0;
            case kAudioStreamPropertyAvailableVirtualFormats: case kAudioStreamPropertyAvailablePhysicalFormats: {
                count = inSize / sizeof(AudioStreamRangedDescription);
                if (count > kSampleRateCount) count = kSampleRateCount;
                AudioStreamRangedDescription* formats = outData;
                for (UInt32 i = 0; i < count; i++) {
                    formats[i].mFormat = streamFormat(kSampleRates[i]);
                    formats[i].mSampleRateRange = (AudioValueRange){kSampleRates[i], kSampleRates[i]};
                }
                *outSize = count * sizeof(AudioStreamRangedDescription);
                return 0;
            }
        }
    }
    return kAudioHardwareUnknownPropertyError;
}

static OSStatus requestRate(Float64 rate) {
    if (!isSupportedRate(rate)) return kAudioDeviceUnsupportedFormatError;
    pthread_mutex_lock(&gStateMutex);
    Boolean change = rate != gSampleRate;
    if (change) gPendingSampleRate = rate;
    pthread_mutex_unlock(&gStateMutex);
    if (change && gHost) gHost->RequestDeviceConfigurationChange(gHost, kObjectID_Device, 0, NULL);
    return 0;
}

static OSStatus SetPropertyData(AudioServerPlugInDriverRef d, AudioObjectID object, pid_t pid, const AudioObjectPropertyAddress* a,
                                UInt32 qSize, const void* q, UInt32 inSize, const void* inData) {
    (void)qSize; (void)q;
    Boolean settable = false;
    if (IsPropertySettable(d, object, pid, a, &settable) != 0 || !settable) return kAudioHardwareUnknownPropertyError;
    if (inData == NULL) return kAudioHardwareIllegalOperationError;
    if (object == kObjectID_Device && a->mSelector == kAudioDevicePropertyNominalSampleRate) {
        if (inSize < sizeof(Float64)) return kAudioHardwareBadPropertySizeError;
        return requestRate(*(const Float64*)inData);
    }
    if (object == kObjectID_Device && a->mSelector == kProperty_TargetSpeed) {
        if (inSize < sizeof(CFPropertyListRef)) return kAudioHardwareBadPropertySizeError;
        CFPropertyListRef value = *(const CFPropertyListRef*)inData;
        double speed = 1.0;
        if (value == NULL || CFGetTypeID(value) != CFNumberGetTypeID() || !CFNumberGetValue(value, kCFNumberDoubleType, &speed))
            return kAudioHardwareIllegalOperationError;
        atomic_store(&gTargetSpeed, clampSpeed(speed));
        notify(kObjectID_Device, kProperty_TargetSpeed);
        return 0;
    }
    if (isStream(object) && a->mSelector == kAudioStreamPropertyIsActive) {
        if (inSize < sizeof(UInt32)) return kAudioHardwareBadPropertySizeError;
        pthread_mutex_lock(&gStateMutex);
        if (object == kObjectID_Stream_Input) gInputActive = *(const UInt32*)inData != 0;
        else gOutputActive = *(const UInt32*)inData != 0;
        pthread_mutex_unlock(&gStateMutex);
        return 0;
    }
    if (isStream(object)) {  // a stream format: only the sample rate may differ
        if (inSize < sizeof(AudioStreamBasicDescription)) return kAudioHardwareBadPropertySizeError;
        const AudioStreamBasicDescription* f = inData;
        AudioStreamBasicDescription ours = streamFormat(f->mSampleRate);
        if (f->mFormatID != ours.mFormatID || f->mChannelsPerFrame != kChannels || f->mBitsPerChannel != 32)
            return kAudioDeviceUnsupportedFormatError;
        return requestRate(f->mSampleRate);
    }
    return kAudioHardwareUnknownPropertyError;
}

// ---------------------------------------------------------------------------------------------- IO
static OSStatus StartIO(AudioServerPlugInDriverRef d, AudioObjectID device, UInt32 client) {
    (void)client;
    if (d != gDriverRef || device != kObjectID_Device) return kAudioHardwareBadObjectError;
    pthread_mutex_lock(&gStateMutex);
    Boolean first = gIOCount == 0;
    gIOCount++;
    pthread_mutex_unlock(&gStateMutex);
    if (first) {
        pthread_mutex_lock(&gClockMutex);
        gAnchorHostTime = (Float64)mach_absolute_time();
        gPeriodCount = 0;
        gSpeed = clampSpeed(atomic_load(&gTargetSpeed));  // starting: no glide needed
        atomic_store(&gReportedSpeed, gSpeed);
        memset(gRing, 0, sizeof(gRing));
        gWriteEnd = 0;
        pthread_mutex_unlock(&gClockMutex);
        notify(kObjectID_Device, kAudioDevicePropertyDeviceIsRunning);
    }
    return 0;
}

static OSStatus StopIO(AudioServerPlugInDriverRef d, AudioObjectID device, UInt32 client) {
    (void)client;
    if (d != gDriverRef || device != kObjectID_Device) return kAudioHardwareBadObjectError;
    pthread_mutex_lock(&gStateMutex);
    if (gIOCount > 0) gIOCount--;
    Boolean last = gIOCount == 0;
    pthread_mutex_unlock(&gStateMutex);
    if (last) notify(kObjectID_Device, kAudioDevicePropertyDeviceIsRunning);
    return 0;
}

// The varispeed clock: each period of kPeriodFrames lasts (kPeriodFrames / rate) / speed seconds of real time, so at
// half speed the device (and the DAW) runs at half the rate. The speed moves toward its target once per period.
static OSStatus GetZeroTimeStamp(AudioServerPlugInDriverRef d, AudioObjectID device, UInt32 client, Float64* outSampleTime,
                                 UInt64* outHostTime, UInt64* outSeed) {
    (void)client;
    if (d != gDriverRef || device != kObjectID_Device) return kAudioHardwareBadObjectError;
    pthread_mutex_lock(&gStateMutex);
    Float64 rate = gSampleRate;
    pthread_mutex_unlock(&gStateMutex);
    Float64 now = (Float64)mach_absolute_time();
    Float64 target = clampSpeed(atomic_load(&gTargetSpeed));
    pthread_mutex_lock(&gClockMutex);
    Float64 ticksPerFrame = gHostTicksPerSecond / rate;
    for (int guard = 0; guard < 4096; guard++) {
        Float64 next = gAnchorHostTime + ticksPerFrame * kPeriodFrames / gSpeed;
        if (now < next) break;
        gAnchorHostTime = next;
        gPeriodCount++;
        if (gSpeed != target) gSpeed = stepSpeed(gSpeed, target);
    }
    *outSampleTime = (Float64)(gPeriodCount * kPeriodFrames);
    *outHostTime = (UInt64)gAnchorHostTime;
    *outSeed = 1;
    atomic_store(&gReportedSpeed, gSpeed);
    pthread_mutex_unlock(&gClockMutex);
    return 0;
}

static OSStatus WillDoIOOperation(AudioServerPlugInDriverRef d, AudioObjectID device, UInt32 client, UInt32 op, Boolean* outWillDo,
                                  Boolean* outInPlace) {
    (void)client;
    if (d != gDriverRef || device != kObjectID_Device) return kAudioHardwareBadObjectError;
    Boolean will = op == kAudioServerPlugInIOOperationReadInput || op == kAudioServerPlugInIOOperationWriteMix;
    if (outWillDo) *outWillDo = will;
    if (outInPlace) *outInPlace = true;
    return 0;
}

static OSStatus BeginIOOperation(AudioServerPlugInDriverRef d, AudioObjectID device, UInt32 client, UInt32 op, UInt32 frames,
                                 const AudioServerPlugInIOCycleInfo* cycle) {
    (void)client; (void)op; (void)frames; (void)cycle;
    return (d == gDriverRef && device == kObjectID_Device) ? 0 : kAudioHardwareBadObjectError;
}

static OSStatus EndIOOperation(AudioServerPlugInDriverRef d, AudioObjectID device, UInt32 client, UInt32 op, UInt32 frames,
                               const AudioServerPlugInIOCycleInfo* cycle) {
    (void)client; (void)op; (void)frames; (void)cycle;
    return (d == gDriverRef && device == kObjectID_Device) ? 0 : kAudioHardwareBadObjectError;
}

// Copies `frames` frames between the ring (starting at sample time `time`, wrapping round) and `audio`;
// a NULL `audio` clears that part of the ring.
static void ringCopy(SInt64 time, UInt32 frames, float* audio, Boolean intoRing) {
    UInt64 start = (UInt64)(((time % kRingFrames) + kRingFrames) % kRingFrames);
    UInt32 first = frames < kRingFrames - start ? frames : (UInt32)(kRingFrames - start);
    UInt32 parts[2][2] = {{(UInt32)start, first}, {0, frames - first}};
    for (int p = 0, done = 0; p < 2; done += parts[p][1], p++) {
        float* ring = gRing + parts[p][0] * kChannels;
        size_t bytes = parts[p][1] * kChannels * sizeof(float);
        if (audio == NULL) memset(ring, 0, bytes);
        else if (intoRing) memcpy(ring, audio + done * kChannels, bytes);
        else memcpy(audio + done * kChannels, ring, bytes);
    }
}

// Loopback: the mix the DAW plays is written into the ring at its sample time; reading the input at the same sample
// time gives it back. Only what was written in the last lap of the ring is returned: anything not written yet, or
// older (a stopped DAW), reads as silence rather than a repeating loop. Both happen on the device's IO thread.
static OSStatus DoIOOperation(AudioServerPlugInDriverRef d, AudioObjectID device, AudioObjectID stream, UInt32 client, UInt32 op,
                              UInt32 frames, const AudioServerPlugInIOCycleInfo* cycle, void* main, void* secondary) {
    (void)client; (void)secondary;
    if (d != gDriverRef || device != kObjectID_Device) return kAudioHardwareBadObjectError;
    if (main == NULL || cycle == NULL || frames > kRingFrames) return 0;
    float* buffer = main;
    if (op == kAudioServerPlugInIOOperationWriteMix && stream == kObjectID_Stream_Output) {
        SInt64 start = (SInt64)cycle->mOutputTime.mSampleTime;
        if (start > gWriteEnd)  // a gap since the last write: clear it so it can't read as old audio
            ringCopy(gWriteEnd, (UInt32)(start - gWriteEnd < kRingFrames ? start - gWriteEnd : kRingFrames), NULL, true);
        ringCopy(start, frames, buffer, true);
        if (start + frames > gWriteEnd) gWriteEnd = start + frames;
    } else if (op == kAudioServerPlugInIOOperationReadInput && stream == kObjectID_Stream_Input) {
        SInt64 start = (SInt64)cycle->mInputTime.mSampleTime, end = start + frames;
        SInt64 from = start > gWriteEnd - kRingFrames ? start : gWriteEnd - kRingFrames;
        SInt64 to = end < gWriteEnd ? end : gWriteEnd;
        memset(buffer, 0, frames * kChannels * sizeof(float));
        if (to > from) ringCopy(from, (UInt32)(to - from), buffer + (from - start) * kChannels, false);
    }
    return 0;
}
